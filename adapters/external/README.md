# QuestDB and l

All four suites have adapters in `runner/external/`, launched by
`runner/run_external.sh`. They use the official Python clients:
[QuestDB](https://questdb.com/docs/connect/clients/python/) and
[l](https://lv1.sh/docs/sdks/python/). The wrapper installs the pinned client
requirements into `.work/external-venv` on first use. The server binaries must
already be installed.

On this machine the defaults are `../l/l` and
`../questdb-10.0.1-rt-linux-x86-64`. Override them with `L_BIN` and
`QUESTDB_HOME`, or `--l-bin` and `--questdb-home`. QuestDB requires its bundled
Java runtime. Both servers are started for the run and stopped afterwards;
QuestDB's database and logs stay in the run directory. The l CLI binds its
IPC port using its native `-p` option; use a trusted benchmark machine.

```bash
./runner/run_external.sh tickops .work/tickops-tiny --threads 4
./runner/run_external.sh taq /data/small/parquet/rowgroup --threads 4
./runner/run_external.sh clickbench /data/hits10m.parquet --threads 4
./runner/run_external.sh pdsh /data/scale-10.0 --scale-factor 10 --threads 4
```

Use `--engines questdb` or `--engines l` for one engine,
`--correctness-only` to skip timing, and `--samples N --warmups N` to change
the sampling protocol. TAQ accepts `--date YYYY-MM-DD` and `--param-dir DIR`.
PDS-H input contains the eight `<table>.parquet` files; `--scale-factor`
controls Q11's scale-dependent threshold and must match the generated data.

The existing four runners also accept `BENCH_EXTRA_ENGINES=questdb,l`.
They run the additional engines against the same input in a separate result
sitting after their embedded-engine run. This also works with
`BENCH_CORRECTNESS_ONLY=true`. No existing published results are overwritten.

## Measurement and correctness

Each new run includes a fresh DuckDB reference. Full result tables, null
positions, duplicate multiplicity, integer identifiers, and nanosecond
timestamps are checked. Floating tolerances match the existing suite gates.
Any value mismatch stops the whole run from producing timing result files.
A query rejected by a runtime is retained as `execution_error`, with its SQL
or qSQL and the actual exception; it is excluded from timing. Explicit gaps
have `gap` status. A partially supported suite is reported as `partial`,
not as full coverage. No successfully checked queries is a failed run.

All engines finish the correctness phase before fresh external servers are
loaded for timing. Loading and correctness conversion are outside timing.
Timing includes SDK request/response and native result materialization:
QuestDB `query().to_arrow()`, l `query()` returning a native `K`, and DuckDB
execution to Arrow. l's Python/Arrow conversion is only for correctness.
These client timings are not interchangeable with the historical embedded
engine timings.

QuestDB loads Parquet into its native tables, with globally sorted designated
timestamps for time-series joins. It uses warm native storage, rather than
claiming an in-memory database. Its query pool uses `--threads`; network and
write pools each use one additional worker. l uses `--threads - 1` secondary
workers plus its main thread; DuckDB uses `SET threads`. The different storage
modes and SDK costs are recorded in the output metadata.

ClickBench's new comparison adds matching tie breakers for grouped `LIMIT`
queries to SQL and qSQL, including an order for Q18. This chooses a deterministic
member of the original query's allowed result sets so every cell can be checked.
The historical ClickBench query files are unchanged. `MIN(VARCHAR)` inputs are
cast to `STRING` in QuestDB: the installed 10.0.1 / Python 5.0.0 combination
otherwise returned corrupt strings in the SDK output during validation.

l reuses the vendored TAQ q queries, with sample standard deviation and Q28's
20-observation minimum made explicit to match this repository's SQL contract.
ClickBench and PDS-H use the qSQL catalogs here. QuestDB uses the vendored SQL
with native dialect translations and its own tick-ops ASOF/TOLERANCE queries.

Explicit gaps:

- Tick ops Q5: the DuckDB reference has no grouped EWM standard deviation.
- l tick ops Q4: local l 0.9752 does not implement `wj1`.
- l ClickBench Q29: no equivalent native regex query has been implemented.

QuestDB 10.0.1 also rejects the TAQ pivot queries (15–17) and exact median
queries (48–49); these remain explicit execution errors in the report.

Other unsupported constructs are attempted and reported with their actual
runtime errors. This allows newer runtime versions to gain coverage without
silently treating an adapter or engine failure as a successful benchmark.

## Results

Each invocation creates `.work/external/<suite>-<timestamp>/` (or a new path
specified by `--out-dir`), containing complete captures, `correctness.json`,
input and adapter hashes, server logs/configuration, and engine identities.
Successful timing runs also write board JSON and `index.json`. The command
prints a board URL; serve the repository with `python3 -m http.server` and open
that URL to view only the new sitting. Historical `results/index.json` is
unchanged until a full dataset sitting is reviewed for publication.

Small synthetic data and a 1,000-row-per-table PDS-H sample were used for
adapter smoke checks. Successful query counts on those fixtures:

| Suite | QuestDB 10.0.1 | l 0.9752 |
| --- | --- | --- |
| TAQ | 48 / 53 | 53 / 53 |
| ClickBench | 43 / 43 | 42 / 43 |
| PDS-H | 22 / 22 | 22 / 22 |
| Tick ops | 7 / 8 | 6 / 8 |

 These checks are not full-scale benchmark results.
Run the local SDK regression tests with:

```bash
BENCH_EXTERNAL_INTEGRATION=1 .work/external-venv/bin/python -m unittest discover -s runner -p test_external.py
```
