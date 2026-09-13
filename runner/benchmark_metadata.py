#!/usr/bin/env python3
"""Write reproducibility metadata for a benchmark sitting."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from materialize import tree_digest
from native_layout import store_layout


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _cache_bytes(level: int) -> int | None:
    cache_root = Path("/sys/devices/system/cpu/cpu0/cache")
    for index in cache_root.glob("index*"):
        if _read(index / "level") != str(level):
            continue
        size = _read(index / "size")
        if not size:
            continue
        units = {"K": 1024, "M": 1024**2, "G": 1024**3}
        suffix = size[-1].upper()
        try:
            return int(size[:-1]) * units[suffix] if suffix in units else int(size)
        except ValueError:
            return None
    return None


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _governors() -> list[str]:
    values = {
        value
        for path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
        if (value := _read(path))
    }
    return sorted(values)


def machine_facts() -> dict:
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except AttributeError:
        affinity = list(range(os.cpu_count() or 1))
    page_size = os.sysconf("SC_PAGE_SIZE")
    pages = os.sysconf("SC_PHYS_PAGES")
    return {
        "cpu": _cpu_model(),
        "cores": os.cpu_count(),
        "affinity_cpus": affinity,
        "ram_bytes": page_size * pages,
        "os": platform.system(),
        "architecture": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "governors": _governors(),
        "l2_bytes": _cache_bytes(2),
        "l3_bytes": _cache_bytes(3),
    }


def _git_state(root: Path) -> dict:
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        text=True,
    ).splitlines()
    dirty_paths = [line[3:] for line in status]
    source_dirty = any(not path.startswith("results/") for path in dirty_paths)
    return {
        "sha": sha,
        "dirty": bool(dirty_paths),
        "source_dirty": source_dirty,
        "dirty_paths": dirty_paths,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _distribution_artifact(distribution: importlib.metadata.Distribution) -> dict:
    digest = hashlib.sha256()
    native = {}
    for relative in sorted(distribution.files or [], key=str):
        path = Path(distribution.locate_file(relative))
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        encoded = str(relative).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        file_hash = _file_sha256(path)
        digest.update(file_hash.encode())
        if path.suffix in {".so", ".dylib", ".pyd"}:
            native[str(relative)] = file_hash
    return {
        "version": distribution.version,
        "sha256": digest.hexdigest(),
        "native_artifacts": native,
    }


def _engine_artifact(name: str) -> dict:
    module = importlib.import_module(name)
    related = {}
    for distribution in importlib.metadata.distributions():
        distribution_name = distribution.metadata.get("Name", "").lower()
        if distribution_name == name or (
            name == "polars" and distribution_name.startswith("polars-runtime-")
        ):
            related[distribution.metadata["Name"]] = _distribution_artifact(distribution)
    return {
        "version": getattr(module, "__version__", "unknown"),
        "build": {
            "git_sha": getattr(module, "__git_sha__", "unknown"),
            "build_state": getattr(module, "__build_state__", "unknown"),
        },
        "module": str(Path(module.__file__).resolve()),
        "distributions": related,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--harness", required=True, type=Path)
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--machine-out", required=True, type=Path)
    parser.add_argument("--metadata-out", required=True, type=Path)
    parser.add_argument("--samples", required=True, type=int)
    parser.add_argument("--warmups", required=True, type=int)
    parser.add_argument("--workers", required=True, type=int)
    parser.add_argument("--suite", choices=("pdsh", "clickbench"), default="pdsh")
    parser.add_argument(
        "--benchmark-mode",
        required=True,
        choices=("resident-native", "end-to-end"),
    )
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument(
        "--native-store", action="append", default=[],
        help="native store directory whose elected encodings are recorded",
    )
    args = parser.parse_args()

    if args.samples < 1 or args.warmups < 0 or args.workers < 1:
        parser.error("samples/workers must be positive and warmups must be non-negative")

    machine = machine_facts()
    if args.benchmark_mode == "resident-native":
        engine_modes = {
            "keyten": "resident-native",
            "polars": "streaming-resident",
            "duckdb": "in-memory",
        }
        io_included = False
    else:
        engine_modes = {
            "keyten": "parquet-end-to-end",
            "polars": "streaming-parquet-end-to-end",
            "duckdb": "parquet-end-to-end",
        }
        io_included = True

    git_state = _git_state(args.repo.resolve())
    if args.require_clean and git_state["source_dirty"]:
        parser.error(
            "benchmark source is dirty; commit/stash source changes or omit --require-clean for development"
        )

    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_repo": git_state,
        "native_stores": [store_layout(path) for path in args.native_store],
        "artifacts": {
            "harness_sha256": tree_digest(args.harness.resolve()),
            "adapter_sha256": tree_digest(args.adapter.resolve()),
        },
        "methodology": {
            "statistic": "median",
            "dispersion": ["mad", "p25", "p75"],
            "timed_samples_per_query": args.samples,
            "warmups_per_timed_sample": args.warmups,
            "expected_query_count": 22,
            "engine_order": "cyclic rotations; each engine occupies every position",
            "raw_samples_retained": True,
            "io_included": io_included,
        },
        "workers": args.workers,
        "adapter_track": "standard",
        "benchmark_mode": args.benchmark_mode,
        "engine_modes": engine_modes,
        "engines": {name: _engine_artifact(name) for name in ("keyten", "polars", "duckdb")},
        "environment": sorted(
            f"{distribution.metadata.get('Name', 'unknown')}=={distribution.version}"
            for distribution in importlib.metadata.distributions()
        ),
    }

    if args.suite == "clickbench":
        # Describe the existing adapters: three timed attempts, no separate
        # untimed query warmup, only the minimum retained in the transcript.
        metadata["methodology"] = {
            "statistic": "min",
            "dispersion": [],
            "timed_samples_per_query": args.samples,
            "warmups_per_query": args.warmups,
            "expected_query_count": 43,
            "engine_order": "keyten, polars, duckdb",
            "raw_samples_retained": False,
            "io_included": {"keyten": True, "polars": True, "duckdb": False},
            "load_included": False,
        }
        metadata["benchmark_mode"] = "warm-native-scan"
        metadata["engine_modes"] = {
            "keyten": "native-scan",
            "polars": "parquet-scan",
            "duckdb": "in-memory",
        }

    args.machine_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.machine_out.write_text(json.dumps(machine, indent=1, sort_keys=True) + "\n")
    args.metadata_out.write_text(json.dumps(metadata, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
