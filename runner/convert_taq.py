#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Convert a NYSETAQBenchmarks result PSV into the board's result schema."""

import csv
import json
import sys

from convert_generic import dump


def convert(psv, engine, version, machine, out, metadata=None):
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
    dump(engine, version, "taq-small", queries, out, machine, metadata)


if __name__ == "__main__":
    if len(sys.argv) not in (6, 7):
        raise SystemExit("usage: convert_taq.py PSV ENGINE VERSION MACHINE OUT [METADATA]")
    psv, engine, version, machine_json, out, *rest = sys.argv[1:]
    metadata = json.load(open(rest[0])) if rest else None
    convert(psv, engine, version, json.load(open(machine_json)), out, metadata)
