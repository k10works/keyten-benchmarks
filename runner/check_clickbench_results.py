#!/usr/bin/env python3
"""Compare complete ClickBench result tables across standard adapters."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
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


def _rows(path: Path, query: int, *, ordered: bool = False) -> tuple[list[str], list[list[Any]]]:
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
    if not ordered:
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


# Zero-based adapter indexes. These GROUP BY queries permit different rows at
# tied LIMIT/OFFSET boundaries (Q18 has no ORDER BY). Verify actual output rows
# against the complete SQL relation, including all aggregates and rank windows.
# keys, descending score column (None for unordered), limit, offset
TIE_SPECS = {
    17: ((0, 1), None, 10, 0),
    21: ((0,), 2, 10, 0),
    27: ((0,), 1, 25, 0),
    28: ((0,), 1, 25, 0),
    31: ((0, 1), 2, 10, 0),
    32: ((0, 1), 2, 10, 0),
    38: ((0,), 1, 10, 1000),
    39: ((0, 1, 2, 3, 4), 5, 10, 1000),
    40: ((0, 1), 2, 10, 100),
}
NON_TOTAL_ORDER_QUERIES = frozenset(TIE_SPECS)


def _prepare_tie_oracle(connection, sql: str, query: int) -> dict:
    keys, score, limit, offset = TIE_SPECS[query]
    # Require the exact supported suffix; fail closed if query definitions change.
    suffix = (r"\s+LIMIT 10;?\s*$" if score is None else
              rf"\s+ORDER BY (?:c|l|PageViews) DESC LIMIT {limit}"
              + (rf" OFFSET {offset}" if offset else "") + r";?\s*$")
    body, replacements = re.subn(suffix, "", sql, flags=re.IGNORECASE)
    if replacements != 1:
        raise ValueError(f"Q{query + 1}: unsupported SQL order/limit suffix")
    width = len(connection.execute(f"SELECT * FROM ({body}) LIMIT 0").description)
    names = ", ".join(f"c{i}" for i in range(width))
    connection.execute(f"CREATE OR REPLACE TEMP TABLE cb_oracle AS "
                       f"SELECT * FROM ({body}) AS result({names})")
    # Q41's EventDate is stored as epoch days in Keyten, DATE in SQL/Polars.
    if query == 40:
        connection.execute("ALTER TABLE cb_oracle ALTER c1 TYPE BIGINT "
                           "USING date_diff('day', DATE '1970-01-01', c1)")
    total = connection.execute("SELECT count(*) FROM cb_oracle").fetchone()[0]
    scores = None if score is None else [row[0] for row in connection.execute(
        f"SELECT c{score} FROM cb_oracle ORDER BY c{score} DESC NULLS LAST "
        f"LIMIT {limit} OFFSET {offset}").fetchall()]
    return {"keys": keys, "score": score, "scores": scores,
            "rows": min(limit, max(0, total - offset)), "groups": total,
            "schema": connection.execute("SELECT * FROM cb_oracle LIMIT 0").to_arrow_table().schema}


def _validate_tied_result(connection, oracle: dict, result) -> None:
    import pyarrow as pa
    columns, rows = result
    if len(columns) != len(oracle["schema"]):
        raise AssertionError("column-count mismatch against complete SQL result")
    if len(rows) != oracle["rows"]:
        raise AssertionError("row-count mismatch against SQL LIMIT/OFFSET window")
    keys = oracle["keys"]
    actual_keys = [tuple(row[i] for i in keys) for row in rows]
    if len(set(actual_keys)) != len(actual_keys):
        raise AssertionError("duplicate group in output")
    if not rows:
        return
    # Only look up the small captured result; the oracle aggregates ALL input
    # rows first. Never restrict source rows to candidate keys before grouping.
    lookup = pa.table({f"c{i}": pa.array([r[i] for r in rows],
                                       type=oracle["schema"].field(i).type)
                       for i in keys})
    connection.register("cb_selected", lookup)
    predicate = " AND ".join(f"o.c{i} IS NOT DISTINCT FROM s.c{i}" for i in keys)
    expected_rows = connection.execute(
        f"SELECT o.* FROM cb_oracle o JOIN cb_selected s ON {predicate}").fetchall()
    expected = {tuple(row[i] for i in keys): row for row in expected_rows}
    for key, row in zip(actual_keys, rows, strict=True):
        if key not in expected:
            raise AssertionError(f"group not present in complete SQL result: {key!r}")
        for col, (actual, correct) in enumerate(zip(row, expected[key], strict=True)):
            equal = _value_equal(actual, correct) if isinstance(correct, float) else actual == correct
            if not equal:
                raise AssertionError(f"value mismatch for group {key!r}, column {col}: "
                                     f"actual={actual!r}, SQL={correct!r}")
    if oracle["score"] is not None:
        for rank, (row, expected_score) in enumerate(zip(rows, oracle["scores"], strict=True)):
            if not _value_equal(row[oracle["score"]], expected_score):
                raise AssertionError(f"rank-window mismatch at output row {rank}: "
                                     f"actual={row[oracle['score']]!r}, SQL={expected_score!r}")


def _compare(
    reference_name: str,
    reference: tuple[list[str], list[list[Any]]],
    candidate_name: str,
    candidate: tuple[list[str], list[list[Any]]],
    query: int | None = None,
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


def compare_results(root: Path, engines: list[str], expected_queries: int = 43,
                    *, hits: Path | None = None, sql_path: Path | None = None) -> dict:
    report: dict = {
        "status": "pass",
        "engines": engines,
        "tolerance": {"absolute": ATOL, "relative": RTOL},
        "queries": [],
        "errors": [],
    }
    connection = None
    sql_queries = []
    if hits is not None:
        import duckdb
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'UTC'")
        connection.read_parquet(str(hits)).create_view("cb_raw")
        connection.execute("CREATE VIEW hits AS SELECT * REPLACE ("
                           "to_timestamp(EventTime)::TIMESTAMP AS EventTime, "
                           "(DATE '1970-01-01' + EventDate * INTERVAL 1 DAY)::DATE AS EventDate) "
                           "FROM cb_raw")
        sql_path = sql_path or Path(__file__).resolve().parents[1] / "adapters/clickbench-duckdb-queries.sql"
        sql_queries = [line.strip() for line in sql_path.read_text().splitlines() if line.strip()]
        report["oracle"] = {"engine": "duckdb", "version": duckdb.__version__,
                            "input": str(hits.resolve()), "input_sha256": _sha256(hits),
                            "sql_sha256": _sha256(sql_path),
                            "method": "complete SQL groups, all cells, exact rank window; legitimate ties allowed"}
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
                results[engine] = _rows(path, query, ordered=query in NON_TOTAL_ORDER_QUERIES)
                entry["canonical_sha256"][engine] = _canonical_sha256(
                    results[engine][1]
                )
            except Exception as error:
                entry.setdefault("errors", []).append(
                    f"{engine} output error: {type(error).__name__}: {error}"
                )

        if len(results) == len(engines):
            candidate_name = engines[0]
            candidate = results[candidate_name]
            entry["rows"] = len(candidate[1])
            entry["columns"] = len(candidate[0])
            if query in NON_TOTAL_ORDER_QUERIES:
                entry["order"] = "complete SQL membership + values + rank window"
                try:
                    if connection is None:
                        raise AssertionError("full-data SQL oracle required; supply --hits (shape-only checks removed)")
                    oracle = _prepare_tie_oracle(connection, sql_queries[query], query)
                    entry["oracle_groups"] = oracle["groups"]
                    entry["oracle_agreement"] = {}
                    for engine in engines:
                        try:
                            _validate_tied_result(connection, oracle, results[engine])
                            entry["oracle_agreement"][engine] = "match"
                        except Exception as error:
                            entry["oracle_agreement"][engine] = str(error)
                            entry.setdefault("errors", []).append(f"{engine}: {error}")
                except Exception as error:
                    entry.setdefault("errors", []).append(f"oracle: {error}")
            else:
                per_ref = {}
                for engine in engines[1:]:
                    try:
                        _compare(engine, results[engine], candidate_name, candidate, query)
                        per_ref[engine] = "match"
                    except AssertionError as error:
                        per_ref[engine] = str(error)
                entry["reference_agreement"] = per_ref
                # Require agreement with BOTH references, not just one.
                if not per_ref or not all(v == "match" for v in per_ref.values()):
                    entry.setdefault("errors", []).append(
                        f"{candidate_name} does not match every reference: " + "; ".join(
                            f"{e}: {m}" for e, m in per_ref.items()))
        if entry.get("errors"):
            entry["status"] = "fail"
            report["errors"].append({"query": query + 1, "errors": entry["errors"]})
        report["queries"].append(entry)
    if report["errors"]:
        report["status"] = "fail"
    if connection is not None:
        connection.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--engines", default="keyten,duckdb,polars")
    parser.add_argument("--expected-queries", type=int, default=43)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--hits", type=Path, help="Original Parquet input for full SQL tie validation")
    parser.add_argument("--sql", type=Path, help="Canonical SQL query file")
    args = parser.parse_args()
    report = compare_results(args.root, args.engines.split(","), args.expected_queries,
                             hits=args.hits, sql_path=args.sql)
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
