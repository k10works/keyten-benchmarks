#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce the TAQ suite: keyten, duckdb, and polars over the same
# in-memory dataset, through the public NYSETAQBenchmarks queryrunner.
#
#   ./runner/run_taq.sh <data-dir> [threads]
#
# <data-dir> must contain the harness's parquet dataset
# (DATA/small/parquet/rowgroup layout); see the harness README for
# generating it from the public sample day. Results land in
# results/taq-small/<engine>.json next to the published ones.

set -euo pipefail
cd "$(dirname "$0")/.."

DATA="${1:?usage: run_taq.sh <data-dir> [threads]}"
THREADS="${2:-$(nproc)}"
HARNESS_REPO="https://github.com/singaraiona/NYSETAQBenchmarks"
HARNESS_REV="main"   # pin to a commit when publishing new numbers
WORK=".work"

mkdir -p "$WORK" results/taq-small
if [ ! -d "$WORK/harness" ]; then
    git clone --depth 1 "$HARNESS_REPO" "$WORK/harness"
fi
git -C "$WORK/harness" checkout -q "$HARNESS_REV"

python3 -m venv "$WORK/venv" 2>/dev/null || true
VENV="$WORK/venv/bin"
"$VENV/pip" install -q --upgrade keyten duckdb polars pandas pyarrow numpy psutil pyyaml numexpr

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

cd "$WORK/harness"
COMMON="-storage_backend memory -querymeta ./artifacts/queries/inmemory/querymeta.psv \
  -paramdir ./artifacts/parameters/small -date 20260401 \
  -db $DATA -sortcols sym,time"

FLUSH=./flush/noflush.sh KEYTEN_WORKERS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine keyten -queryfile ./artifacts/queries/inmemory/keyten.psv -result ../keyten.psv
FLUSH=./flush/noflush.sh DUCKDB_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine duckdb_con -queryfile ./artifacts/queries/inmemory/duckdb.psv -result ../duckdb.psv
FLUSH=./flush/noflush.sh POLARS_MAX_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine polars -queryfile ./artifacts/queries/inmemory/polars.psv -result ../polars.psv
cd ../..

ver() { "$VENV/python" -c "import $1; print($1.__version__)"; }
python3 runner/convert_taq.py "$WORK/keyten.psv" keyten "$(ver keyten)" "$MACHINE" results/taq-small/keyten.json
python3 runner/convert_taq.py "$WORK/duckdb.psv" duckdb "$(ver duckdb)" "$MACHINE" results/taq-small/duckdb.json
python3 runner/convert_taq.py "$WORK/polars.psv" polars "$(ver polars)" "$MACHINE" results/taq-small/polars.json
echo "results written to results/taq-small/ — open board/index.html to view"
