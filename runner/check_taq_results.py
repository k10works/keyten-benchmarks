#!/usr/bin/env python3
"""Fail-closed cross-engine comparison for captured TAQ query results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path


ABS_TOLERANCE = 5e-5
REL_TOLERANCE = 1e-7


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _query_indices(querymeta: Path) -> list[int]:
    with querymeta.open(newline="", encoding="utf-8") as handle:
        return [int(row["idx"]) for row in csv.DictReader(handle, delimiter="|")]


def _number(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _equal(left: str, right: str) -> bool:
    if left == right:
        return True
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return False
    if math.isnan(left_number) or math.isnan(right_number):
        return math.isnan(left_number) and math.isnan(right_number)
    return math.isclose(
        left_number, right_number,
        rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE,
    )


def _compare_files(
    reference_name: str,
    reference_path: Path,
    candidate_name: str,
    candidate_path: Path,
) -> None:
    sentinel = object()
    with reference_path.open(newline="", encoding="utf-8") as left_handle, \
         candidate_path.open(newline="", encoding="utf-8") as right_handle:
        left_reader = csv.reader(left_handle)
        right_reader = csv.reader(right_handle)
        try:
            reference_columns = next(left_reader)
            candidate_columns = next(right_reader)
        except StopIteration as error:
            raise AssertionError("empty file (CSV header is missing)") from error
        if len(reference_columns) != len(candidate_columns):
            raise AssertionError(
                f"column-count mismatch: {reference_name}={len(reference_columns)}, "
                f"{candidate_name}={len(candidate_columns)}"
            )
        if set(reference_columns) != set(candidate_columns):
            raise AssertionError(
                f"column-name mismatch: {reference_name}={reference_columns}, "
                f"{candidate_name}={candidate_columns}"
            )
        positions = [candidate_columns.index(column) for column in reference_columns]
        for row_index, pair in enumerate(
            itertools.zip_longest(left_reader, right_reader, fillvalue=sentinel)
        ):
            left_row, right_row = pair
            if left_row is sentinel or right_row is sentinel:
                shorter = reference_name if left_row is sentinel else candidate_name
                raise AssertionError(f"row-count mismatch: {shorter} ended at row {row_index}")
            if len(left_row) != len(reference_columns):
                raise AssertionError(
                    f"malformed {reference_name} row {row_index}: "
                    f"expected {len(reference_columns)} fields, got {len(left_row)}"
                )
            if len(right_row) != len(candidate_columns):
                raise AssertionError(
                    f"malformed {candidate_name} row {row_index}: "
                    f"expected {len(candidate_columns)} fields, got {len(right_row)}"
                )
            for column_index, right_index in enumerate(positions):
                left = left_row[column_index]
                right = right_row[right_index]
                if not _equal(left, right):
                    column = reference_columns[column_index]
                    raise AssertionError(
                        f"value mismatch at {column}[{row_index}]: "
                        f"{candidate_name}={right!r}, {reference_name}={left!r}"
                    )


def compare_results(
    root: Path,
    querymeta: Path,
    engines: list[str],
) -> dict:
    report: dict = {
        "status": "pass",
        "engines": engines,
        "tolerance": {"absolute": ABS_TOLERANCE, "relative": REL_TOLERANCE},
        "queries": [],
        "errors": [],
    }
    statuses = {}
    for engine in engines:
        status_path = root / f"{engine}-status.json"
        if not status_path.is_file():
            report["errors"].append(
                {"query": None, "engine": engine, "error": f"missing status report: {status_path}"}
            )
            statuses[engine] = {}
            continue
        statuses[engine] = json.loads(status_path.read_text(encoding="utf-8")).get("queries", {})

    for query in _query_indices(querymeta):
        entry: dict = {"query": query, "status": "pass", "sha256": {}}
        outputs: dict[str, Path] = {}
        for engine in engines:
            status = statuses.get(engine, {}).get(str(query), {})
            if status.get("status") != "success":
                message = f"{engine} execution status is {status.get('status', 'missing')}"
                if status.get("error"):
                    message += f": {status['error']}"
                entry.setdefault("errors", []).append(message)
                continue
            path = root / engine / f"queryoutput_{query}.csv"
            if not path.is_file():
                entry.setdefault("errors", []).append(f"{engine} output is missing: {path}")
                continue
            entry["sha256"][engine] = _sha256(path)
            outputs[engine] = path

        if len(outputs) == len(engines):
            reference_name = engines[0]
            entry["rows"] = statuses[reference_name][str(query)].get("rows")
            entry["columns"] = statuses[reference_name][str(query)].get("columns")
            for engine in engines[1:]:
                try:
                    _compare_files(
                        reference_name, outputs[reference_name], engine, outputs[engine]
                    )
                except (OSError, csv.Error, AssertionError) as error:
                    entry.setdefault("errors", []).append(str(error))

        if entry.get("errors"):
            entry["status"] = "fail"
            report["errors"].append({"query": query, "errors": entry["errors"]})
        report["queries"].append(entry)

    if report["errors"]:
        report["status"] = "fail"
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("querymeta", type=Path)
    parser.add_argument("--engines", default="keyten,duckdb,polars")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = compare_results(args.root, args.querymeta, args.engines.split(","))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for query in report["queries"]:
        print(f"TAQ Q{query['query']:02d} {query['status'].upper()}")
        for error in query.get("errors", []):
            print(f"  {error}")
    print(f"TAQ correctness: {report['status'].upper()}")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
