#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
WORKFLOW_ROOT="${SCRIPT_DIR:h}"
PYTHON_SCRIPT="${1:-}"
if [[ -z "$PYTHON_SCRIPT" ]]; then
  print -u2 "Missing Python script name."
  exit 2
fi
shift
RUNTIME_SCRIPT="$WORKFLOW_ROOT/scripts/$PYTHON_SCRIPT"

if [[ ! -f "$RUNTIME_SCRIPT" ]]; then
  print -u2 "Missing shared Python runtime: $RUNTIME_SCRIPT"
  exit 1
fi

find_python() {
  local candidate
  for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        print -r -- "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  print -u2 "Python 3.9+ was not found. Install Python and expose python3 on PATH, then retry the codex-with-cc plugin command."
  exit 1
fi

exec "$PYTHON_BIN" "$RUNTIME_SCRIPT" "$@"
