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
# KEYTEN_VERSION=0.1.49 ./runner/run_tickops.sh ... pins the keyten
# install to an exact release (default: latest via --upgrade); either way
# the version recorded into results/tickops/keyten.json comes from the
# venv's own imported __version__ post-install (ver() below), never from
# the requested string -- see run_pdsh.sh for why (0.1.49-sitting
# incident where --upgrade silently resolved to the prior release).
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

mkdir -p "$WORK" "$OUT" results/tickops
rm -f "$OUT/keyten.csv" "$OUT/duckdb.csv" "$OUT/polars.csv"

python3 -m venv "$WORK/venv" 2>/dev/null || true
VENV="$WORK/venv/bin"
if [ -n "${KEYTEN_WHEEL:-}" ]; then
  "$VENV/pip" install -q --no-cache-dir --force-reinstall "$KEYTEN_WHEEL"
  "$VENV/pip" install -q --upgrade duckdb polars pyarrow numpy 2>/dev/null || "$VENV/pip" install -q --upgrade duckdb polars pyarrow
elif [ -n "${KEYTEN_VERSION:-}" ]; then
  "$VENV/pip" install -q --no-cache-dir --force-reinstall "keyten==$KEYTEN_VERSION"
  "$VENV/pip" install -q --upgrade duckdb polars pyarrow numpy
else
  "$VENV/pip" install -q --upgrade keyten duckdb polars pyarrow numpy
fi
KEYTEN_ACTUAL="$("$VENV/python" -c 'import keyten; print(keyten.__version__)')"
if [ -z "${KEYTEN_WHEEL:-}" ] && [ -n "${KEYTEN_VERSION:-}" ] && [ "$KEYTEN_ACTUAL" != "$KEYTEN_VERSION" ]; then
  echo "run_tickops.sh: requested keyten==$KEYTEN_VERSION but venv has $KEYTEN_ACTUAL after install" >&2
  exit 1
fi

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
"$VENV/python" "$HARNESS/harness.py" capture --engine keyten --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
"$VENV/python" "$HARNESS/harness.py" capture --engine duckdb --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
env POLARS_MAX_THREADS="$THREADS" "$VENV/python" "$HARNESS/harness.py" capture \
  --engine polars --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"

"$VENV/python" "$HARNESS/harness.py" check --out-dir "$OUT" --engines keyten,duckdb,polars
if [ "${BENCH_CORRECTNESS_ONLY:-false}" = true ]; then
  echo "TickOps correctness gate passed; timing skipped by request"
  exit 0
fi

"$VENV/python" "$HARNESS/harness.py" time --engine keyten --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
"$VENV/python" "$HARNESS/harness.py" time --engine duckdb --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"
env POLARS_MAX_THREADS="$THREADS" "$VENV/python" "$HARNESS/harness.py" time \
  --engine polars --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"

ver() { "$VENV/python" -c "import $1; print($1.__version__)"; }
python3 runner/convert_tickops.py "$OUT/keyten.csv" keyten "$(ver keyten)" "$MACHINE" results/tickops/keyten.json
python3 runner/convert_tickops.py "$OUT/duckdb.csv" duckdb "$(ver duckdb)" "$MACHINE" results/tickops/duckdb.json
python3 runner/convert_tickops.py "$OUT/polars.csv" polars "$(ver polars)" "$MACHINE" results/tickops/polars.json
echo "results written to results/tickops/ — open board/index.html to view"
