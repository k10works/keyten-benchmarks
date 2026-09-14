#!/usr/bin/env python3
# One ClickBench sample per query, after untimed warmups, in a fresh round daemon.
import argparse, sys, time, json, urllib.request, os
from pathlib import Path
QF="queries.sql"
ENGINE="polars"
def post(path, data=b""):
    r=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:"+__import__("os").environ.get("BENCH_PORT","8000")+path, data=data, method="POST"), timeout=600)
    return json.load(r)
ap=argparse.ArgumentParser()
ap.add_argument("--capture-dir", type=Path)
args=ap.parse_args()
post("/load")
with open(QF) as query_file:
    queries=[l.rstrip("\n") for l in query_file if l.strip()]
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
warmups = int(os.environ.get("RUN_WARMUP_ITERATIONS", "2"))
if warmups < 0:
    raise SystemExit("warmups must be non-negative")
run_id = os.environ.get("RUN_BENCHMARK_RUN_ID", "0")
position = int(os.environ.get("RUN_ORDER_POSITION", "1"))
total = 0.0
for i, q in enumerate(queries):
    # The server executes these normally, but their returned timings are discarded.
    for _ in range(warmups):
        post("/query", q.encode())
    elapsed = post("/query", q.encode())["elapsed"]
    total += elapsed
    print(f"q{i:02d} {elapsed * 1000:.6f}ms run_id={run_id} order_position={position} warmup_iterations={warmups}")
print(f"TOTAL {total * 1000:.6f}ms (one timed sample per query)")
