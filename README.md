# Keyten benchmarks

Reproducible, same-machine benchmark results for [Keyten](https://k10.works/)
against DuckDB and Polars — and the board that publishes them:
**[bench.k10.works](https://bench.k10.works/)**.

The rules, applied to every published number:

- **Same machine, same data, same queries, same thread count.** All engines run
  in one sitting on identical input; machine and engine versions are recorded
  in every result file.
- **PyPI releases, default settings.** Each engine is installed from PyPI and
  run through its public API — no source builds, no tuning flags.
- **Correctness before timing.** Query statuses and result sizes are checked
  across engines; a query an engine does not complete appears as a gap on the
  board and is excluded from the common-subset totals.
- **Best of three** runs per query, timed inside a shared harness.

## Reproduce

```bash
git clone https://github.com/k10works/keyten-benchmarks
cd keyten-benchmarks
./runner/run_taq.sh <path-to-taq-parquet-dataset>
```

The runner creates a virtual environment, installs `keyten`, `duckdb`, and
`polars` from PyPI, clones the public
[NYSETAQBenchmarks](https://github.com/singaraiona/NYSETAQBenchmarks) harness
at the pinned revision, runs all three engines, and writes results in the
board's format to `results/taq-small/`. Open `board/index.html` (or
`python3 -m http.server` and browse to `/board/`) to see your numbers rendered
exactly like the published ones. The dataset derives from the public TAQ
sample day — see the harness README for generating the Parquet layout.

## Suites

| Suite | Status | What it measures |
| --- | --- | --- |
| TAQ (in-memory) | published | Market-data analytics: filters, per-symbol windows, asof joins, OHLC bars over one trading day |
| ClickBench | planned | The standard web-analytics suite, via the upstream harness plus a `keyten` adapter (upstream submission planned) |
| PDS-H | planned | Decision-support queries derived from TPC-H, via the upstream harness |

## Contributing

Result submissions from other machines and harness improvements are welcome —
open a PR with your `results/*.json` and machine details. Code contributions
are accepted under the same
[Contributor Assignment Agreement](https://github.com/k10works/keyten/blob/main/CLA.md)
as the engine.

## License

MIT — copyright Rayforce Technologies Inc.
