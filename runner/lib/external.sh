# Optional server engines use separate, correctness-gated result directories.
EXTERNAL_RUNNER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/run_external.sh"
run_external_suite() {
  [ -n "${BENCH_EXTRA_ENGINES:-}" ] || return 0
  local suite="$1" data="$2" threads="$3"
  shift 3
  local options=()
  if [ "${BENCH_CORRECTNESS_ONLY:-false}" = true ]; then
    options+=(--correctness-only)
  fi
  "$EXTERNAL_RUNNER" "$suite" "$data" --engines "$BENCH_EXTRA_ENGINES" \
    --threads "$threads" "${options[@]}" "$@"
}
