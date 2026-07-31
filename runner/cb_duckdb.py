#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""ClickBench queries in-process through duckdb over an in-memory table."""
import sys, time
import duckdb

con = duckdb.connect()
t0 = time.time()
# The canonical queries expect typed Date/DateTime columns; the shared
# parquet stores them as raw ints, so load through the typed casts.
con.execute(
    "CREATE TABLE hits AS SELECT * REPLACE ("
    " to_timestamp(EventTime)::TIMESTAMP AS EventTime,"
    " (DATE '1970-01-01' + EventDate * INTERVAL 1 DAY) AS EventDate)"
    f" FROM read_parquet('{sys.argv[1]}')"
)
print(f"# load {time.time()-t0:.1f}s")
queries = [l.strip() for l in open(sys.argv[2]) if l.strip()]
total = 0.0
for i, q in enumerate(queries):
    best = None
    for _ in range(3):
        t0 = time.time()
        try:
            con.execute(q).fetchall()
        except Exception as e:
            print(f"q{i:02d} ERROR {e}")
            best = float("nan"); break
        el = time.time() - t0
        best = el if best is None else min(best, el)
    total += best if best == best else 0.0
    print(f"q{i:02d} {best*1000:8.2f}ms")
print(f"TOTAL {total*1000:8.2f}ms  (sum of best-of-3 over {len(queries)} queries)")
