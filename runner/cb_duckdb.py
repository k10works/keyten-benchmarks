#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""ClickBench queries in-process through DuckDB over an in-memory table."""
import argparse
import json
import os
from pathlib import Path
import time

import duckdb
import pyarrow.parquet as pq


parser = argparse.ArgumentParser()
parser.add_argument("hits", type=Path)
parser.add_argument("queries", type=Path)
parser.add_argument("--capture-dir", type=Path)
args = parser.parse_args()

con = duckdb.connect()
# Raw EventTime values are UTC epoch seconds, matching the other adapters.
# Casting TIMESTAMPTZ to TIMESTAMP otherwise applies the host's local zone.
con.execute("SET TimeZone = 'UTC'")
t0 = time.time()
# The canonical queries expect typed Date/DateTime columns; the shared
# parquet stores them as raw ints, so load through the typed casts.
con.execute(
    "CREATE TABLE hits AS SELECT * REPLACE ("
    " to_timestamp(EventTime)::TIMESTAMP AS EventTime,"
    " (DATE '1970-01-01' + EventDate * INTERVAL 1 DAY)::DATE AS EventDate)"
    " FROM read_parquet(?)",
    [str(args.hits)],
)
print(f"# load {time.time()-t0:.1f}s")
queries = [line.strip() for line in args.queries.read_text().splitlines() if line.strip()]

if args.capture_dir is not None:
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    statuses = {}
    for i, query in enumerate(queries):
        try:
            # Fetching Arrow is the execution and retains every result cell.
            table = con.execute(query).to_arrow_table()
            pq.write_table(table, args.capture_dir / f"q{i:02d}.parquet")
            statuses[str(i)] = {
                "status": "success",
                "rows": table.num_rows,
                "columns": table.num_columns,
            }
            print(f"q{i:02d} PASS rows={table.num_rows}")
        except Exception as error:
            statuses[str(i)] = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
            print(f"q{i:02d} FAIL {statuses[str(i)]['error']}")
    (args.capture_dir.parent / "duckdb-status.json").write_text(
        json.dumps({"engine": "duckdb", "queries": statuses}, indent=2) + "\n",
        encoding="utf-8",
    )
    raise SystemExit(0)

warmups = int(os.environ.get("RUN_WARMUP_ITERATIONS", "2"))
if warmups < 0:
    raise SystemExit("warmups must be non-negative")
run_id = os.environ.get("RUN_BENCHMARK_RUN_ID", "0")
position = int(os.environ.get("RUN_ORDER_POSITION", "1"))
total = 0.0
for i, query in enumerate(queries):
    for _ in range(warmups):
        con.execute(query).to_arrow_table()
    t0 = time.perf_counter()
    con.execute(query).to_arrow_table()
    elapsed = time.perf_counter() - t0
    total += elapsed
    print(f"q{i:02d} {elapsed * 1000:.6f}ms run_id={run_id} order_position={position} warmup_iterations={warmups}")
print(f"TOTAL {total * 1000:.6f}ms (one timed sample per query)")
