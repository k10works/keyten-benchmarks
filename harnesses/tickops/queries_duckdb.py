# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""The 8-query tick-ops suite over DuckDB's public SQL surface.

``ewm_vol`` (query 5) has no public grouped-EWM function in DuckDB SQL --
per the board's rules, that is recorded as an honest gap rather than
worked around with a UDF.

``asof_tol_5s`` (query 7) needs a tolerance on top of an ASOF JOIN;
DuckDB's ``ASOF JOIN`` only accepts a single inequality, so the tolerance
is applied as a post-join ``CASE`` filter over the matched row -- still
plain SQL, no UDF.
"""


def load(con, data_dir):
    """Materialize both tables into the (in-memory) database once, matching
    the TAQ harness's ``CREATE TABLE ... AS`` precedent -- a VIEW would
    re-decode the Parquet files on every query."""
    con.execute(f"CREATE OR REPLACE TABLE trades AS SELECT * FROM read_parquet('{data_dir}/trades.parquet')")
    con.execute(f"CREATE OR REPLACE TABLE quotes AS SELECT * FROM read_parquet('{data_dir}/quotes.parquet')")
    return con, con


SQL = {
    1: """
        SELECT sym, date_trunc('minute', ts) AS minute,
               arg_min(price, ts) AS open, max(price) AS high, min(price) AS low,
               arg_max(price, ts) AS close, sum(size) AS volume,
               sum(price * size) / sum(size) AS vwap
        FROM trades
        GROUP BY sym, minute
    """,
    2: """
        SELECT sym, ts, ln(price) - lag(ln(price)) OVER (PARTITION BY sym ORDER BY ts) AS ret
        FROM trades
    """,
    3: """
        WITH r AS (
            SELECT sym, ts, ln(price) - lag(ln(price)) OVER (PARTITION BY sym ORDER BY ts) AS ret
            FROM trades
        )
        SELECT sym, ts,
               CASE WHEN COUNT(ret) OVER w < 20 THEN NULL ELSE STDDEV_SAMP(ret) OVER w END AS roll_std
        FROM r
        WINDOW w AS (PARTITION BY sym ORDER BY ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
    """,
    4: """
        WITH r AS (
            SELECT sym, ts, ln(price) - lag(ln(price)) OVER (PARTITION BY sym ORDER BY ts) AS ret
            FROM trades
        )
        SELECT sym, ts,
               CASE WHEN COUNT(ret) OVER w < 2 THEN NULL ELSE STDDEV_SAMP(ret) OVER w END AS roll_std_5m
        FROM r
        WINDOW w AS (PARTITION BY sym ORDER BY ts RANGE BETWEEN INTERVAL 5 MINUTE PRECEDING AND CURRENT ROW)
    """,
    6: """
        SELECT t.sym AS sym, t.ts AS ts, t.price AS price, t.size AS size, q.bid AS bid, q.ask AS ask
        FROM trades t ASOF LEFT JOIN quotes q ON t.sym = q.sym AND t.ts >= q.ts
    """,
    7: """
        WITH matched AS (
            SELECT t.sym AS sym, t.ts AS ts, t.price AS price, t.size AS size,
                   q.ts AS qts, q.bid AS qbid, q.ask AS qask
            FROM trades t ASOF LEFT JOIN quotes q ON t.sym = q.sym AND t.ts >= q.ts
        )
        SELECT sym, ts, price, size,
               CASE WHEN qts IS NOT NULL AND ts - qts <= INTERVAL 5 SECOND THEN qbid ELSE NULL END AS bid,
               CASE WHEN qts IS NOT NULL AND ts - qts <= INTERVAL 5 SECOND THEN qask ELSE NULL END AS ask
        FROM matched
    """,
    8: """
        WITH per_minute AS (
            SELECT sym, date_trunc('minute', ts) AS minute, sum(price * size) AS value
            FROM trades
            GROUP BY sym, minute
        ), ranked AS (
            SELECT minute,
                   ROW_NUMBER() OVER (PARTITION BY minute ORDER BY value DESC) AS rnk,
                   COUNT(*) OVER (PARTITION BY minute) AS n_syms
            FROM per_minute
        )
        SELECT minute, SUM(CASE WHEN rnk <= CEIL(n_syms * 0.1) THEN 1 ELSE 0 END) AS top_decile_count
        FROM ranked
        GROUP BY minute
        ORDER BY minute
    """,
}


def _query(idx):
    def run(con, _con2):
        return con.sql(SQL[idx]).to_arrow_table()
    return run


QUERIES = [
    dict(idx=1, name="bars_1m", run=_query(1), code=SQL[1].strip()),
    dict(idx=2, name="log_returns", run=_query(2), code=SQL[2].strip()),
    dict(idx=3, name="roll_vol_rows", run=_query(3), code=SQL[3].strip()),
    dict(idx=4, name="roll_vol_5m", run=_query(4), code=SQL[4].strip()),
    dict(idx=5, name="ewm_vol", run=None,
         gap="DuckDB SQL has no public grouped exponentially-weighted-moving-stddev "
             "function; a UDF workaround is excluded by the board's rules.",
         code=None),
    dict(idx=6, name="asof_nbbo", run=_query(6), code=SQL[6].strip()),
    dict(idx=7, name="asof_tol_5s", run=_query(7), code=SQL[7].strip()),
    dict(idx=8, name="xsec_rank", run=_query(8), code=SQL[8].strip()),
]
