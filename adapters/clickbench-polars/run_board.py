#!/usr/bin/env python3
# Warm best-of-N runner for the 43 ClickBench queries against the native daemon.
import sys, time, json, urllib.request
QF="queries.sql"; TRIES=3
def post(path, data=b""):
    r=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000"+path, data=data, method="POST"), timeout=600)
    return json.load(r)
post("/load")
queries=[l.rstrip("\n") for l in open(QF) if l.strip()]
total=0.0; rows=[]
for i,q in enumerate(queries):
    best=None
    for _ in range(TRIES):
        try:
            o=post("/query", q.encode()); e=o["elapsed"]
        except Exception as ex:
            e=float("nan"); print(f"q{i:02d} ERROR {ex}", file=sys.stderr); break
        best=e if best is None else min(best,e)
    rows.append((i,best)); total+=(best or 0.0)
    print(f"q{i:02d} {best*1000:8.2f}ms")
print(f"TOTAL {total*1000:8.2f}ms  (sum of best-of-{TRIES} over {len(queries)} queries)")
