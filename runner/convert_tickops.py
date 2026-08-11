#!/usr/bin/env python3
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
"""Convert one engine's tickops harness.py CSV (idx,name,query,ms) into the
board's result schema.

The pdsh/clickbench converters in convert_generic.py each parse a format
specific to their own upstream harness (a PDS-H timings.csv, a ClickBench
transcript); neither shape fits the tickops harness's own idx/query/ms CSV,
so this is a small, separate converter. It reuses convert_generic.dump()
for the actual document shape so the schema stays identical across every
suite.
"""

import csv
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from convert_generic import dump  # noqa: E402


def tickops(results_csv, engine, version, out, mach):
    queries = []
    with open(results_csv) as f:
        for row in csv.DictReader(f):
            queries.append({
                "idx": int(row["idx"]),
                "query": row["query"][:160],
                "ms": round(float(row["ms"]), 2),
            })
    queries.sort(key=lambda q: q["idx"])
    dump(engine, version, "tickops-small", queries, out, mach)


if __name__ == "__main__":
    _, results_csv, engine, version, machine_json, out = sys.argv
    tickops(results_csv, engine, version, out, json.load(open(machine_json)))
