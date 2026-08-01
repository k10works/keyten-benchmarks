# ClickBench (reference)

The ClickBench 43-query suite needs no vendored harness: keyten-benchmarks
runs it self-contained (`runner/run_clickbench.sh`, `adapters/clickbench-*`).
The 10M-row dataset derives from the public ClickBench hits file:

    duckdb -c "COPY (FROM read_parquet('hits.parquet') LIMIT 10000000)
               TO 'hits10m.parquet'"

Upstream: https://github.com/ClickHouse/ClickBench (Apache-2.0).
