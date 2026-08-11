#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Deterministic seeded generator for the tick-ops suite's synthetic market
day: trades and quotes across a symbol universe, sorted by (sym, ts) and
written as Parquet.

    python3 gen_data.py <out-dir> [--trades N] [--quotes N] [--syms N]
                         [--seed N] [--scale F]

Defaults match the published suite: 20,000,000 trades and 60,000,000 quotes
across 2,000 symbols. ``--scale`` is a reproducibility knob (not a tuning
flag) that shrinks all three counts together for a fast smoke run, e.g.
``--scale 0.005`` for ~100k trades; explicit ``--trades``/``--quotes``/
``--syms`` override the scaled defaults.

Per symbol, trade prices and quote midpoints each follow an independent
geometric random walk (log-normal steps) from a per-symbol base price;
quote spreads and trade/quote sizes are drawn from log-normal
distributions. Symbol "activity" (row count) follows a log-normal weight
split across the universe via a multinomial draw, so a handful of symbols
trade far more than the rest -- realistic without claiming to model any
actual market.
"""

import argparse
import json
import os
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DAY_START = np.datetime64("2026-01-02T09:30:00", "ns")
DAY_SPAN_NS = 23_400 * 1_000_000_000  # 09:30 -> 16:00, 6.5h


def _syms(n_syms):
    width = max(4, len(str(n_syms - 1)))
    return [f"SYM{i:0{width}d}" for i in range(n_syms)]


def generate(n_trades, n_quotes, n_syms, seed):
    rng = np.random.default_rng(seed)
    syms = _syms(n_syms)

    weights = rng.lognormal(mean=0.0, sigma=1.0, size=n_syms)
    weights /= weights.sum()
    trade_counts = np.maximum(rng.multinomial(n_trades, weights), 2)
    quote_counts = np.maximum(rng.multinomial(n_quotes, weights), 2)

    t_sym, t_ts, t_price, t_size = [], [], [], []
    q_sym, q_ts, q_bid, q_ask, q_bsize, q_asize = [], [], [], [], [], []

    for i, sym in enumerate(syms):
        nt, nq = int(trade_counts[i]), int(quote_counts[i])
        base_price = rng.uniform(5.0, 500.0)

        t_off = np.sort(rng.integers(0, DAY_SPAN_NS, size=nt, endpoint=False))
        log_price = np.log(base_price) + np.cumsum(rng.normal(0.0, 0.0005, size=nt))
        t_sym.append(np.full(nt, sym))
        t_ts.append(t_off)
        t_price.append(np.exp(log_price))
        t_size.append(np.maximum(1, rng.lognormal(4.0, 1.0, size=nt).astype(np.int64)))

        q_off = np.sort(rng.integers(0, DAY_SPAN_NS, size=nq, endpoint=False))
        log_mid = np.log(base_price) + np.cumsum(rng.normal(0.0, 0.0004, size=nq))
        mid = np.exp(log_mid)
        spread = np.maximum(0.01, rng.exponential(0.02, size=nq))
        q_sym.append(np.full(nq, sym))
        q_ts.append(q_off)
        q_bid.append(mid - spread / 2.0)
        q_ask.append(mid + spread / 2.0)
        q_bsize.append(np.maximum(1, rng.lognormal(4.0, 1.0, size=nq).astype(np.int64)))
        q_asize.append(np.maximum(1, rng.lognormal(4.0, 1.0, size=nq).astype(np.int64)))

    trades = pa.table({
        "sym": np.concatenate(t_sym),
        "ts": (DAY_START + np.concatenate(t_ts)).astype("datetime64[ns]"),
        "price": np.concatenate(t_price),
        "size": np.concatenate(t_size),
    })
    quotes = pa.table({
        "sym": np.concatenate(q_sym),
        "ts": (DAY_START + np.concatenate(q_ts)).astype("datetime64[ns]"),
        "bid": np.concatenate(q_bid),
        "ask": np.concatenate(q_ask),
        "bsize": np.concatenate(q_bsize),
        "asize": np.concatenate(q_asize),
    })
    # Rows are already emitted in (sym, ts) order by construction (symbols
    # ascend, per-symbol offsets are pre-sorted); sort_by is cheap insurance.
    trades = trades.sort_by([("sym", "ascending"), ("ts", "ascending")])
    quotes = quotes.sort_by([("sym", "ascending"), ("ts", "ascending")])
    return trades, quotes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--trades", type=int, default=None)
    ap.add_argument("--quotes", type=int, default=None)
    ap.add_argument("--syms", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scale", type=float, default=1.0,
                     help="reproducibility knob: shrinks the default row/symbol "
                          "counts together, e.g. --scale 0.005 for a smoke run")
    args = ap.parse_args(argv)

    n_trades = args.trades if args.trades is not None else max(2, round(20_000_000 * args.scale))
    n_quotes = args.quotes if args.quotes is not None else max(2, round(60_000_000 * args.scale))
    n_syms = args.syms if args.syms is not None else max(2, round(2_000 * args.scale))

    trades, quotes = generate(n_trades, n_quotes, n_syms, args.seed)

    os.makedirs(args.out_dir, exist_ok=True)
    pq.write_table(trades, os.path.join(args.out_dir, "trades.parquet"))
    pq.write_table(quotes, os.path.join(args.out_dir, "quotes.parquet"))
    manifest = {
        "seed": args.seed,
        "syms": n_syms,
        "trades_requested": n_trades,
        "trades_actual": trades.num_rows,
        "quotes_requested": n_quotes,
        "quotes_actual": quotes.num_rows,
        "day_start": str(DAY_START),
        "day_span_ns": DAY_SPAN_NS,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {trades.num_rows} trades, {quotes.num_rows} quotes, "
          f"{n_syms} syms -> {args.out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
