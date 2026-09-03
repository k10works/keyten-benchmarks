#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
#
# Reproduce PDS-H (TPC-H derived) at SF10, in-memory ("skip" IO), for
# keyten, polars, and duckdb through the public polars-benchmark harness.
# Our keyten query set is vendored in adapters/pdsh-keyten. Every sitting
# executes a verified fresh snapshot while reusing only data and venv caches.
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
ROOT="$PWD"
WORK="$ROOT/.work"
CACHE="$WORK/pdsh"
RUN_DIR="$WORK/pdsh-run"
SAMPLES="${BENCH_SAMPLES:-12}"
WARMUPS="${BENCH_WARMUPS:-2}"
THREADS="${BENCH_THREADS:-$(nproc)}"
BENCHMARK_MODE="${BENCH_MODE:-resident-native}"
case "$BENCHMARK_MODE" in
  resident-native)
    IO_TYPE=skip
    DEFAULT_RESULT_DIR="$ROOT/results/pdsh-sf10"
    ;;
  end-to-end)
    IO_TYPE=parquet
    DEFAULT_RESULT_DIR="$ROOT/results/pdsh-sf10-end-to-end"
    ;;
  *) echo "run_pdsh.sh: BENCH_MODE must be resident-native or end-to-end" >&2; exit 1 ;;
esac
RESULT_DIR="${BENCH_RESULTS_DIR:-$DEFAULT_RESULT_DIR}"
mkdir -p "$WORK" "$CACHE/data" "$RESULT_DIR"

case "$SAMPLES:$WARMUPS:$THREADS" in
  *[!0-9:]*|0:*|*:0) echo "run_pdsh.sh: invalid sample/warmup/thread counts" >&2; exit 1 ;;
esac

# Never execute the long-lived cache tree: old copies there caused tracked
# adapter edits to be silently ignored. materialize.py removes and verifies
# only RUN_DIR; the 19GB dataset and venv remain under CACHE.
python3 runner/verify_adapter.py adapters/pdsh-keyten harnesses/pdsh/queries/keyten
python3 runner/materialize.py harnesses/pdsh "$RUN_DIR" --allowed-parent "$WORK" >/dev/null
python3 -m venv "$CACHE/.venv" 2>/dev/null || true
CACHE_ABS="$(realpath "$CACHE")"
ln -s "$CACHE_ABS/data" "$RUN_DIR/data"
ln -s "$CACHE_ABS/.venv" "$RUN_DIR/.venv"

VENV="$CACHE_ABS/.venv/bin"
if [ -n "${KEYTEN_VERSION:-}" ]; then
  "$VENV/pip" install -q --no-cache-dir --force-reinstall "keyten==$KEYTEN_VERSION"
  "$VENV/pip" install -q -r "$RUN_DIR/requirements.txt" duckdb polars
else
  "$VENV/pip" install -q -r "$RUN_DIR/requirements.txt" keyten duckdb polars
fi
KEYTEN_ACTUAL="$("$VENV/python" -c 'import keyten; print(keyten.__version__)')"
if [ -n "${KEYTEN_VERSION:-}" ] && [ "$KEYTEN_ACTUAL" != "$KEYTEN_VERSION" ]; then
  echo "run_pdsh.sh: requested keyten==$KEYTEN_VERSION but venv has $KEYTEN_ACTUAL after install" >&2
  exit 1
fi

cd "$RUN_DIR"
if [ ! -d "data/tables/scale-$SCALE" ] || [ -z "$(ls data/tables/scale-$SCALE/*.parquet 2>/dev/null)" ]; then
    "$VENV/pip" install -q tpchgen-cli
    mkdir -p "data/tables/scale-$SCALE"
    "$VENV/tpchgen-cli" --output-dir="data/tables/scale-$SCALE" --format=tbl -s "${SCALE%.0}"
    PYTHONPATH=. "$VENV/python" -m scripts.prepare_data --tpch_gen_folder="data/tables/scale-$SCALE"
fi
rm -f output/run/timings.csv

STORED_CHECK=false
case "$SCALE" in
  1|1.0)
    "$VENV/python" "$ROOT/runner/prepare_pdsh_answers.py" \
      "$RUN_DIR/tpch-dbgen/answers" "$CACHE_ABS/data/answers"
    STORED_CHECK=true
    ;;
esac

MACHINE="$WORK/machine.json"
METADATA="$WORK/pdsh-metadata.json"
CLEAN_ARGS=()
if [ "${BENCH_REQUIRE_CLEAN:-false}" = true ]; then
  CLEAN_ARGS+=(--require-clean)
