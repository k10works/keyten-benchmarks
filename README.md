# Keyten benchmarks

Reproducible, same-machine benchmark results for [Keyten](https://k10.works/)
against DuckDB and Polars, with opt-in QuestDB and [l](https://lv1.sh/)
adapters for all four suites — and the board that publishes them:
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

TAQ correctness checks complete result rows before timing. Q32 permits only
output reordering within identical `(sym, time)` keys, matching its SQL
`ORDER BY`; a one-to-one comparison still checks every value and duplicate
count, including the moving VWAP attached to each row. Numeric tolerances
are unchanged. Reordered groups larger than 512 rows fail closed.

**PDS-H (SF10)** — table generation is driven by the vendored harness's
Makefile (TPC-H derived; see `harnesses/pdsh/README.md`):

```bash
./runner/run_pdsh.sh 10.0
```

**ClickBench (10M)** — derive the subset from the public ClickBench hits
file with a retained hash/schema manifest, then run:

```bash
python3 runner/derive_clickbench_10m.py hits.parquet hits10m.parquet
./runner/run_clickbench.sh hits10m.parquet
```

Set `BENCH_PORT` to use a different local daemon port (default `8000`).
The runner also accepts the legacy `BENCH_POLARS_PORT` setting and gives both
adapters the same port; conflicting values fail before setup. Daemon startup
has a 180-second deadline, configurable with `BENCH_STARTUP_TIMEOUT` (1–3600
seconds). A child that exits before readiness fails immediately. The runner
cleans up its own daemon on success or failure and refuses to use an existing
service answering `/health` on that port.

**Tick ops** — synthetic market data is generated deterministically on first
run; a full day or a quick smoke test:

```bash
./runner/run_tickops.sh .work/tickops
./runner/run_tickops.sh .work/tickops-tiny 4 -- --scale 0.005
```

## QuestDB and l

Both engines use their official Python SDKs and local server binaries. Run
one suite directly, including a fresh DuckDB correctness reference:

```bash
./runner/run_external.sh tickops .work/tickops-tiny --threads 4
```

The same command accepts `taq`, `clickbench`, and `pdsh`. To append the new
engines to an existing suite runner, set `BENCH_EXTRA_ENGINES=questdb,l`.
New runs have separate results and a board URL; published results are not
replaced. See [setup, commands, timing boundaries, and coverage](adapters/external/README.md).
The PyPI/default-settings rules above describe the existing embedded-engine
results; external runs record their server binaries and worker configuration.

## Suites

| Suite | Runner | What it measures |
| --- | --- | --- |
| TAQ (in-memory) | `runner/run_taq.sh` | Market-data analytics: filters, per-symbol windows, asof joins, OHLC bars over one trading day |
| ClickBench 10M | `runner/run_clickbench.sh` | The standard 43 web-analytics queries over a 10M-row subset of the public hits dataset (full-scale upstream submission planned) |
| PDS-H SF10 | `runner/run_pdsh.sh` | 22 decision-support queries derived from TPC-H, via the public polars-benchmark harness (results not comparable to official TPC results) |
| Tick ops | `runner/run_tickops.sh` | Eight queries over synthetic market data: OHLCV bars, returns, rolling volatility, exponential volatility, asof joins, cross-sectional ranks |

## Contributing

Result submissions from other machines and harness improvements are welcome —
open a PR with your `results/*.json` and machine details. Code contributions
are accepted under the same
[Contributor Assignment Agreement](https://github.com/k10works/keyten/blob/main/CLA.md)
as the engine.

## License

MIT — copyright Rayforce Technologies Inc.
