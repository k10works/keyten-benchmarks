# ClickBench (reference)

The ClickBench 43-query suite needs no vendored harness: keyten-benchmarks
runs it self-contained (`runner/run_clickbench.sh`, `adapters/clickbench-*`).
The 10M-row dataset derives from the public ClickBench hits file. Use the
checked-in derivation tool so the exact prefix, source/output SHA-256 hashes,
row count, and schema are retained in a manifest:

    curl -fLO https://datasets.clickhouse.com/hits_compatible/hits.parquet
    python3 runner/derive_clickbench_10m.py hits.parquet hits10m.parquet

The default is exactly 10,000,000 rows in Parquet physical order. For a
small execution smoke use `--rows N`; this exercises the same derivation
and full-result correctness surface without claiming coverage of the
10M data distribution.

Before timing, `runner/run_clickbench.sh` captures every cell of every
query result from Keyten, DuckDB, and Polars as Parquet. The fail-closed
checker records shapes and SHA-256 digests and compares complete result
multisets with `1e-6` absolute plus `1e-7` relative float tolerance.
Missing output, execution errors, and mismatches are per-query failures;
timing starts only after all 43 queries pass.

Upstream: https://github.com/ClickHouse/ClickBench (Apache-2.0).
