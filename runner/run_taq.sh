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
#
# KEYTEN_VERSION=0.1.49 pins the keyten install to an exact release
# (default: latest via --upgrade); the version recorded into results is
# always the venv's own imported __version__ post-install -- see
# run_pdsh.sh for why.

set -euo pipefail
cd "$(dirname "$0")/.."
source runner/lib/identity.sh

DATA="${1:?usage: run_taq.sh <data-dir> [threads]}"
THREADS="${2:-$(nproc)}"
WORK=".work"

mkdir -p "$WORK" results/taq-small
rm -f "$WORK/keyten.psv" "$WORK/duckdb.psv" "$WORK/polars.psv"
# The harness is vendored in-repo (harnesses/taq) with Keyten engine
# support, until the upstream NYSETAQBenchmarks PR is accepted.
rm -rf "$WORK/harness"
cp -r harnesses/taq "$WORK/harness"

python3 -m venv "$WORK/venv" 2>/dev/null || true
VENV="$WORK/venv/bin"
KEYTEN_PYTHON="$PWD/$VENV/python"
if [ -n "${KEYTEN_WHEEL:-}" ]; then
  "$VENV/pip" install -q --no-cache-dir --force-reinstall "$KEYTEN_WHEEL"
  "$VENV/pip" install -q --upgrade duckdb polars pyarrow numpy 2>/dev/null || "$VENV/pip" install -q --upgrade duckdb polars pyarrow
elif [ -n "${KEYTEN_VERSION:-}" ]; then
  "$VENV/pip" install -q --no-cache-dir --force-reinstall "keyten==$KEYTEN_VERSION"
  "$VENV/pip" install -q --upgrade duckdb polars pandas pyarrow numpy psutil pyyaml numexpr
else
  "$VENV/pip" install -q --upgrade keyten duckdb polars pandas pyarrow numpy psutil pyyaml numexpr
fi
KEYTEN_ACTUAL="$("$VENV/python" -c 'import keyten; print(keyten.__version__)')"
if [ -z "${KEYTEN_WHEEL:-}" ] && [ -n "${KEYTEN_VERSION:-}" ] && [ "$KEYTEN_ACTUAL" != "$KEYTEN_VERSION" ]; then
  echo "run_taq.sh: requested keyten==$KEYTEN_VERSION but venv has $KEYTEN_ACTUAL after install" >&2
  exit 1
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

cd "$WORK/harness"
COMMON="-storage_backend memory -querymeta ./artifacts/queries/inmemory/querymeta.psv \
  -paramdir ./artifacts/parameters/small -date 20260401 \
  -db $DATA -sortcols sym,time"

if [ "${BENCH_SKIP_CORRECTNESS:-false}" != true ]; then
  CORRECTNESS="../taq-correctness"
  rm -rf "$CORRECTNESS"
  mkdir -p "$CORRECTNESS"/{keyten,duckdb,polars}
  KEYTEN_WORKERS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
    $COMMON -engine keyten -queryfile ./artifacts/queries/inmemory/keyten.psv \
    -correctness-only -queryOutputDir "$CORRECTNESS/keyten" \
    -status-report "$CORRECTNESS/keyten-status.json"
  DUCKDB_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
    $COMMON -engine duckdb_con -queryfile ./artifacts/queries/inmemory/duckdb.psv \
    -correctness-only -queryOutputDir "$CORRECTNESS/duckdb" \
    -status-report "$CORRECTNESS/duckdb-status.json"
  POLARS_MAX_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
    $COMMON -engine polars -queryfile ./artifacts/queries/inmemory/polars.psv \
    -correctness-only -queryOutputDir "$CORRECTNESS/polars" \
    -status-report "$CORRECTNESS/polars-status.json"
  ../venv/bin/python ../../runner/check_taq_results.py "$CORRECTNESS" \
    ./artifacts/queries/inmemory/querymeta.psv \
    --report ../taq-correctness-report.json
fi
if [ "${BENCH_CORRECTNESS_ONLY:-false}" = true ]; then
  echo "TAQ timing skipped by request"
  exit 0
fi

print_keyten_identity before
FLUSH=./flush/noflush.sh KEYTEN_WORKERS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine keyten -queryfile ./artifacts/queries/inmemory/keyten.psv -result ../keyten.psv
FLUSH=./flush/noflush.sh DUCKDB_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine duckdb_con -queryfile ./artifacts/queries/inmemory/duckdb.psv -result ../duckdb.psv
FLUSH=./flush/noflush.sh POLARS_MAX_THREADS=$THREADS ../venv/bin/python pysrc/queryrunner/main.py \
  $COMMON -engine polars -queryfile ./artifacts/queries/inmemory/polars.psv -result ../polars.psv
print_keyten_identity after

cd ../..

METADATA="$WORK/taq-metadata.json"
"$KEYTEN_PYTHON" runner/benchmark_metadata.py \
  --suite taq --repo "$PWD" \
  --harness "$PWD/$WORK/harness" \
  --adapter "$PWD/$WORK/harness/pysrc/queryrunner/executors/inmemory" \
  --machine-out "$WORK/taq-machine-facts.json" \
  --metadata-out "$METADATA" \
  --samples 3 --warmups 0 --workers "$THREADS" \
  --benchmark-mode resident-memory

ver() { "$VENV/python" -c "import $1; print($1.__version__)"; }
python3 runner/convert_taq.py "$WORK/keyten.psv" keyten "$(ver keyten)" "$MACHINE" results/taq-small/keyten.json "$METADATA"
python3 runner/convert_taq.py "$WORK/duckdb.psv" duckdb "$(ver duckdb)" "$MACHINE" results/taq-small/duckdb.json "$METADATA"
python3 runner/convert_taq.py "$WORK/polars.psv" polars "$(ver polars)" "$MACHINE" results/taq-small/polars.json "$METADATA"
echo "results written to results/taq-small/ — open board/index.html to view"
