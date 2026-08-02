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

Every suite runs from this repository alone: the query harnesses (with their
Keyten engine additions) are vendored under `harnesses/`, and each runner
creates a virtual environment, installs `keyten`, `duckdb`, and `polars` from
PyPI, runs all three engines in one sitting, and writes results in the
board's format to `results/<suite>/`. Open `board/index.html` (or
`python3 -m http.server` and browse to `/board/`) to see your numbers
rendered exactly like the published ones.

If `python3 -m venv` on your machine creates environments without `pip`
(some distributions), pre-create them with `uv venv --seed .work/venv` and
`uv venv --seed .work/pdsh/.venv` first. Right after a keyten release, pin
the exact version (`pip install keyten==X.Y.Z`) — a stale package index can
silently install the previous one.

```bash
git clone https://github.com/k10works/keyten-benchmarks
cd keyten-benchmarks
```

**TAQ** — the dataset derives from the public TAQ sample day; generate the
Parquet layout with `harnesses/taq/generateDB.sh` (see `harnesses/taq/
README.md`), then point the runner at the rowgroup directory:

```bash
./runner/run_taq.sh <data>/small/parquet/rowgroup
```

**PDS-H (SF10)** — table generation is driven by the vendored harness's
Makefile (TPC-H derived; see `harnesses/pdsh/README.md`):

```bash
./runner/run_pdsh.sh 10.0
```

**ClickBench (10M)** — derive the subset from the public ClickBench hits
file, then run:

```bash
duckdb -c "COPY (FROM read_parquet('hits.parquet') LIMIT 10000000)
           TO 'hits10m.parquet'"
./runner/run_clickbench.sh hits10m.parquet
```

## Suites

| Suite | Runner | What it measures |
| --- | --- | --- |
| TAQ (in-memory) | `runner/run_taq.sh` | Market-data analytics: filters, per-symbol windows, asof joins, OHLC bars over one trading day |
| ClickBench 10M | `runner/run_clickbench.sh` | The standard 43 web-analytics queries over a 10M-row subset of the public hits dataset (full-scale upstream submission planned) |
| PDS-H SF10 | `runner/run_pdsh.sh` | 22 decision-support queries derived from TPC-H, via the public polars-benchmark harness (results not comparable to official TPC results) |

## Contributing

Result submissions from other machines and harness improvements are welcome —
open a PR with your `results/*.json` and machine details. Code contributions
are accepted under the same
[Contributor Assignment Agreement](https://github.com/k10works/keyten/blob/main/CLA.md)
as the engine.

## License

MIT — copyright Rayforce Technologies Inc.
