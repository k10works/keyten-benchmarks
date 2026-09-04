#!/usr/bin/env python3
# Warm best-of-N runner for the 43 ClickBench queries against the native daemon.
import argparse, sys, time, json, urllib.request
from pathlib import Path
QF="queries.sql"; TRIES=3
ENGINE="keyten"
def post(path, data=b""):
    r=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000"+path, data=data, method="POST"), timeout=600)
    return json.load(r)
ap=argparse.ArgumentParser()
ap.add_argument("--capture-dir", type=Path)
args=ap.parse_args()
post("/load")
queries=[l.rstrip("\n") for l in open(QF) if l.strip()]
if args.capture_dir is not None:
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    statuses={}
    for i,q in enumerate(queries):
        try:
            value=post(f"/capture/{i}", q.encode())
            statuses[str(i)]={"status":"success", **value}
            print(f"q{i:02d} PASS rows={value['rows']}", file=sys.stderr)
        except Exception as ex:
            statuses[str(i)]={"status":"error", "error":str(ex)}
            print(f"q{i:02d} FAIL {ex}", file=sys.stderr)
    (args.capture_dir.parent/f"{ENGINE}-status.json").write_text(
        json.dumps({"engine":ENGINE,"queries":statuses}, indent=2)+"\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
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
