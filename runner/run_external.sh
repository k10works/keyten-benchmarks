#!/usr/bin/env bash
# Official Python clients, installed separately from the embedded-engine venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.work/external-venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c 'import questdb, l, duckdb, pyarrow' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install -r "$ROOT/runner/external-requirements.txt"
fi
exec "$VENV/bin/python" "$ROOT/runner/run_external.py" "$@"
