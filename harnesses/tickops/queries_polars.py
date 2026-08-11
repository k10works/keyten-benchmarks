# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""The 8-query tick-ops suite over Polars' public lazy API."""

import polars as pl


def load(data_dir):
    """Read both tables into memory once (``read_parquet`` is eager), then
    hand back a fresh lazy plan per table; every query starts from the
    in-memory frame instead of re-decoding Parquet."""
    trades = pl.read_parquet(f"{data_dir}/trades.parquet").lazy()
    quotes = pl.read_parquet(f"{data_dir}/quotes.parquet").lazy()
    return trades, quotes


def _with_ret(trades):
    return trades.with_columns([
        pl.col("price").log().diff().over("sym").alias("ret"),
    ])


def bars_1m(trades, quotes):
    # group_by_dynamic is polars' idiomatic per-symbol time-bucketing (vs.
    # group_by + truncate): its defaults (closed="left", label="left",
    # start_by="window") floor-bucket exactly like keyten's truncate("1m"),
    # verified boundary-for-boundary against group_by+truncate.
    return (
        trades.group_by_dynamic(pl.col("ts").alias("minute"), every="1m", group_by="sym")
        .agg([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("size").sum().alias("volume"),
            ((pl.col("price") * pl.col("size")).sum() / pl.col("size").sum()).alias("vwap"),
        ])
        .collect()
    )


def log_returns(trades, quotes):
    return _with_ret(trades).select(["sym", "ts", "ret"]).collect()


def roll_vol_rows(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts",
                 pl.col("ret").rolling_std(window_size=20, min_samples=20).over("sym").alias("roll_std")])
        .collect()
    )


def roll_vol_5m(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts",
                 pl.col("ret").rolling_std_by("ts", window_size="5m", min_samples=2, closed="right")
                 .over("sym").alias("roll_std_5m")])
        .collect()
    )


def ewm_vol(trades, quotes):
    return (
        _with_ret(trades)
        .select(["sym", "ts", pl.col("ret").ewm_std(span=20).over("sym").alias("ewm_std")])
        .collect()
    )


def asof_nbbo(trades, quotes):
    left = trades.select(["sym", "ts", "price", "size"])
    right = quotes.select(["sym", "ts", "bid", "ask"])
    return left.join_asof(right, on="ts", by="sym", strategy="backward").collect()


def asof_tol_5s(trades, quotes):
    left = trades.select(["sym", "ts", "price", "size"])
    right = quotes.select(["sym", "ts", "bid", "ask"])
    return left.join_asof(right, on="ts", by="sym", strategy="backward", tolerance="5s").collect()


def xsec_rank(trades, quotes):
    per_minute = (
        trades.with_columns([(pl.col("price") * pl.col("size")).alias("value")])
        .group_by_dynamic(pl.col("ts").alias("minute"), every="1m", group_by="sym")
        .agg([pl.col("value").sum().alias("value")])
    )
    ranked = per_minute.with_columns([
        pl.col("value").rank(method="ordinal", descending=True).over("minute").alias("rnk"),
        pl.col("value").count().over("minute").alias("n_syms"),
    ]).with_columns([
        (pl.col("rnk") <= (pl.col("n_syms").cast(pl.Float64) * 0.1).ceil()).cast(pl.Int64).alias("is_top"),
    ])
    return (
        ranked.group_by(["minute"])
        .agg([pl.col("is_top").sum().alias("top_decile_count")])
        .sort("minute")
        .collect()
    )


QUERIES = [
    dict(idx=1, name="bars_1m", run=bars_1m,
         code="trades.group_by_dynamic(col('ts').alias('minute'), every='1m', group_by='sym')"
              ".agg([...open/high/low/close/volume, (price*size).sum()/size.sum() as vwap]).collect()"),
    dict(idx=2, name="log_returns", run=log_returns,
         code="trades.with_columns([col('price').log().diff().over('sym').alias('ret')])"
              ".select(['sym','ts','ret']).collect()"),
    dict(idx=3, name="roll_vol_rows", run=roll_vol_rows,
         code="ret.rolling_std(window_size=20, min_samples=20).over('sym')"),
    dict(idx=4, name="roll_vol_5m", run=roll_vol_5m,
         code="ret.rolling_std_by('ts', window_size='5m', min_samples=2).over('sym')"),
    dict(idx=5, name="ewm_vol", run=ewm_vol,
         code="ret.ewm_std(span=20).over('sym')"),
    dict(idx=6, name="asof_nbbo", run=asof_nbbo,
         code="trades.join_asof(quotes, on='ts', by='sym', strategy='backward').collect()"),
    dict(idx=7, name="asof_tol_5s", run=asof_tol_5s,
         code="trades.join_asof(quotes, on='ts', by='sym', strategy='backward', "
              "tolerance='5s').collect()"),
    dict(idx=8, name="xsec_rank", run=xsec_rank,
         code="value = group_by_dynamic(col('ts').alias('minute'), every='1m', group_by='sym').agg(sum); "
              "rank('ordinal', descending=True).over('minute') <= ceil(0.1 * n_syms_in_minute), summed per minute"),
]
