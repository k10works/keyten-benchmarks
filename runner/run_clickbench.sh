#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce the ClickBench 43-query suite over a 10M-row hits subset for
# keyten, polars, and duckdb. The dataset derives from the public
# ClickBench hits file:
#   duckdb -c "COPY (FROM read_parquet('hits.parquet') LIMIT 10000000)
#              TO 'hits10m.parquet'"
# keyten and polars run their expression variants of the 43 queries
# (adapters/clickbench-*/queries.sql, one expression per line) through a
# small local daemon; duckdb runs the upstream SQL in-process over an
# in-memory table. Best of 3 per query, warm.
#
#   ./runner/run_clickbench.sh <hits10m.parquet>
#
# KEYTEN_VERSION=0.1.49 pins the keyten install to an exact release
# (default: latest via --upgrade); the version recorded into results is
# always the venv's own imported __version__ post-install -- see
# run_pdsh.sh for why.
set -euo pipefail
cd "$(dirname "$0")/.."
HITS="${1:?usage: run_clickbench.sh <hits10m.parquet>}"
HITS="$(realpath "$HITS")"
WORK=".work"; mkdir -p "$WORK" results/clickbench-10m
rm -f "$WORK/cb_keyten.txt" "$WORK/cb_polars.txt" "$WORK/cb_duckdb.txt"
python3 -m venv "$WORK/venv" 2>/dev/null || true
if [ -n "${KEYTEN_VERSION:-}" ]; then
  "$WORK/venv/bin/pip" install -q --no-cache-dir --force-reinstall "keyten==$KEYTEN_VERSION"
  "$WORK/venv/bin/pip" install -q --upgrade polars duckdb pyarrow fastapi uvicorn
else
  "$WORK/venv/bin/pip" install -q --upgrade keyten polars duckdb pyarrow fastapi uvicorn
fi
KEYTEN_ACTUAL="$("$WORK/venv/bin/python" -c 'import keyten; print(keyten.__version__)')"
if [ -n "${KEYTEN_VERSION:-}" ] && [ "$KEYTEN_ACTUAL" != "$KEYTEN_VERSION" ]; then
  echo "run_clickbench.sh: requested keyten==$KEYTEN_VERSION but venv has $KEYTEN_ACTUAL after install" >&2
  exit 1
fi

run_daemon() { # dir, env, out, optional capture dir
  local dir="$1" envs="$2" out="$3" capture="${4:-}"
  # exec so $! is the server itself; the pid file writes from repo root.
  ( cd "$dir" && exec env $envs "../../$WORK/venv/bin/python" "$(ls server*.py)" ) &
  echo $! > "$WORK/srv.pid"
  until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 1; done
  if [ -n "$capture" ]; then
    ( cd "$dir" && "../../$WORK/venv/bin/python" run_board*.py --capture-dir "$capture" > "../../$out" )
  else
    ( cd "$dir" && "../../$WORK/venv/bin/python" run_board*.py > "../../$out" )
  fi
  kill "$(cat "$WORK/srv.pid")" 2>/dev/null || true
  # Drain: the next daemon must not see this one's socket answering.
  until ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 1; done
}

# keyten: first run converts the parquet into the engine's native store.
# The EAGER path (read then write) is deliberate: the whole-column write
# applies the at-rest encoding verdicts (dictionaries, statistics) that
# the streaming sink does not yet decide as well.
"$WORK/venv/bin/python" - "$HITS" "$WORK/hits10m.k10dir" <<'PYEOF'
import sys, os
import keyten as kt
src, dst = sys.argv[1], sys.argv[2]
if not os.path.exists(dst):
    kt.DataFrame.read_parquet(src).write_native(dst)
PYEOF

if [ "${BENCH_SKIP_CORRECTNESS:-false}" != true ]; then
  ROOT="$PWD"
  CORRECTNESS="$ROOT/$WORK/clickbench-correctness"
  rm -rf "$CORRECTNESS"
  mkdir -p "$CORRECTNESS"/{keyten,duckdb,polars}
  run_daemon adapters/clickbench-keyten \
    "KEYTEN_NATIVE=$ROOT/$WORK/hits10m.k10dir CLICKBENCH_CAPTURE_DIR=$CORRECTNESS/keyten" \
    "$WORK/cb_keyten_capture.txt" "$CORRECTNESS/keyten"
  run_daemon adapters/clickbench-polars \
    "POLARS_PARQUET=$HITS CLICKBENCH_CAPTURE_DIR=$CORRECTNESS/polars" \
    "$WORK/cb_polars_capture.txt" "$CORRECTNESS/polars"
  "$WORK/venv/bin/python" runner/cb_duckdb.py "$HITS" \
    adapters/clickbench-duckdb-queries.sql --capture-dir "$CORRECTNESS/duckdb" \
    > "$WORK/cb_duckdb_capture.txt"
  "$WORK/venv/bin/python" runner/check_clickbench_results.py "$CORRECTNESS" \
    --report "$WORK/clickbench-correctness-report.json"
  if [ "${BENCH_CORRECTNESS_ONLY:-false}" = true ]; then
    echo "ClickBench correctness gate passed; timing skipped by request"
    exit 0
  fi
fi

run_daemon adapters/clickbench-keyten "KEYTEN_NATIVE=$PWD/$WORK/hits10m.k10dir" "$WORK/cb_keyten.txt"
run_daemon adapters/clickbench-polars "POLARS_PARQUET=$HITS" "$WORK/cb_polars.txt"
"$WORK/venv/bin/python" runner/cb_duckdb.py "$HITS" adapters/clickbench-duckdb-queries.sql > "$WORK/cb_duckdb.txt"

MACHINE="$WORK/machine.json"
"$WORK/venv/bin/python" - "$MACHINE" <<'PYEOF'
import datetime, json, os, platform, sys
cpu = ""
for line in open("/proc/cpuinfo"):
    if line.startswith("model name"):
        cpu = line.split(":", 1)[1].strip()
        break
ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**30
json.dump({"cpu": cpu, "cores": os.cpu_count(), "ram_gb": ram,
           "os": f"{platform.system()} {platform.machine()}",
           "date": datetime.date.today().isoformat()}, open(sys.argv[1], "w"))
PYEOF
V() { "$WORK/venv/bin/python" -c "import $1;print($1.__version__)"; }
python3 runner/convert_generic.py clickbench "$WORK/cb_keyten.txt" keyten "$(V keyten)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/keyten.json
python3 runner/convert_generic.py clickbench "$WORK/cb_polars.txt" polars "$(V polars)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/polars.json
python3 runner/convert_generic.py clickbench "$WORK/cb_duckdb.txt" duckdb "$(V duckdb)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/duckdb.json
