#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce the tick-ops suite: keyten, duckdb, and polars over the same
# synthetic-market-day Parquet dataset, through the vendored harness in
# harnesses/tickops.
#
#   ./runner/run_tickops.sh <data-dir> [threads] [-- gen_data.py args...]
#
# <data-dir> is generated via gen_data.py the first time (deterministic,
# seeded -- see harnesses/tickops/README.md) if it doesn't already contain
# trades.parquet/quotes.parquet; delete it to regenerate. A tiny smoke
# dataset:
#
#   ./runner/run_tickops.sh .work/tickops-tiny 4 -- --scale 0.005
#
# Results land in results/tickops-small/<engine>.json next to the
# published ones; correctness is cross-checked across all three engines
# before any timing is recorded (see harness.py) and fails the run loudly
# on a real mismatch.

set -euo pipefail
cd "$(dirname "$0")/.."

DATA="${1:?usage: run_tickops.sh <data-dir> [threads] [-- gen_data.py args...]}"
shift
THREADS="${1:-$(nproc)}"
[ $# -gt 0 ] && shift
[ "${1:-}" = "--" ] && shift
GEN_ARGS=("$@")

WORK=".work"
HARNESS="harnesses/tickops"
OUT="$WORK/tickops"

mkdir -p "$WORK" "$OUT" results/tickops-small

python3 -m venv "$WORK/venv" 2>/dev/null || true
VENV="$WORK/venv/bin"
"$VENV/pip" install -q --upgrade keyten duckdb polars pyarrow numpy

if [ ! -f "$DATA/trades.parquet" ]; then
  echo "generating dataset at $DATA ..."
  "$VENV/python" "$HARNESS/gen_data.py" "$DATA" "${GEN_ARGS[@]}"
fi

MACHINE="$WORK/machine.json"
python3 - "$MACHINE" <<'PYEOF'
import json, os, sys, platform, datetime
cpu = ""
for line in open("/proc/cpuinfo"):
    if line.startswith("model name"):
        cpu = line.split(":", 1)[1].strip(); break
ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**30
json.dump({"cpu": cpu, "cores": os.cpu_count(), "ram_gb": ram,
           "os": f"{platform.system()} {platform.machine()}",
           "date": datetime.date.today().isoformat()}, open(sys.argv[1], "w"))
PYEOF

# harness.py takes thread count as --threads for every engine (keyten
# kt.set_workers(), duckdb SET threads); polars is the one engine that
# reads its thread count from the process environment at import time, so
# POLARS_MAX_THREADS has to be set before the interpreter starts. Setting
# KEYTEN_WORKERS/DUCKDB_THREADS here too would be vestigial -- harness.py
# doesn't read them -- so only polars gets an env var.
"$VENV/python" "$HARNESS/harness.py" run --engine keyten --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
"$VENV/python" "$HARNESS/harness.py" run --engine duckdb --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
env POLARS_MAX_THREADS="$THREADS" "$VENV/python" "$HARNESS/harness.py" run \
  --engine polars --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"

"$VENV/python" "$HARNESS/harness.py" check --out-dir "$OUT" --engines keyten,duckdb,polars

ver() { "$VENV/python" -c "import $1; print($1.__version__)"; }
python3 runner/convert_tickops.py "$OUT/keyten.csv" keyten "$(ver keyten)" "$MACHINE" results/tickops-small/keyten.json
python3 runner/convert_tickops.py "$OUT/duckdb.csv" duckdb "$(ver duckdb)" "$MACHINE" results/tickops-small/duckdb.json
python3 runner/convert_tickops.py "$OUT/polars.csv" polars "$(ver polars)" "$MACHINE" results/tickops-small/polars.json
echo "results written to results/tickops-small/ — open board/index.html to view"
