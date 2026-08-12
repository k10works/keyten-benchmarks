#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Tick-ops harness: load the Parquet dataset once per engine, cross-check
every query's *full* result against the other engines for correctness,
then time best-of-3.

    python3 harness.py run --engine {keyten,duckdb,polars} --data-dir DIR --out-dir DIR [--threads N]
    python3 harness.py check --out-dir DIR --engines keyten,duckdb,polars

``run`` loads both tables into memory once (see each ``queries_*.load()``
-- keyten and duckdb materialize an in-memory table, polars an eager
``DataFrame``; no query re-decodes Parquet) with the given ``--threads``
(keyten via ``set_workers``, duckdb via ``SET threads``; polars reads
``POLARS_MAX_THREADS`` from the process environment at import time, so
the caller must set it before starting Python -- see run_tickops.sh),
then for each query:

1. Correctness -- call the query once, sort the *full* result on its
   natural key (see ``NATURAL_KEY``), and write it to
   ``<out-dir>/<engine>.q<idx>.parquet``. Nothing here is timed.
2. Timing -- wait for a quiet machine (``wait_quiet``, 1-min load < 1.0,
   bounded so an unattended run can't hang forever), then call the query
   three more times, timing each engine's own natural materialization:
   keyten's ``.collect()``, polars' ``.collect()``, duckdb's
   ``.to_arrow_table()`` (already embedded in its query functions -- for
   DuckDB, forcing the relation to Arrow *is* execution, the same role
   ``.collect()`` plays for the lazy engines). The best of three lands in
   ``<out-dir>/<engine>.csv`` (idx,name,query,ms).

``check`` reads every engine's per-query Parquet file and compares them
pairwise, column by column, over *every row* (not just an aggregate) --
this catches a value landing in the wrong group/row that a sum alone
would hide. Rows are matched positionally after both sides are sorted on
the same natural key; non-floating columns must match exactly, floating
columns within the tolerance documented in README.md. A query missing
from an engine (a documented gap, e.g. duckdb's ewm_vol) is skipped, not
treated as a mismatch. Any real mismatch exits non-zero -- correctness
fails loudly, never silently.
"""

import argparse
import csv
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ATOL = 1e-6
RTOL = 1e-4

# Max time to block waiting for a quiet machine before giving up and
# running anyway (loudly). Long enough to ride out a neighboring CI job,
# short enough that an unattended/CI-ish invocation doesn't hang forever.
QUIET_MAX_WAIT_S = 300


def wait_quiet(threshold=1.0, poll_s=2, max_wait_s=QUIET_MAX_WAIT_S):
    """Block until the 1-min load average is under ``threshold``.

    Ported from the parity-plan measurement-forensics diagnostic's rig fix
    (.superpowers/sdd/2026-08-11-window-perf-plan/rig/tickops_ab.py): an
    interleaved run with no per-query quiet gate lets self-load climb from
    the harness's own back-to-back process launches, which measurably
    inflates keyten's short/thread-heavy queries (reproduced there: a
    clean ~260ms query read ~562ms under synthetic 8-way contention,
    matching a historical outlier). Called before every query's timed
    block, not just once at process start. Bounded by ``max_wait_s`` so a
    CI-ish run can't hang forever on a host that never goes quiet -- gives
    up with a loud warning instead of blocking indefinitely.
    """
    waited = 0.0
    while True:
        load1, _, _ = os.getloadavg()
        if load1 < threshold:
            return
        if waited >= max_wait_s:
            print(
                f"WARNING: wait_quiet gave up after {max_wait_s}s "
                f"(1-min load {load1:.2f} still >= {threshold}); proceeding anyway",
                file=sys.stderr,
            )
            return
        time.sleep(poll_s)
        waited += poll_s


# The column(s) each query's result is sorted and compared on.
NATURAL_KEY = {
    1: ["sym", "minute"],
    2: ["sym", "ts"],
    3: ["sym", "ts"],
    4: ["sym", "ts"],
    5: ["sym", "ts"],
    6: ["sym", "ts"],
    7: ["sym", "ts"],
    8: ["minute"],
}


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

        # Correctness: materialize the full result once, sorted on its
        # natural key, and hand it to disk for check() to compare
        # value-for-value across engines. Not timed.
        result = q["run"](trades, quotes)
        table = _to_arrow(engine, result)
        key = [(k, "ascending") for k in NATURAL_KEY[idx]]
        table = table.sort_by(key)
        pq.write_table(table, os.path.join(out_dir, f"{engine}.q{idx}.parquet"))
        checksums[idx] = {"rows": table.num_rows, "columns": table.column_names}
        del result, table

        # Timing: best of three, each engine's own natural materialization
        # call only (no extra Arrow conversion inside the timed region).
        # Quiet-gate first -- see wait_quiet's docstring for why.
        wait_quiet()
        times_ms = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = q["run"](trades, quotes)
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


def _normalize(col):
    """Widen timestamp columns to a common unit so a us/ns storage
    difference alone never reads as a mismatch (widening ns<-us is exact,
    never lossy)."""
    if pa.types.is_timestamp(col.type):
        return pc.cast(col, pa.timestamp("ns"))
    return col


def _column_matches(a, b, atol=ATOL, rtol=RTOL):
    """True if arrow columns ``a`` and ``b`` agree at every row: same null
    positions, floating values within tolerance, everything else exact."""
    if len(a) != len(b):
        return False
    a, b = _normalize(a), _normalize(b)
    na, nb = pc.is_null(a), pc.is_null(b)
    if not pc.all(pc.equal(na, nb)).as_py():
        return False
    valid = pc.is_valid(a)
    if not pc.any(valid).as_py():
        return True  # every row null on both sides -- nothing left to compare
    av, bv = pc.filter(a, valid), pc.filter(b, valid)
    if pa.types.is_floating(a.type):
        diff = pc.abs(pc.subtract(av, bv))
        thresh = pc.add(atol, pc.multiply(rtol, pc.max_element_wise(pc.abs(av), pc.abs(bv))))
        return pc.all(pc.less_equal(diff, thresh)).as_py()
    return pc.all(pc.equal(av, bv)).as_py()


def _table_matches(a: pa.Table, b: pa.Table):
    """Compare every shared column, row-for-row (both pre-sorted on the
    same natural key). Returns (ok, [mismatched column names])."""
    if a.num_rows != b.num_rows:
        return False, ["rows"]
    cols = [c for c in a.column_names if c in b.column_names]
    bad = [c for c in cols if not _column_matches(a.column(c), b.column(c))]
    return not bad, bad


def check(out_dir, engines):
    gaps = {}
    present_idx = {}
    for e in engines:
        with open(os.path.join(out_dir, f"{e}.checksum.json")) as f:
            summary = {int(k): v for k, v in json.load(f).items()}
        for idx, v in summary.items():
            if "gap" in v:
                gaps.setdefault(idx, {})[e] = v["gap"]
            else:
                present_idx.setdefault(idx, set()).add(e)

    all_idx = sorted(set(present_idx) | set(gaps))
    mismatches = []
    report = {}
    for idx in all_idx:
        engines_here = sorted(present_idx.get(idx, ()))
        report[idx] = {"gaps": gaps.get(idx, {})}
        if len(engines_here) < 2:
            report[idx]["status"] = "single-engine (nothing to cross-check)"
            continue
        tables = {
            e: pq.read_table(os.path.join(out_dir, f"{e}.q{idx}.parquet"))
            for e in engines_here
        }
        base_e = engines_here[0]
        base = tables[base_e]
        bad_pairs = {}
        for e in engines_here[1:]:
            ok, bad_cols = _table_matches(base, tables[e])
            if not ok:
                bad_pairs[f"{base_e}~{e}"] = bad_cols
        status = "OK" if not bad_pairs else "MISMATCH"
        report[idx]["status"] = status
        report[idx]["rows"] = base.num_rows
        if bad_pairs:
            report[idx]["mismatched_columns"] = bad_pairs
            mismatches.append(idx)

    with open(os.path.join(out_dir, "correctness_report.json"), "w") as f:
        json.dump(report, f, indent=1)

    for idx in all_idx:
        r = report[idx]
        gap_note = f" gaps={list(r['gaps'])}" if r["gaps"] else ""
        bad_note = f" mismatched={r['mismatched_columns']}" if "mismatched_columns" in r else ""
        print(f"query {idx:2d}: {r['status']}{gap_note}{bad_note}")

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
