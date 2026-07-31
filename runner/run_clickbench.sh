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
set -euo pipefail
cd "$(dirname "$0")/.."
HITS="${1:?usage: run_clickbench.sh <hits10m.parquet>}"
WORK=".work"; mkdir -p "$WORK" results/clickbench-10m
python3 -m venv "$WORK/venv" 2>/dev/null || true
"$WORK/venv/bin/pip" install -q --upgrade keyten polars duckdb fastapi uvicorn

run_daemon() { # dir, env, out
  local dir="$1" envs="$2" out="$3"
  ( cd "$dir" && env $envs "../../$WORK/venv/bin/python" "$(ls server*.py)" & echo $! > "../../$WORK/srv.pid" ) 
  until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 1; done
  ( cd "$dir" && "../../$WORK/venv/bin/python" run_board*.py > "../../$out" )
  kill "$(cat "$WORK/srv.pid")" 2>/dev/null || true
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
run_daemon adapters/clickbench-keyten "KEYTEN_NATIVE=$PWD/$WORK/hits10m.k10dir" "$WORK/cb_keyten.txt"
run_daemon adapters/clickbench-polars "POLARS_PARQUET=$HITS" "$WORK/cb_polars.txt"
"$WORK/venv/bin/python" runner/cb_duckdb.py "$HITS" adapters/clickbench-duckdb-queries.sql > "$WORK/cb_duckdb.txt"

MACHINE="$WORK/machine.json"
V() { "$WORK/venv/bin/python" -c "import $1;print($1.__version__)"; }
python3 runner/convert_generic.py clickbench "$WORK/cb_keyten.txt" keyten "$(V keyten)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/keyten.json
python3 runner/convert_generic.py clickbench "$WORK/cb_polars.txt" polars "$(V polars)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/polars.json
python3 runner/convert_generic.py clickbench "$WORK/cb_duckdb.txt" duckdb "$(V duckdb)" "$MACHINE" adapters/clickbench-duckdb-queries.sql results/clickbench-10m/duckdb.json
