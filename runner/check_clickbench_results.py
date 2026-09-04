#!/usr/bin/env python3
"""Compare complete ClickBench result tables across standard adapters."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


ATOL = 1e-6
RTOL = 1e-7
EPOCH_DATE = date(1970, 1, 1)
EPOCH_DATETIME = datetime(1970, 1, 1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporal_role(query: int, column: str, position: int) -> str | None:
    if query == 6:  # min/max EventDate
        return "days"
    if query == 23 and column == "EventDate":  # SELECT *
        return "days"
    if query == 23 and column == "EventTime":
        return "seconds"
    if query == 42 and position == 0:  # date_trunc('minute', EventTime)
        return "seconds"
    return None


def _canonical_cell(value: Any, role: str | None) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        epoch = EPOCH_DATETIME.replace(tzinfo=timezone.utc) if value.tzinfo else EPOCH_DATETIME
        return int((value - epoch).total_seconds())
    if isinstance(value, date):
        return (value - EPOCH_DATE).days
    if role in ("days", "seconds") and isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value


def _rows(path: Path, query: int) -> tuple[list[str], list[list[Any]]]:
    table = pq.read_table(path)
    columns = table.column_names
    data = table.to_pylist()
    rows = []
    for record in data:
        rows.append([
            _canonical_cell(
                record[column], _temporal_role(query, column, position)
            )
            for position, column in enumerate(columns)
        ])
    # SQL relations are unordered unless ORDER BY says otherwise. Sorting the
    # full row preserves multiplicity while avoiding adapter storage order as
    # an accidental correctness contract.
    rows.sort(key=lambda row: json.dumps(row, default=str, separators=(",", ":")))
    return columns, rows


def _canonical_sha256(rows: list[list[Any]]) -> str:
    payload = json.dumps(
        rows, ensure_ascii=False, allow_nan=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _value_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, float) and math.isnan(left):
            return isinstance(right, float) and math.isnan(right)
        if isinstance(right, float) and math.isnan(right):
            return False
        if isinstance(left, int) and isinstance(right, int):
            return left == right
        return math.isclose(float(left), float(right), rel_tol=RTOL, abs_tol=ATOL)
    return left == right


def _compare(
    reference_name: str,
    reference: tuple[list[str], list[list[Any]]],
    candidate_name: str,
    candidate: tuple[list[str], list[list[Any]]],
) -> None:
    reference_columns, reference_rows = reference
    candidate_columns, candidate_rows = candidate
    if len(reference_columns) != len(candidate_columns):
        raise AssertionError(
            f"column-count mismatch: {reference_name}={len(reference_columns)}, "
            f"{candidate_name}={len(candidate_columns)}"
        )
    if len(reference_rows) != len(candidate_rows):
        raise AssertionError(
            f"row-count mismatch: {reference_name}={len(reference_rows)}, "
            f"{candidate_name}={len(candidate_rows)}"
        )
    for row_index, (left_row, right_row) in enumerate(
        zip(reference_rows, candidate_rows, strict=True)
    ):
        for column_index, (left, right) in enumerate(
            zip(left_row, right_row, strict=True)
        ):
            if not _value_equal(left, right):
                raise AssertionError(
                    f"value mismatch at row {row_index}, column {column_index}: "
                    f"{candidate_name}={right!r}, {reference_name}={left!r}"
                )


def compare_results(root: Path, engines: list[str], expected_queries: int = 43) -> dict:
    report: dict = {
        "status": "pass",
        "engines": engines,
        "tolerance": {"absolute": ATOL, "relative": RTOL},
        "queries": [],
        "errors": [],
    }
    statuses = {}
    for engine in engines:
        path = root / f"{engine}-status.json"
        if not path.is_file():
            report["errors"].append({"query": None, "error": f"missing status report: {path}"})
            statuses[engine] = {}
        else:
            statuses[engine] = json.loads(path.read_text(encoding="utf-8")).get("queries", {})

    for query in range(expected_queries):
        entry: dict = {
            "query": query + 1,
            "adapter_index": query,
            "status": "pass",
            "sha256": {},
            "canonical_sha256": {},
        }
        results = {}
        for engine in engines:
            status = statuses.get(engine, {}).get(str(query), {})
            if status.get("status") != "success":
                message = f"{engine} execution status is {status.get('status', 'missing')}"
                if status.get("error"):
                    message += f": {status['error']}"
                entry.setdefault("errors", []).append(message)
                continue
            path = root / engine / f"q{query:02d}.parquet"
            if not path.is_file():
                entry.setdefault("errors", []).append(f"{engine} output is missing: {path}")
                continue
            entry["sha256"][engine] = _sha256(path)
            try:
                results[engine] = _rows(path, query)
                entry["canonical_sha256"][engine] = _canonical_sha256(
                    results[engine][1]
                )
            except Exception as error:
                entry.setdefault("errors", []).append(
                    f"{engine} output error: {type(error).__name__}: {error}"
                )

        if len(results) == len(engines):
            reference_name = engines[0]
            reference = results[reference_name]
            entry["rows"] = len(reference[1])
            entry["columns"] = len(reference[0])
            for engine in engines[1:]:
                try:
                    _compare(reference_name, reference, engine, results[engine])
                except AssertionError as error:
                    entry.setdefault("errors", []).append(str(error))
        if entry.get("errors"):
            entry["status"] = "fail"
            report["errors"].append({"query": query + 1, "errors": entry["errors"]})
        report["queries"].append(entry)
    if report["errors"]:
        report["status"] = "fail"
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--engines", default="keyten,duckdb,polars")
    parser.add_argument("--expected-queries", type=int, default=43)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = compare_results(args.root, args.engines.split(","), args.expected_queries)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for query in report["queries"]:
        print(f"ClickBench Q{query['query']:02d} {query['status'].upper()}")
        for error in query.get("errors", []):
            print(f"  {error}")
    print(f"ClickBench correctness: {report['status'].upper()}")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
