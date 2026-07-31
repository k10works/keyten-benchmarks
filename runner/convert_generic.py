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
    best = {}
    for row in csv.DictReader(open(timings_csv)):
        if row["solution"] != engine:
            continue
        version = version or row["version"]
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
