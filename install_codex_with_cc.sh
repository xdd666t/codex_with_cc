#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
RUNTIME_SCRIPT="$SCRIPT_DIR/codex_with_cc/scripts/install_codex_with_cc.py"
BOOTSTRAP_PYTHON="auto"
RUNTIME_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bootstrap-python)
      BOOTSTRAP_PYTHON="$2"
      shift 2
      ;;
    *)
      RUNTIME_ARGS+=("$1")
      shift
      ;;
  esac
done

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

add_homebrew_to_path() {
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ "$BOOTSTRAP_PYTHON" == "never" || "$BOOTSTRAP_PYTHON" == "Never" ]]; then
    print -u2 "Python 3.9+ is required but was not found, and --bootstrap-python never was requested."
    exit 1
  fi
  print -u2 "Python 3.9+ was not found; bootstrapping Python runtime for codex_with_cc."
  add_homebrew_to_path
  if ! command -v brew >/dev/null 2>&1; then
    print -u2 "Homebrew was not found; installing Homebrew using the official non-interactive installer."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >&2
    add_homebrew_to_path
  fi
  brew install python >&2
  PYTHON_BIN="$(find_python || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  print -u2 "Python bootstrap completed but Python 3.9+ is still not available on PATH."
  exit 1
fi
if [[ ! -f "$RUNTIME_SCRIPT" ]]; then
  print -u2 "Missing shared Python runtime: $RUNTIME_SCRIPT"
  exit 1
fi

exec "$PYTHON_BIN" "$RUNTIME_SCRIPT" "${RUNTIME_ARGS[@]}"
