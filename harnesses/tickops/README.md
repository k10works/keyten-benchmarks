# Tick-ops suite

Eight queries over one synthetic market day -- trades and quotes across a
symbol universe, generated locally and deterministically -- exercising
keyten's Tier-1 lazy surface: bar aggregation with an in-agg VWAP,
row-count and duration-window rolling stats, an EWM stat, `join_asof`
with `strategy`/`tolerance`, and a windowed `rank()`.

## Dataset

`gen_data.py` writes `trades.parquet` (sym, ts, price, size) and
`quotes.parquet` (sym, ts, bid, ask, bsize, asize) to an output directory,
both sorted by `(sym, ts)`. The published suite is 20,000,000 trades and
60,000,000 quotes across 2,000 symbols, one trading day (09:30-16:00).
Generation is deterministic and seeded (`--seed`, default 42): per symbol,
trade prices and quote midpoints each follow an independent geometric
random walk from a per-symbol base price; row counts across symbols follow
a log-normal activity split via a multinomial draw so a handful of
symbols trade far more than the rest.

`--scale F` shrinks trades/quotes/syms together for a fast, still
deterministic run (e.g. `--scale 0.005` for ~100k trades); explicit
`--trades`/`--quotes`/`--syms` override it. This is a reproducibility
knob, not an engine tuning flag -- every engine sees the identical
generated Parquet.

## Queries

| idx | name | what |
| --- | --- | --- |
| 1 | `bars_1m` | Per-sym 1-minute OHLCV + VWAP over trades. |
| 2 | `log_returns` | Per-sym log return per trade (`diff(log(price))`). |
| 3 | `roll_vol_rows` | Per-sym `rolling_std` over 20 trades of the log return. |
| 4 | `roll_vol_5m` | Per-sym duration-window (5 minute) rolling std of the log return. |
| 5 | `ewm_vol` | Per-sym `ewm_std(span=20)` of the log return. |
| 6 | `asof_nbbo` | Trades asof-joined to the latest quote per sym, backward. |
| 7 | `asof_tol_5s` | Same, with a 5-second staleness tolerance (unmatched -> null). |
| 8 | `xsec_rank` | Per-minute cross-sectional rank of symbols by traded value; top-decile count per minute. |

`bars_1m` (keyten) uses `group_by(['sym', col('ts').truncate('1m')])`
rather than `group_by_bar` -- `group_by_bar` buckets the *whole* frame by
time with no additional grouping key, so it collapses across symbols; it
doesn't fit a per-symbol bar (verified: there is no `by`-key parameter on
its public signature). VWAP is computed in-agg
(`(price*size).sum() / size.sum()`), not as a follow-up `with_columns`.

Polars' idiomatic per-symbol time bucketing is `group_by_dynamic`, not
`group_by` + a truncated key -- `bars_1m` and `xsec_rank` use it
(`group_by_dynamic(col('ts').alias('minute'), every='1m', group_by='sym')`).
Its defaults (`closed="left"`, `label="left"`, `start_by="window"`)
floor-bucket identically to keyten's `truncate("1m")`, verified
boundary-for-boundary against a `group_by` + `truncate` formulation before
switching (same row counts and bucket labels).

## Engines

- `queries_keyten.py` -- keyten's public 0.1.47 lazy API.
- `queries_duckdb.py` -- plain SQL over an in-memory `read_parquet` table.
- `queries_polars.py` -- polars' public lazy API.

