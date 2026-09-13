#!/usr/bin/env bash
# Copyright (c) 2026 Rayforce Technologies Inc. Licensed under the MIT License.
# Call directly (not in a subshell) so the before hash survives until after.
# KEYTEN_PYTHON must name the interpreter used for the timed runs.
print_keyten_identity() {
  local phase="$1" binary digest
  binary="$("$KEYTEN_PYTHON" -c 'import keyten, os; print(os.path.join(os.path.dirname(keyten.__file__), "_keyten.abi3.so"))')" || exit 1
  digest="$(sha256sum "$binary")" || exit 1
  digest="${digest:0:64}"
  printf 'keyten binary sha256=%s wheel=%s phase=%s\n' "$digest" "${KEYTEN_WHEEL:-pypi}" "$phase"
  case "$phase" in
    before) KEYTEN_IDENTITY_BEFORE="$digest" ;;
    after)
      if [ "$digest" != "${KEYTEN_IDENTITY_BEFORE:-}" ]; then
        echo "keyten binary identity mismatch: before=${KEYTEN_IDENTITY_BEFORE:-missing} after=$digest" >&2
        exit 1
      fi
      ;;
    *) echo "invalid keyten identity phase: $phase" >&2; exit 1 ;;
  esac
}
