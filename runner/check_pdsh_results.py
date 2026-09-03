#!/usr/bin/env python3
"""Cross-check captured PDS-H results before a timed sitting."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import polars as pl


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _family(dtypes: list[pl.DataType]) -> str:
    if any(dtype.is_temporal() for dtype in dtypes):
        return "temporal"
    if any(dtype.is_float() or dtype.is_decimal() for dtype in dtypes):
        return "float"
    if any(dtype.is_integer() for dtype in dtypes):
        return "integer"
    if all(dtype == pl.Boolean for dtype in dtypes):
        return "boolean"
    return "string"


def _normalize(frames: dict[str, pl.DataFrame]) -> tuple[dict[str, pl.DataFrame], dict[str, str]]:
    names = list(frames)
    reference_columns = frames[names[0]].columns
    for engine, frame in frames.items():
        if frame.columns != reference_columns:
            raise AssertionError(
                f"column mismatch: {names[0]}={reference_columns}, {engine}={frame.columns}"
            )
        if frame.height != frames[names[0]].height:
            raise AssertionError(
                f"row-count mismatch: {names[0]}={frames[names[0]].height}, {engine}={frame.height}"
            )

    families = {}
    normalized = {engine: frame for engine, frame in frames.items()}
    for column in reference_columns:
        family = _family([frame.schema[column] for frame in frames.values()])
        families[column] = family
        target = {
            "temporal": pl.Int64,
            "float": pl.Float64,
            "integer": pl.Int64,
            "boolean": pl.Boolean,
            "string": pl.String,
        }[family]
        normalized = {
            engine: frame.with_columns(pl.col(column).cast(target, strict=True))
            for engine, frame in normalized.items()
        }

    sort_columns = [name for name in reference_columns if families[name] != "float"]
    if sort_columns:
        normalized = {
            engine: frame.sort(sort_columns, nulls_last=True)
            for engine, frame in normalized.items()
        }
    return normalized, families


def _float_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    if math.isnan(left) or math.isnan(right):
        return math.isnan(left) and math.isnan(right)
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


def _compare_frames(frames: dict[str, pl.DataFrame]) -> None:
    normalized, families = _normalize(frames)
    engines = list(normalized)
    reference_name = engines[0]
    reference = normalized[reference_name]
    for engine in engines[1:]:
        candidate = normalized[engine]
        for column in reference.columns:
            left = reference.get_column(column).to_list()
            right = candidate.get_column(column).to_list()
            if families[column] == "float":
                bad = [
                    row
                    for row, (a, b) in enumerate(zip(left, right, strict=True))
                    if not _float_equal(a, b)
                ]
                if bad:
                    row = bad[0]
                    raise AssertionError(
                        f"{engine} float mismatch at {column}[{row}]: "
                        f"{right[row]!r} != {reference_name} {left[row]!r}"
                    )
            elif left != right:
                row = next(i for i, pair in enumerate(zip(left, right, strict=True)) if pair[0] != pair[1])
                raise AssertionError(
                    f"{engine} value mismatch at {column}[{row}]: "
                    f"{right[row]!r} != {reference_name} {left[row]!r}"
                )


def compare_results(
    root: Path, engines: list[str], expected_queries: int = 22
) -> dict:
    report = {"status": "pass", "engines": engines, "queries": [], "errors": []}
    for query in range(1, expected_queries + 1):
        paths = {engine: root / engine / f"q{query}.parquet" for engine in engines}
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            report["errors"].append({"query": query, "error": f"missing results: {missing}"})
            continue
        frames = {engine: pl.read_parquet(path) for engine, path in paths.items()}
        entry = {
            "query": query,
            "rows": next(iter(frames.values())).height,
            "columns": next(iter(frames.values())).columns,
            "sha256": {engine: _sha256(path) for engine, path in paths.items()},
        }
        try:
            _compare_frames(frames)
        except AssertionError as error:
            entry["status"] = "fail"
            entry["error"] = str(error)
            report["errors"].append({"query": query, "error": str(error)})
        else:
            entry["status"] = "pass"
        report["queries"].append(entry)
    if report["errors"]:
        report["status"] = "fail"
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--engines", default="duckdb,polars,keyten")
    parser.add_argument("--expected-queries", type=int, default=22)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = compare_results(
        args.root, args.engines.split(","), args.expected_queries
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(
        f"PDS-H correctness: {report['status']} "
        f"({len(report['queries'])}/{args.expected_queries} queries captured)"
    )
    if report["status"] != "pass":
        for error in report["errors"]:
            print(f"q{error['query']}: {error['error']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
