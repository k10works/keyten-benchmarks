#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Tick-ops harness: load the Parquet dataset, cross-check each engine's
query result against the others for correctness, then time best-of-3.

    python3 harness.py run --engine {keyten,duckdb,polars} --data-dir DIR --out-dir DIR [--threads N]
    python3 harness.py check --out-dir DIR --engines keyten,duckdb,polars

``run`` executes one engine's 8 queries in-process (so KEYTEN_WORKERS /
DUCKDB_THREADS / POLARS_MAX_THREADS parity is set per-process by the
caller, same as run_taq.sh) and writes, per engine, ``<out-dir>/<engine>.csv``
(idx,name,query,ms best-of-3) and ``<out-dir>/<engine>.checksum.json``
(row counts + numeric spot-aggregates per query, computed by the engine's
own native reduction over the *full* materialized result -- see
``checksum()``).

``check`` loads every engine's checksum file and compares them pairwise:
row counts must match exactly; numeric sums must match within the
tolerance documented in README.md. A query missing from an engine's
checksums (a duckdb ewm_vol-style gap) is skipped, not treated as a
mismatch. Any real mismatch exits non-zero -- correctness fails loudly,
never silently.
"""

import argparse
import csv
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.compute as pc

ATOL = 1e-6
RTOL = 1e-4

# Numeric columns to spot-check per query idx, keyed by the shared output
# column names every engine module uses.
NUMERIC_COLS = {
    1: ["open", "high", "low", "close", "volume", "vwap"],
    2: ["ret"],
    3: ["roll_std"],
    4: ["roll_std_5m"],
    5: ["ewm_std"],
    6: ["price", "size", "bid", "ask"],
    7: ["price", "size", "bid", "ask"],
    8: ["top_decile_count"],
}


def checksum(table: pa.Table, idx: int) -> dict:
    out = {"rows": table.num_rows}
    for c in NUMERIC_COLS[idx]:
        col = table.column(c)
        valid = pc.count(col, mode="only_valid").as_py()
        s = pc.sum(col).as_py()
        out[f"{c}__valid"] = int(valid)
        out[f"{c}__sum"] = float(s) if s is not None else None
    return out


def _to_arrow(engine, result):
    if engine == "keyten":
        return pa.table(result)
    if engine == "polars":
        return result.to_arrow()
    return result  # duckdb query functions already return a pa.Table


def _load_engine(engine, data_dir, threads):
    if engine == "keyten":
        import keyten as kt
        kt.set_workers(threads)
        import queries_keyten as mod
        trades, quotes = mod.load(data_dir)
        return mod, trades, quotes
    if engine == "duckdb":
        import duckdb
        con = duckdb.connect()
        con.execute(f"SET threads = {threads}")
        import queries_duckdb as mod
        trades, quotes = mod.load(con, data_dir)
        return mod, trades, quotes
    if engine == "polars":
        import polars as pl  # noqa: F401  (import after POLARS_MAX_THREADS is set by the caller)
        import queries_polars as mod
        trades, quotes = mod.load(data_dir)
        return mod, trades, quotes
    raise ValueError(f"unknown engine {engine!r}")


def run(engine, data_dir, out_dir, threads):
    mod, trades, quotes = _load_engine(engine, data_dir, threads)
    os.makedirs(out_dir, exist_ok=True)

    rows_csv = []
    checksums = {}
    for q in mod.QUERIES:
        idx, name = q["idx"], q["name"]
        if q.get("run") is None:
            checksums[idx] = {"gap": q.get("gap", "not implemented")}
            print(f"[{engine}] {idx:2d} {name:15s} GAP: {q.get('gap')}", file=sys.stderr)
            continue

        # Correctness: materialize once, checksum via the engine's own
        # arrow-backed columnar reduction (no Python row loops).
        result = q["run"](trades, quotes)
        table = _to_arrow(engine, result)
        checksums[idx] = checksum(table, idx)
        del result, table

        # Timing: best of three, the query call itself (build + execute).
        times_ms = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = q["run"](trades, quotes)
            _to_arrow(engine, r)  # force materialization before stopping the clock
            t1 = time.perf_counter()
            times_ms.append((t1 - t0) * 1000.0)
            del r
        ms = round(min(times_ms), 3)
        rows_csv.append({"idx": idx, "name": name, "query": q.get("code", ""), "ms": ms})
        print(f"[{engine}] {idx:2d} {name:15s} {ms:10.3f} ms  rows={checksums[idx]['rows']}", file=sys.stderr)

    csv_path = os.path.join(out_dir, f"{engine}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["idx", "name", "query", "ms"])
        w.writeheader()
        w.writerows(rows_csv)

    checksum_path = os.path.join(out_dir, f"{engine}.checksum.json")
    with open(checksum_path, "w") as f:
        json.dump(checksums, f, indent=1)

    print(f"{csv_path}\n{checksum_path}", file=sys.stderr)


def _close(a, b, atol=ATOL, rtol=RTOL):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def check(out_dir, engines):
    loaded = {}
    for e in engines:
        path = os.path.join(out_dir, f"{e}.checksum.json")
        with open(path) as f:
            loaded[e] = {int(k): v for k, v in json.load(f).items()}

    all_idx = sorted({idx for c in loaded.values() for idx in c})
    mismatches = []
    report = {}
    for idx in all_idx:
        present = {e: loaded[e][idx] for e in engines if idx in loaded[e] and "gap" not in loaded[e][idx]}
        gaps = {e: loaded[e][idx]["gap"] for e in engines if idx in loaded[e] and "gap" in loaded[e][idx]}
        report[idx] = {"gaps": gaps}
        if len(present) < 2:
            report[idx]["status"] = "single-engine (nothing to cross-check)"
            continue
        engines_here = sorted(present)
        base_e = engines_here[0]
        base = present[base_e]
        field_ok = True
        for e in engines_here[1:]:
            for k, v in base.items():
                other = present[e].get(k)
                if k == "rows" or k.endswith("__valid"):
                    if other != v:
                        field_ok = False
                elif not _close(v, other):
                    field_ok = False
        status = "OK" if field_ok else "MISMATCH"
        report[idx]["status"] = status
        report[idx]["engines"] = {e: present[e] for e in engines_here}
        if status == "MISMATCH":
            mismatches.append(idx)

    with open(os.path.join(out_dir, "correctness_report.json"), "w") as f:
        json.dump(report, f, indent=1)

    for idx in all_idx:
        r = report[idx]
        gap_note = f" gaps={list(r['gaps'])}" if r["gaps"] else ""
        print(f"query {idx:2d}: {r['status']}{gap_note}")

    if mismatches:
        print(f"CORRECTNESS FAILURE: queries {mismatches} mismatch across engines", file=sys.stderr)
        sys.exit(1)
    print("all cross-checked queries match", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--engine", required=True, choices=["keyten", "duckdb", "polars"])
    run_p.add_argument("--data-dir", required=True)
    run_p.add_argument("--out-dir", required=True)
    run_p.add_argument("--threads", type=int, default=os.cpu_count() or 1)

    check_p = sub.add_parser("check")
    check_p.add_argument("--out-dir", required=True)
    check_p.add_argument("--engines", default="keyten,duckdb,polars")

    args = ap.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    if args.cmd == "run":
        run(args.engine, args.data_dir, args.out_dir, args.threads)
    else:
        check(args.out_dir, args.engines.split(","))


if __name__ == "__main__":
    main()
