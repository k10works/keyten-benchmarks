#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Convert PDS-H timings.csv or a ClickBench board transcript into the
board's result schema."""

import csv
import json
import sys


def machine(path):
    return json.load(open(path))


def pdsh(timings_csv, engine, out, mach, version):
    """``version``, when given, pins which rows count: only rows for
    ``engine`` whose own recorded ``version`` column matches are used for
    the per-query min. This matters because the upstream harness
    (queries/common_utils.py:log_query_timing) *appends* to timings.csv
    rather than overwriting it, so a manual partial rerun (e.g. only
    re-timing keyten after a version fix, without re-running the whole
    suite) leaves older rows in the file. Without this filter, min()
    silently blends timings from two different engine versions -- this is
    exactly what the 0.1.49 sitting hit and had to hand-purge (see
    official-sitting-report.md, issue 1). If ``version`` is not given, all
    versions found for ``engine`` in the file must agree, or this refuses
    to guess and errors out loudly instead of blending them.
    """
    rows = [row for row in csv.DictReader(open(timings_csv)) if row["solution"] == engine]
    if not rows:
        raise SystemExit(f"convert_generic pdsh: no rows for engine {engine!r} in {timings_csv}")

    if version:
        rows = [row for row in rows if row["version"] == version]
        if not rows:
            raise SystemExit(
                f"convert_generic pdsh: engine {engine!r} has rows in {timings_csv} "
                f"but none at requested version {version!r} -- refusing to blend other versions"
            )
    else:
        seen_versions = {row["version"] for row in rows}
        if len(seen_versions) > 1:
            raise SystemExit(
                f"convert_generic pdsh: {timings_csv} has mixed versions for engine "
                f"{engine!r} ({sorted(seen_versions)}) and no --version was given to "
                f"disambiguate -- refusing to silently blend them. Pass the version "
                f"explicitly, or clear/regenerate timings.csv before rerunning."
            )
        version = seen_versions.pop()

    best = {}
    for row in rows:
        qn = int(row["query_number"])
        ms = float(row["duration[s]"]) * 1000
        best[qn] = min(best.get(qn, ms), ms)
    queries = [
        {"idx": qn, "query": f"PDS-H query {qn} (TPC-H derived)", "ms": round(ms, 2)}
        for qn, ms in sorted(best.items())
    ]
    dump(engine, version, "pdsh-sf10", queries, out, mach)


def clickbench(transcript, engine, version, out, mach, sqlfile):
    sql = [l.strip() for l in open(sqlfile) if l.strip()] if sqlfile else []
    queries = []
    for line in open(transcript):
        if not line.startswith("q"):
            continue
        name, ms = line.split()[0], line.split()[1]
        idx = int(name[1:])
        if ms in ("nanms", "ERROR"):
            continue
        queries.append({
            "idx": idx + 1,
            "query": sql[idx][:160] if idx < len(sql) else "",
            "ms": round(float(ms.replace("ms", "")), 2),
        })
    dump(engine, version, "clickbench-10m", queries, out, mach)


def dump(engine, version, suite, queries, out, mach):
    doc = {
        "suite": suite,
        "engine": engine,
        "version": version,
        "threads": mach["cores"],
        "machine": mach,
        "queries": queries,
        "total_ms": round(sum(q["ms"] for q in queries), 1),
    }
    json.dump(doc, open(out, "w"), indent=1)
    print(out, doc["total_ms"], "ms over", len(queries), "queries")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "pdsh":
        _, _, timings, engine, version, machine_json, out = sys.argv
        pdsh(timings, engine, out, machine(machine_json), version or None)
    else:
        _, _, transcript, engine, version, machine_json, sqlfile, out = sys.argv
        clickbench(transcript, engine, version, out, machine(machine_json), sqlfile)
