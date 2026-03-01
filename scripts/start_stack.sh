#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/start_stack.py"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python command not found in PATH." >&2
  exit 127
fi

exec "$PYTHON_BIN" "$PYTHON_SCRIPT" "$@"
