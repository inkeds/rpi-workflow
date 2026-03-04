#!/usr/bin/env python3
"""SessionStart hook core (Python)."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pre_tool_use_core as pre_core


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_project_dir(start: Path) -> Path:
    cur = start.resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / ".claude" / "workflow").is_dir():
            return cand
    return cur


def load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def append_event(log_dir: Path, event_log: Path, payload: Dict[str, Any]) -> None:
    if not log_dir.exists():
        return
    pre_core.append_jsonl_line(event_log, json.dumps(payload, ensure_ascii=False))


def str_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="SessionStart hook core")
    parser.add_argument("--project-dir", default="", help="Project root path")
    args = parser.parse_args()

    script_file = Path(__file__).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else resolve_project_dir(script_file.parent)

    output_dir = project_dir / ".rpi-outfile"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    phase_file = state_dir / "project_phase.json"
    current_task_file = state_dir / "current_task.json"
    init_summary_file = state_dir / "init_summary.json"
    event_log = log_dir / "events.jsonl"

    phase_data = load_json_file(phase_file)
    phase = str_value(phase_data.get("phase", "M0"), "M0")
    ratio = str_value(phase_data.get("spec_ratio", "6:4"), "6:4")

    current_task = load_json_file(current_task_file)
    task_id = str_value(current_task.get("task_id", ""))
    status = str_value(current_task.get("status", "idle"), "idle")

    append_event(
        log_dir,
        event_log,
        {
            "ts": utc_now(),
            "event": "session_start",
            "phase": phase,
            "task_id": task_id,
            "status": status,
        },
    )

    if task_id and status != "idle":
        msg = (
            f"Workflow active. phase={phase} (Vibe:Spec {ratio}), task={task_id}, status={status}. "
            "Continue RPI and close with gate evidence."
        )
    else:
        init_summary = load_json_file(init_summary_file)
        init_phase = str_value(init_summary.get("init_phase", ""))
        if init_phase == "skeleton_generated":
            idea = str_value(init_summary.get("idea", ""))
            recommended = str_value(init_summary.get("recommended", "A"), "A")
            msg = (
                f'Workflow active. phase={phase} (Vibe:Spec {ratio}). Init in progress: skeleton generated but direction not confirmed. '
                f'Idea: "{idea}". Recommended direction: {recommended}. Resume with /rpi-init deepen.'
            )
        elif init_phase == "direction_confirmed":
            msg = (
                f"Workflow active. phase={phase} (Vibe:Spec {ratio}). Init completed, direction confirmed. "
                "Continue with /rpi-spec expand or /rpi-task start."
            )
        else:
            msg = f"Workflow active. phase={phase} (Vibe:Spec {ratio}). No active task. Start with /rpi-task start before editing code."

    # Keep SessionStart output schema-safe across CLI versions.
    print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
