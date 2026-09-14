#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Convert PDS-H timings.csv or a ClickBench board transcript into the
board's result schema."""

import csv
import json
import math
import statistics
import sys
from pathlib import Path


def machine(path):
    return json.load(open(path))


def percentile(values, fraction):
    """Linearly interpolated percentile over a non-empty numeric sequence."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_samples(samples_ms):
    """Return the release statistic and dispersion without dropping raw data."""
    if not samples_ms:
        raise ValueError("cannot summarize an empty sample set")
    median = statistics.median(samples_ms)
    deviations = [abs(value - median) for value in samples_ms]
    return {
        "median_ms": round(median, 6),
        "mad_ms": round(statistics.median(deviations), 6),
        "p25_ms": round(percentile(samples_ms, 0.25), 6),
        "p75_ms": round(percentile(samples_ms, 0.75), 6),
        "min_ms": round(min(samples_ms), 6),
        "max_ms": round(max(samples_ms), 6),
        "samples_ms": [round(value, 6) for value in samples_ms],
    }


def pdsh(timings_csv, engine, out, mach, version, metadata=None):
    """``version``, when given, pins which rows count: only rows for
    ``engine`` whose own recorded ``version`` column matches are used for
    the per-query sample set. This matters because the upstream harness
    (queries/common_utils.py:log_query_timing) *appends* to timings.csv
    rather than overwriting it, so a manual partial rerun (e.g. only
    re-timing keyten after a version fix, without re-running the whole
    suite) leaves older rows in the file. Without this filter, the summary
    silently blends timings from two different engine versions -- this is
    exactly what the 0.1.49 sitting hit and had to hand-purge (see
    official-sitting-report.md, issue 1). If ``version`` is not given, all
    versions found for ``engine`` in the file must agree, or this refuses
    to guess and errors out loudly instead of blending them.
    """
    with open(timings_csv) as handle:
        rows = [row for row in csv.DictReader(handle) if row["solution"] == engine]
    if not rows:
        raise SystemExit(f"convert_generic pdsh: no rows for engine {engine!r} in {timings_csv}")

    if version:
        rows = [row for row in rows if row["version"] == version]
        if not rows:
            raise SystemExit(
                f"convert_generic pdsh: engine {engine!r} has rows in {timings_csv} "
                f"but none at requested version {version!r} -- refusing to blend other versions"
            )
    else:
        seen_versions = {row["version"] for row in rows}
        if len(seen_versions) > 1:
            raise SystemExit(
                f"convert_generic pdsh: {timings_csv} has mixed versions for engine "
                f"{engine!r} ({sorted(seen_versions)}) and no --version was given to "
                f"disambiguate -- refusing to silently blend them. Pass the version "
                f"explicitly, or clear/regenerate timings.csv before rerunning."
            )
        version = seen_versions.pop()

    samples = {}
    for row in rows:
        qn = int(row["query_number"])
        ms = float(row["duration[s]"]) * 1000
        samples.setdefault(qn, []).append({
            "run_id": row.get("benchmark_run_id", ""),
            "order_position": int(row.get("order_position") or -1),
            "execution_mode": row.get("execution_mode", ""),
            "warmup_iterations": int(row.get("warmup_iterations") or 0),
            "ms": ms,
        })

    methodology = (metadata or {}).get("methodology", {})
    expected_samples = methodology.get("timed_samples_per_query")
    if expected_samples is not None:
        wrong = {qn: len(values) for qn, values in samples.items() if len(values) != expected_samples}
        if wrong:
            raise SystemExit(
                f"convert_generic pdsh: expected {expected_samples} samples per query, got {wrong}"
            )
        expected_runs = {str(run) for run in range(expected_samples)}
        wrong_runs = {
            qn: sorted(sample["run_id"] for sample in values)
            for qn, values in samples.items()
            if {sample["run_id"] for sample in values} != expected_runs
        }
        if wrong_runs:
            raise SystemExit(
                f"convert_generic pdsh: missing or duplicate benchmark rounds: {wrong_runs}"
            )
    expected_queries = methodology.get("expected_query_count")
    if expected_queries is not None and len(samples) != expected_queries:
        raise SystemExit(
            f"convert_generic pdsh: expected {expected_queries} queries, got {len(samples)}"
        )

    queries = []
    expected_mode = (metadata or {}).get("engine_modes", {}).get(engine)
    expected_warmups = methodology.get("warmups_per_timed_sample")
    for qn, raw_samples in sorted(samples.items()):
        if expected_mode and any(
            sample["execution_mode"] != expected_mode for sample in raw_samples
        ):
            raise SystemExit(
                f"convert_generic pdsh: query {qn} has an execution mode other than {expected_mode!r}"
            )
        if expected_warmups is not None and any(
            sample["warmup_iterations"] != expected_warmups for sample in raw_samples
        ):
            raise SystemExit(
                f"convert_generic pdsh: query {qn} does not have {expected_warmups} warmups per sample"
            )
        positions = {sample["order_position"] for sample in raw_samples}
        if expected_samples is not None and expected_samples >= 3 and positions != {1, 2, 3}:
            raise SystemExit(
                f"convert_generic pdsh: query {qn} did not run in all engine-order positions: {positions}"
            )
        values = [sample["ms"] for sample in raw_samples]
        summary = summarize_samples(values)
        summary["samples"] = [
            {**sample, "ms": round(sample["ms"], 6)} for sample in raw_samples
        ]
        queries.append({
            "idx": qn,
            "query": f"PDS-H query {qn} (TPC-H derived)",
            "ms": round(summary["median_ms"], 2),
            "stats": summary,
        })
    dump(engine, version, "pdsh-sf10", queries, out, mach, metadata)


def clickbench(transcript, engine, version, out, mach, sqlfile, metadata=None):
    sql = [line.strip() for line in Path(sqlfile).read_text().splitlines() if line.strip()] if sqlfile else []
    samples = {}
    for line in Path(transcript).read_text().splitlines():
        if not line.startswith("q"):
            continue
        fields = line.split()
        try:
            idx = int(fields[0][1:]) + 1
            ms = float(fields[1].removesuffix("ms"))
            attrs = dict(field.split("=", 1) for field in fields[2:])
            sample = {
                "ms": ms,
                "run_id": attrs.get("run_id", ""),
                "order_position": int(attrs.get("order_position", "-1")),
                "warmup_iterations": int(attrs.get("warmup_iterations", "0")),
                "execution_mode": (metadata or {}).get("engine_modes", {}).get(engine, ""),
            }
        except (ValueError, IndexError) as error:
            raise SystemExit(f"convert_generic clickbench: invalid sample: {line}") from error
        if idx < 1 or not math.isfinite(ms) or ms < 0:
            raise SystemExit(f"convert_generic clickbench: invalid timing: {line}")
        samples.setdefault(idx, []).append(sample)
    if not samples:
        raise SystemExit("convert_generic clickbench: no timed samples")

    methodology = (metadata or {}).get("methodology", {})
    expected_samples = methodology.get("timed_samples_per_query")
    expected_warmups = methodology.get("warmups_per_timed_sample")
    expected_queries = methodology.get("expected_query_count")
    if expected_queries is not None and set(samples) != set(range(1, expected_queries + 1)):
        raise SystemExit(f"convert_generic clickbench: expected queries 1..{expected_queries}, got {sorted(samples)}")
    queries = []
    for idx, raw in sorted(samples.items()):
        if expected_samples is not None:
            if len(raw) != expected_samples:
                raise SystemExit(f"convert_generic clickbench: query {idx} expected {expected_samples} samples, got {len(raw)}")
            if {sample["run_id"] for sample in raw} != {str(run) for run in range(expected_samples)}:
                raise SystemExit(f"convert_generic clickbench: query {idx} missing or duplicate rounds")
            if expected_samples >= 3 and {sample["order_position"] for sample in raw} != {1, 2, 3}:
                raise SystemExit(f"convert_generic clickbench: query {idx} did not run in all engine-order positions")
        if expected_warmups is not None and any(sample["warmup_iterations"] != expected_warmups for sample in raw):
            raise SystemExit(f"convert_generic clickbench: query {idx} does not have {expected_warmups} warmups per sample")
        summary = summarize_samples([sample["ms"] for sample in raw])
        summary["samples"] = [{**sample, "ms": round(sample["ms"], 6)} for sample in raw]
        queries.append({
            "idx": idx,
            "query": sql[idx - 1][:160] if idx <= len(sql) else "",
            "ms": round(summary["median_ms"], 2),
            "stats": summary,
        })
    dump(engine, version, "clickbench-10m", queries, out, mach, metadata)


def dump(engine, version, suite, queries, out, mach, metadata=None):
    doc = {
        "suite": suite,
        "engine": engine,
        "version": version,
        "threads": (metadata or {}).get("workers", mach["cores"]),
        "machine": mach,
        "queries": queries,
        "total_ms": round(sum(q["ms"] for q in queries), 1),
    }
    if metadata:
        doc["benchmark"] = metadata
        engine_modes = metadata.get("engine_modes", {})
        if engine in engine_modes:
            doc["execution_mode"] = engine_modes[engine]
    with open(out, "w") as handle:
        json.dump(doc, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(out, doc["total_ms"], "ms over", len(queries), "queries")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "pdsh":
        if len(sys.argv) not in (7, 8):
            raise SystemExit(
                "usage: convert_generic.py pdsh TIMINGS ENGINE VERSION MACHINE OUT [METADATA]"
            )
        _, _, timings, engine, version, machine_json, out, *rest = sys.argv
        metadata = json.load(open(rest[0])) if rest else None
        pdsh(timings, engine, out, machine(machine_json), version or None, metadata)
    else:
        if len(sys.argv) not in (8, 9):
            raise SystemExit(
                "usage: convert_generic.py clickbench TRANSCRIPT ENGINE VERSION MACHINE SQL OUT [METADATA]"
            )
        _, _, transcript, engine, version, machine_json, sqlfile, out, *rest = sys.argv
        metadata = json.load(open(rest[0])) if rest else None
        clickbench(transcript, engine, version, out, machine(machine_json), sqlfile, metadata)
