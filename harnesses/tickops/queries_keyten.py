# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""The 8-query tick-ops suite over keyten's public 0.1.47 API. Every query
is expressed on the idiomatic Tier-1 lazy surface: ``group_by`` +
``truncate`` for bars (``group_by_bar`` buckets the *whole* frame by time
with no extra grouping key, so it does not fit a per-symbol bar -- see
README), in-agg VWAP, ``rolling_std``/``rolling_std_by``/``ewm_std``,
``join_asof`` with ``strategy``/``tolerance``, and ``rank().over()``.
"""

import keyten as kt


def load(data_dir):
    """Read both tables into memory once (matching the TAQ harness's
    ``read_parquet`` + ``.lazy()`` precedent); every query starts a fresh
    lazy plan off the same in-memory frame, so no query re-decodes Parquet."""
    trades = kt.DataFrame.read_parquet(f"{data_dir}/trades.parquet")
    quotes = kt.DataFrame.read_parquet(f"{data_dir}/quotes.parquet")
    return trades.lazy(), quotes.lazy()


def _with_ret(trades):
    return trades.with_columns([
        kt.col("price").log().diff().over("sym").alias("ret"),
    ])


def bars_1m(trades, quotes):
    return (
        trades.group_by(["sym", kt.col("ts").truncate("1m").alias("minute")])
        .agg([
            kt.col("price").first().alias("open"),
            kt.col("price").max().alias("high"),
            kt.col("price").min().alias("low"),
            kt.col("price").last().alias("close"),
            kt.col("size").sum().alias("volume"),
            ((kt.col("price") * kt.col("size")).sum() / kt.col("size").sum()).alias("vwap"),
        ])
        .collect()
    )


def log_returns(trades, quotes):
    return _with_ret(trades).select(["sym", "ts", "ret"]).collect()


def roll_vol_rows(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts", kt.col("ret").rolling_std(20).over("sym").alias("roll_std")])
        .collect()
    )


def roll_vol_5m(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts", kt.col("ret").rolling_std_by("ts", "5m").over("sym").alias("roll_std_5m")])
        .collect()
    )


def ewm_vol(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts", kt.col("ret").ewm_std(span=20).over("sym").alias("ewm_std")])
        .collect()
    )


def asof_nbbo(trades, quotes):
    left = trades.select(["sym", "ts", "price", "size"])
    right = quotes.select(["sym", "ts", "bid", "ask"])
    return left.join_asof(right, ("ts", "ts"), [("sym", "sym")], strategy="backward").collect()


def asof_tol_5s(trades, quotes):
    left = trades.select(["sym", "ts", "price", "size"])
    right = quotes.select(["sym", "ts", "bid", "ask"])
    return left.join_asof(
        right, ("ts", "ts"), [("sym", "sym")], strategy="backward", tolerance="5s"
    ).collect()


def xsec_rank(trades, quotes):
    per_minute = (
        trades.with_columns([(kt.col("price") * kt.col("size")).alias("value")])
        .group_by(["sym", kt.col("ts").truncate("1m").alias("minute")])
        .agg([kt.col("value").sum().alias("value")])
    )
    ranked = per_minute.with_columns([
        kt.col("value").rank("ordinal", descending=True).over("minute").alias("rnk"),
        kt.col("value").count().over("minute").alias("n_syms"),
    ]).with_columns([
        (kt.col("rnk") <= (kt.col("n_syms").cast("float") * 0.1).ceil()).cast("int").alias("is_top"),
    ])
    return (
        ranked.group_by(["minute"])
        .agg([kt.col("is_top").sum().alias("top_decile_count")])
        .sort("minute")
        .collect()
    )


QUERIES = [
    dict(idx=1, name="bars_1m", run=bars_1m,
         code="trades.group_by(['sym', col('ts').truncate('1m')]).agg([...open/high/low/close/volume, "
              "(price*size).sum()/size.sum() as vwap]).collect()"),
    dict(idx=2, name="log_returns", run=log_returns,
         code="trades.with_columns([col('price').log().diff().over('sym').alias('ret')])"
              ".select(['sym','ts','ret']).collect()"),
    dict(idx=3, name="roll_vol_rows", run=roll_vol_rows,
         code="ret.rolling_std(20).over('sym')"),
    dict(idx=4, name="roll_vol_5m", run=roll_vol_5m,
         code="ret.rolling_std_by('ts', '5m').over('sym')"),
    dict(idx=5, name="ewm_vol", run=ewm_vol,
         code="ret.ewm_std(span=20).over('sym')"),
    dict(idx=6, name="asof_nbbo", run=asof_nbbo,
         code="trades.join_asof(quotes, ('ts','ts'), [('sym','sym')], strategy='backward').collect()"),
    dict(idx=7, name="asof_tol_5s", run=asof_tol_5s,
         code="trades.join_asof(quotes, ('ts','ts'), [('sym','sym')], strategy='backward', "
              "tolerance='5s').collect()"),
    dict(idx=8, name="xsec_rank", run=xsec_rank,
         code="value.rank('ordinal', descending=True).over('minute') <= ceil(0.1 * n_syms_in_minute), "
              "summed per minute"),
]