fi
"$VENV/python" "$ROOT/runner/benchmark_metadata.py" \
  --repo "$ROOT" \
  --harness "$ROOT/harnesses/pdsh" \
  --adapter "$ROOT/adapters/pdsh-keyten" \
  --machine-out "$MACHINE" \
  --metadata-out "$METADATA" \
  --samples "$SAMPLES" \
  --warmups "$WARMUPS" \
  --workers "$THREADS" \
  --benchmark-mode "$BENCHMARK_MODE" \
  "${CLEAN_ARGS[@]}"

if [ "${BENCH_PREPARE_ONLY:-false}" = true ]; then
  echo "prepared verified PDS-H snapshot and metadata at $RUN_DIR"
  exit 0
fi

if [ "${BENCH_SKIP_CORRECTNESS:-false}" != true ]; then
  CORRECTNESS="$WORK/pdsh-correctness"
  case "$CORRECTNESS" in
    "$WORK"/*) ;;
    *) echo "run_pdsh.sh: refusing unsafe correctness directory $CORRECTNESS" >&2; exit 1 ;;
  esac
  rm -rf "$CORRECTNESS"
  mkdir -p "$CORRECTNESS"
  for e in duckdb polars keyten; do
    streaming=false
    if [ "$BENCHMARK_MODE" = resident-native ]; then
      mode=in-memory
      case "$e" in
        keyten) mode=resident-native ;;
        polars) mode=streaming-resident; streaming=true ;;
      esac
    else
      mode=parquet-end-to-end
      if [ "$e" = polars ]; then
        mode=streaming-parquet-end-to-end
        streaming=true
      fi
    fi
    env \
      SCALE_FACTOR="$SCALE" \
      RUN_IO_TYPE="$IO_TYPE" \
      RUN_LOG_TIMINGS=false \
      RUN_PRE_RUN=false \
      RUN_ITERATIONS=1 \
      RUN_WORKERS="$THREADS" \
      RUN_CAPTURE_RESULTS=true \
      RUN_RESULT_DIR="$CORRECTNESS" \
      RUN_CHECK_RESULTS="$STORED_CHECK" \
      RUN_EXECUTION_MODE="$mode" \
      RUN_POLARS_STREAMING="$streaming" \
      POLARS_MAX_THREADS="$THREADS" \
      timeout 1800 "$VENV/python" -m "queries.$e"
  done
  "$VENV/python" "$ROOT/runner/check_pdsh_results.py" "$CORRECTNESS" \
    --report "$WORK/pdsh-correctness-report.json"
  if [ "${BENCH_CORRECTNESS_ONLY:-false}" = true ]; then
    echo "PDS-H correctness gate passed; timing skipped by request"
    exit 0
  fi
fi

# One timed sample per fresh process keeps each sample independent. Cyclic
# rotations put every engine in every thermal/order position.
for ((round = 0; round < SAMPLES; round++)); do
  case $((round % 3)) in
    0) order=(keyten polars duckdb) ;;
    1) order=(polars duckdb keyten) ;;
    2) order=(duckdb keyten polars) ;;
  esac
  position=0
  for e in "${order[@]}"; do
    position=$((position + 1))
    streaming=false
    if [ "$BENCHMARK_MODE" = resident-native ]; then
      mode=in-memory
      case "$e" in
        keyten) mode=resident-native ;;
        polars) mode=streaming-resident; streaming=true ;;
      esac
    else
      mode=parquet-end-to-end
      if [ "$e" = polars ]; then
        mode=streaming-parquet-end-to-end
        streaming=true
      fi
    fi
    env \
      SCALE_FACTOR="$SCALE" \
      RUN_IO_TYPE="$IO_TYPE" \
      RUN_LOG_TIMINGS=true \
      RUN_PRE_RUN=true \
      RUN_WARMUP_ITERATIONS="$WARMUPS" \
      RUN_ITERATIONS=1 \
      RUN_WORKERS="$THREADS" \
      RUN_BENCHMARK_RUN_ID="$round" \
      RUN_ORDER_POSITION="$position" \
      RUN_EXECUTION_MODE="$mode" \
      RUN_POLARS_STREAMING="$streaming" \
      POLARS_MAX_THREADS="$THREADS" \
      timeout 1800 "$VENV/python" -m "queries.$e"
  done
done

cd "$ROOT"
VER() { "$VENV/python" -c "import $1; print($1.__version__)"; }
for e in keyten polars duckdb; do
  python3 runner/convert_generic.py pdsh "$RUN_DIR/output/run/timings.csv" "$e" "$(VER "$e")" "$MACHINE" \
    "$RESULT_DIR/$e.json" "$METADATA"
done
