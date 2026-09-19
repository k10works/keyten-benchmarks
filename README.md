# Keyten benchmarks

Reproducible, same-machine benchmark results for [Keyten](https://k10.works/)
against DuckDB and Polars, with opt-in QuestDB and [l](https://lv1.sh/)
adapters for all four suites — and the board that publishes them:
**[bench.k10.works](https://bench.k10.works/)**.

The result files and suite notes record each sitting's provenance:

- **Same machine, data, queries, and thread count.** Fresh comparative runs use
  identical inputs. Keyten-only refreshes identify retained competitor captures
  and timings; a changed host condition requires a matched baseline refresh.
- **Identified engine builds.** Runners normally install PyPI packages. Development
  runs may supply a Keyten wheel; its binary hash and build identity are recorded.
  Optional features and non-default host conditions are disclosed in suite notes.
- **Correctness before timing.** Current runners validate complete query outputs.
  A query an engine does not complete appears as a gap and is excluded from the
  common-subset totals. Earlier sittings retain their recorded check coverage.
- **Recorded timing protocol.** Current PDS-H and ClickBench runs use medians of
  twelve fresh-process samples after two warmups. TAQ and Tick use best of three.
  Separate paired regression experiments are reported separately from board ranks.

## September 19 optional-JIT board

The current board measures a development wheel with `jit-dynasm` enabled under
polling idle (C1/C2 disabled for all engines during measurement, then restored).
A fresh pre-JIT all-engine baseline establishes the matched host condition;
the candidate refresh times only Keyten and retains those competitor timings.
This is a measured optional-feature experiment, not a default PyPI release claim.

- [Candidate board](https://bench.k10.works/board/)
- [Matched pre-JIT board](https://bench.k10.works/board/?results=../results/archive/pre-jit-polling-idle-20260919)
- [Previous host-condition board](https://bench.k10.works/board/?results=../results/archive/pre-jit-original-host-20260919)
- [Evidence and identities](results/jit-evidence-20260919.json)
- [Full audit, rejected gates, and remaining gaps](https://github.com/k10works/keyten/tree/main/audit/jit-dispatch-20260919)

The separate PDS-H gate passes both candidate comparisons and its identical-binary
control. All board suites pass full-output correctness. The board flags TAQ Q35
(+5.22%, +2.86ms); twelve alternating pairs of the full TAQ sequence pass the
unchanged >2ms **and** >5% per-query threshold, with Q35 +1.44%/+0.818ms.
Original-condition JIT rejections remain in the audit. Board totals improve
0.00–0.40% against the matched baseline; these small differences are not
statistical-significance claims. LLVM is not included.

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
