#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENGINE_SCRIPT="$PROJECT_DIR/.claude/workflow/engine/session_start_core.py"
PYTHON_BIN=""

if [[ ! -f "$ENGINE_SCRIPT" ]]; then
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  exit 0
fi

exec "$PYTHON_BIN" "$ENGINE_SCRIPT" --project-dir "$PROJECT_DIR"
