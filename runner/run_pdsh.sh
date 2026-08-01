#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce PDS-H (TPC-H derived) at SF10, in-memory ("skip" IO), for
# keyten, polars, and duckdb through the public polars-benchmark harness.
# Our keyten query set is vendored in adapters/pdsh-keyten and copied in.
#
#   ./runner/run_pdsh.sh [scale]
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
.venv/bin/pip install -q -r requirements.txt keyten duckdb polars
make tables SCALE_FACTOR="$SCALE" 2>/dev/null || .venv/bin/python scripts/prepare_data.py
rm -f output/run/timings.csv
for e in keyten polars duckdb; do
  SCALE_FACTOR="$SCALE" RUN_IO_TYPE=skip RUN_LOG_TIMINGS=1 RUN_PRE_RUN=true RUN_ITERATIONS=3 \
    .venv/bin/python -m queries.$e
done
cd ../..
MACHINE="$WORK/machine.json"   # written by run_taq.sh's probe, or create equivalently
for e in keyten polars duckdb; do
  python3 runner/convert_generic.py pdsh "$WORK/pdsh/output/run/timings.csv" "$e" "" "$MACHINE" \
    "results/pdsh-sf10/$e.json"
done
