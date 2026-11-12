#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x "/d/DepTooL/anaconda3/envs/ai-assistant/python.exe" ]]; then
  PY="/d/DepTooL/anaconda3/envs/ai-assistant/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "error=python_not_found" >&2
  exit 2
fi

if [[ "${1:-}" == "--check" ]]; then
  shift
  exec "$PY" "$ROOT/scripts/upgrade_rollback.py" check "$@"
fi

exec "$PY" "$ROOT/scripts/upgrade_rollback.py" "$@"
