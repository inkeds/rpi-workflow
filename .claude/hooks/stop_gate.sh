#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENGINE_SCRIPT="$PROJECT_DIR/.claude/workflow/engine/stop_gate_core.py"
PYTHON_BIN=""

emit_block() {
  local reason="$1"
  local escaped="$reason"
  escaped="${escaped//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  printf '{"decision":"block","reason":"%s"}\n' "$escaped"
}

if [[ ! -f "$ENGINE_SCRIPT" ]]; then
  emit_block "Stop gate core engine missing: .claude/workflow/engine/stop_gate_core.py"
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  emit_block "python3 or python is required for stop gate core. Install Python 3 and retry."
  exit 0
fi

exec "$PYTHON_BIN" "$ENGINE_SCRIPT" --project-dir "$PROJECT_DIR"
