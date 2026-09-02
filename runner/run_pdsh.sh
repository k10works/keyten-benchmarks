#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce PDS-H (TPC-H derived) at SF10, in-memory ("skip" IO), for
# keyten, polars, and duckdb through the public polars-benchmark harness.
# Our keyten query set is vendored in adapters/pdsh-keyten and copied in.
#
#   ./runner/run_pdsh.sh [scale]
#   KEYTEN_VERSION=0.1.49 ./runner/run_pdsh.sh
#
# KEYTEN_VERSION pins the keyten install to an exact release (default:
# latest via --upgrade). Either way the version actually recorded into
# results/pdsh-sf10/keyten.json comes from the venv's own importlib
# metadata post-install, never from the requested string -- see the
# post-install assert below, added after the 0.1.49 sitting saw a bare
# `pip install --upgrade keyten` silently resolve to the previous release
# (0.1.48) despite 0.1.49 already being live on PyPI; root cause not
# reproduced here (no network in this environment) but the sitting report
# notes even a cache-cleared install reproduced it, which points at PyPI
# index/CDN propagation lag rather than pip's local wheel cache -- see
# task-1-report.md for the full writeup.
set -euo pipefail
cd "$(dirname "$0")/.."
SCALE="${1:-10.0}"
WORK=".work"; mkdir -p "$WORK" results/pdsh-sf10

# The harness is vendored in-repo (harnesses/pdsh, Keyten query set
# included) until the upstream polars-benchmark PR is accepted.
if [ ! -d "$WORK/pdsh" ]; then
    cp -r harnesses/pdsh "$WORK/pdsh"
fi
cd "$WORK/pdsh"
python3 -m venv .venv 2>/dev/null || true
if [ -n "${KEYTEN_VERSION:-}" ]; then
  .venv/bin/pip install -q --no-cache-dir --force-reinstall "keyten==$KEYTEN_VERSION"
  .venv/bin/pip install -q -r requirements.txt duckdb polars
else
  .venv/bin/pip install -q -r requirements.txt keyten duckdb polars
fi
KEYTEN_ACTUAL="$(.venv/bin/python -c 'import keyten; print(keyten.__version__)')"
if [ -n "${KEYTEN_VERSION:-}" ] && [ "$KEYTEN_ACTUAL" != "$KEYTEN_VERSION" ]; then
  echo "run_pdsh.sh: requested keyten==$KEYTEN_VERSION but venv has $KEYTEN_ACTUAL after install" >&2
  exit 1
fi
if [ ! -d "data/tables/scale-$SCALE" ] || [ -z "$(ls data/tables/scale-$SCALE/*.parquet 2>/dev/null)" ]; then
    .venv/bin/pip install -q tpchgen-cli
    mkdir -p "data/tables/scale-$SCALE"
    .venv/bin/tpchgen-cli --output-dir="data/tables/scale-$SCALE" --format=tbl -s "${SCALE%.0}"
    PYTHONPATH=. .venv/bin/python -m scripts.prepare_data --tpch_gen_folder="data/tables/scale-$SCALE"
fi
rm -f output/run/timings.csv
for e in keyten polars duckdb; do
  POLARS_STREAMING=false
  if [ "$e" = polars ]; then
    POLARS_STREAMING=true
  fi
  SCALE_FACTOR="$SCALE" RUN_IO_TYPE=skip RUN_LOG_TIMINGS=1 RUN_PRE_RUN=true RUN_ITERATIONS=3 \
    RUN_POLARS_STREAMING="$POLARS_STREAMING" \
    timeout 1800 .venv/bin/python -m queries.$e
done
cd ../..
MACHINE="$WORK/machine.json"   # written by run_taq.sh's probe, or create equivalently
VER() { "$WORK/pdsh/.venv/bin/python" -c "import $1; print($1.__version__)"; }
for e in keyten polars duckdb; do
  python3 runner/convert_generic.py pdsh "$WORK/pdsh/output/run/timings.csv" "$e" "$(VER $e)" "$MACHINE" \
    "results/pdsh-sf10/$e.json"
done
