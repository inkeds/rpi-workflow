#!/usr/bin/env python3
"""Translate platform hook payloads into the existing RPI hook core contract."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


CORE_BY_EVENT = {
    "SessionStart": "session_start_core.py",
    "UserPromptSubmit": "user_prompt_submit_core.py",
    "PreToolUse": "pre_tool_use_core.py",
    "PostToolUse": "post_tool_use_core.py",
    "Stop": "stop_gate_core.py",
}


def normalize_codex_payload(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    if tool_name in {"exec_command", "shell", "command"}:
        normalized["tool_name"] = "Bash"
        if isinstance(tool_input, dict):
            normalized["tool_input"] = {
                "command": tool_input.get("cmd") or tool_input.get("command") or "",
                **tool_input,
            }
    elif tool_name in {"apply_patch", "patch"}:
        normalized["tool_name"] = "Edit"
        normalized["tool_input"] = tool_input if isinstance(tool_input, dict) else {"patch": str(tool_input)}
    elif tool_name:
        normalized["tool_name"] = tool_name
        normalized["tool_input"] = tool_input
    normalized.setdefault("hook_event_name", event)
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RPI hook protocol bridge")
    parser.add_argument("--platform", choices=("codex", "claude"), required=True)
    parser.add_argument("--event", choices=tuple(CORE_BY_EVENT), required=True)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if args.platform == "codex":
        payload = normalize_codex_payload(args.event, payload)

    project_dir = args.project_dir.resolve()
    core = project_dir / ".claude" / "workflow" / "engine" / CORE_BY_EVENT[args.event]
    if not core.exists():
        print(json.dumps({"systemMessage": f"RPI hook core missing: {core}"}, ensure_ascii=False))
        return 0

    env = dict(os.environ)
    env["RPI_HOOK_PLATFORM"] = args.platform
    completed = subprocess.run(
        [sys.executable, str(core), "--project-dir", str(project_dir)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
