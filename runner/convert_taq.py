#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Convert a NYSETAQBenchmarks result PSV into the board's result schema."""

import csv
import json
import sys


def convert(psv, engine, version, machine, out):
    queries = []
    with open(psv) as f:
        for r in csv.DictReader(f, delimiter="|"):
            try:
                idx = int(r["idx"])
            except ValueError:
                continue
            if idx < 1 or r["status"] != "success":
                continue
            times = [int(r[k]) for k in ("run1timeNS", "run2timeNS", "run3timeNS") if r[k]]
            if not times:
                continue
            queries.append({
                "idx": idx,
                "tags": r.get("tags", ""),
                "query": r.get("query", "")[:160],
                "ms": round(min(times) / 1e6, 2),
            })
    doc = {
        "suite": "taq-small",
        "engine": engine,
        "version": version,
        "threads": 8,
        "machine": machine,
        "queries": queries,
        "total_ms": round(sum(q["ms"] for q in queries), 1),
    }
    json.dump(doc, open(out, "w"), indent=1)
    print(out, doc["total_ms"], "ms over", len(queries), "queries")


if __name__ == "__main__":
    psv, engine, version, machine_json, out = sys.argv[1:6]
    convert(psv, engine, version, json.load(open(machine_json)), out)