Every `load()` materializes both tables into memory exactly once
(`kt.DataFrame.read_parquet` + `.lazy()`, `CREATE TABLE ... AS SELECT *
FROM read_parquet(...)`, `pl.read_parquet(...).lazy()` respectively,
matching the TAQ harness's own in-memory precedent) -- a lazy scan or a
DuckDB `VIEW` would silently re-decode the Parquet files on every single
query call, understating every engine's real per-query cost by re-billing
IO into it. Loading happens once outside every timed region; only the
query itself is timed.

**Honest gap:** DuckDB SQL has no public grouped
exponentially-weighted-moving-stddev function, so `ewm_vol` (query 5) is
not implemented for duckdb -- no UDF workaround, per the board's rules.
It's recorded as a gap in the checksum output and excluded from the
duckdb result file and its `total_ms`.

**Tolerance edge case:** DuckDB's window `RANGE BETWEEN INTERVAL 5 MINUTE
PRECEDING AND CURRENT ROW` is closed on both ends; keyten's
`rolling_std_by` window is `(t - window, t]`, open on the left. The two
disagree only when a row sits at *exactly* 5 minutes before another --
a measure-zero event against the generator's continuous, nanosecond-
resolution timestamps, so it does not show up in practice. `asof_tol_5s`
similarly applies DuckDB's tolerance as a post-`ASOF JOIN` `CASE` filter,
since DuckDB's `ASOF JOIN` only accepts one inequality -- still plain SQL,
no UDF.

## Correctness

`harness.py run --engine <e>` executes all 8 queries for one engine. For
each, before any timing: it calls the query once, sorts the *full* result
on its natural key (`(sym, ts)` for the per-trade queries, `(sym,
minute)` for `bars_1m`, `minute` for `xsec_rank`), and writes it to
`<out-dir>/<e>.q<idx>.parquet`. It then writes `<out-dir>/<e>.csv`
(idx,name,query,ms best-of-3) and a small `<out-dir>/<e>.checksum.json`
(row count + column names, for `check` to know what's on disk).

`harness.py check` reads every engine's per-query Parquet file and
compares them pairwise **column by column, over every row** -- not just
an aggregate, which would miss a value landing in the wrong group or row
while the sum still comes out right. Rows are matched positionally after
both sides are sorted on the same key (via PyArrow's `sort_by`, then
compared with `pyarrow.compute` -- no Python row loop). Non-floating
columns (including the sort key itself, and null positions on every
column) must match **exactly**; floating columns are compared with
`abs(a - b) <= 1e-6 + 1e-4 * max(abs(a), abs(b))` (looser than a typical
tolerance because float summation/accumulation order -- and therefore the
last few bits of a large aggregate -- is not reproducible across engines;
see keyten's own `Expr.sum()` docs). A query missing from an engine (a
documented gap) is skipped, not treated as a mismatch. Any real mismatch
prints the offending query and its mismatched columns, and exits
non-zero -- correctness fails loudly, before any number is trusted enough
to time.

Row order within a tied key (e.g. two trades at the exact same `(sym,
ts)`) is not otherwise constrained, so a tie could in principle sort
differently across engines; with the generator's continuous,
nanosecond-resolution timestamps this is a measure-zero event that does
not show up in practice.

## Timing

Only each engine's own natural materialization call is timed, and nothing
else -- no Arrow conversion happens inside the timed region for any
engine. For keyten and polars that's `.collect()`, called directly on the
query's lazy plan. DuckDB's query relations are themselves lazy, so its
query functions call `.to_arrow_table()` as part of building the result
(not as a separate step after it) -- forcing a DuckDB relation to Arrow
*is* its execution, playing the same role `.collect()` plays for the
other two; timing it is timing the query, not an extra conversion tacked
on afterwards. The one-off Arrow conversion used for the correctness
Parquet dump happens from a *separate*, untimed call to the query.

## Thread parity

`harness.py run` takes `--threads N`; keyten and duckdb use it directly
(`keyten.set_workers(N)`, `SET threads = N`) -- no environment variable
needed. Polars is the one engine that reads `POLARS_MAX_THREADS` from the
process environment at *import* time, so it can't be set through an
API call after the fact; `run_tickops.sh` sets it in the environment
before starting polars' subprocess, same as `run_taq.sh` does for its
engines.

## Running directly

```bash
python3 gen_data.py /tmp/tickops-tiny --scale 0.005
python3 harness.py run --engine keyten --data-dir /tmp/tickops-tiny --out-dir /tmp/tickops-out --threads 4
python3 harness.py run --engine duckdb --data-dir /tmp/tickops-tiny --out-dir /tmp/tickops-out --threads 4
python3 harness.py run --engine polars --data-dir /tmp/tickops-tiny --out-dir /tmp/tickops-out --threads 4
python3 harness.py check --out-dir /tmp/tickops-out
```

Or end to end, including the venv and board JSON conversion, via
`../../runner/run_tickops.sh`.
