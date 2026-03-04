#!/usr/bin/env python3
"""Stop hook core (Python)."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import file_lock
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


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def append_jsonl_line(path: Path, line: str) -> None:
    file_lock.append_line_locked(path, line)


def str_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off", ""}:
            return False
    return default


def int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    return default


def iso_to_epoch(iso: str) -> int:
    raw = (iso or "").strip()
    if not raw:
        return 0
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return int(datetime.fromisoformat(raw).timestamp())
    except Exception:
        return 0


def emit_stop_decision(decision: str, reason: str) -> None:
    payload = {
        "decision": decision,
        "reason": reason,
    }
    print(json.dumps(payload, ensure_ascii=False))


def append_event(log_dir: Path, event_log: Path, obj: Dict[str, Any]) -> None:
    if not log_dir.exists():
        return
    append_jsonl_line(event_log, json.dumps(obj, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop hook core")
    parser.add_argument("--project-dir", default="", help="Project root path")
    args = parser.parse_args()

    script_file = Path(__file__).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else resolve_project_dir(script_file.parent)

    output_dir = project_dir / ".rpi-outfile"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    config_dir = project_dir / ".claude" / "workflow" / "config"

    runtime_file = config_dir / "runtime.json"
    current_task_file = state_dir / "current_task.json"
    event_log = log_dir / "events.jsonl"
    stop_loop_file = state_dir / "stop_loop_state.json"

    runtime = load_json_file(runtime_file)
    current_task = load_json_file(current_task_file)

    max_stop_blocks = int_value(
        runtime.get("stop_loop_max_blocks"),
        int_value(os.environ.get("RPI_MAX_STOP_BLOCKS"), 10),
    )
    stop_loop_timeout_minutes = int_value(
        runtime.get("stop_loop_timeout_minutes"),
        int_value(os.environ.get("RPI_STOP_LOOP_TIMEOUT_MINUTES"), 30),
    )

    task_id = str_value(current_task.get("task_id", ""))
    status = str_value(current_task.get("status", "idle"), "idle")
    enforce = bool_value(current_task.get("enforce_stop_gate", True), True)

    def write_stop_state(task: str, count: int, ts: str) -> None:
        if not state_dir.exists():
            return
        write_json_atomic(stop_loop_file, {"task_id": task, "block_count": count, "last_block_at": ts})

    def reset_stop_state() -> None:
        write_stop_state("", 0, "")

    if not task_id or status == "idle" or not enforce:
        reset_stop_state()
        return 0

    tdd = current_task.get("tdd", {})
    if not isinstance(tdd, dict):
        tdd = {}
    quality_gate = current_task.get("quality_gate", {})
    if not isinstance(quality_gate, dict):
        quality_gate = {}

    tdd_ready = bool_value(tdd.get("red_test_written", False), False) and str_value(tdd.get("latest_test_status", "")) == "pass"
    gate_ready = str_value(quality_gate.get("last_run_status", "")) == "pass"

    stop_state = load_json_file(stop_loop_file)
    state_task = str_value(stop_state.get("task_id", ""))
    state_count = int_value(stop_state.get("block_count"), 0)
    state_ts = str_value(stop_state.get("last_block_at", ""))

    now_epoch = int(time.time())
    if state_task != task_id:
        state_count = 0

    if state_ts:
        last_epoch = iso_to_epoch(state_ts)
        if last_epoch > 0:
            elapsed = now_epoch - last_epoch
            if elapsed > stop_loop_timeout_minutes * 60:
                state_count = 0

    state_count += 1
    now_iso = utc_now()
    write_stop_state(task_id, state_count, now_iso)

    reason_parts = []
    if not tdd_ready:
        reason_parts.append("TDD evidence incomplete")
    if not gate_ready:
        reason_parts.append("quality gate not passed")

    reason = f"Active task {task_id} is still in progress"
    if reason_parts:
        reason = reason + "; " + "; ".join(reason_parts)
    reason += (
        ". Next: (1) /rpi-gates run to verify TDD+quality gates, "
        '(2) /rpi-task close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<note>", '
        '(3) /rpi-task pause "<reason>" /rpi-task abort "<reason>" for clean interruption.'
    )

    if state_count >= max_stop_blocks:
        append_event(
            log_dir,
            event_log,
            {
                "ts": now_iso,
                "event": "stop_loop_allow",
                "task_id": task_id,
                "attempts": state_count,
                "reason": reason,
            },
        )
        reset_stop_state()
        emit_stop_decision(
            "approve",
            f"Stop loop limit reached ({state_count}/{max_stop_blocks}) — allowing exit to prevent deadlock. "
            f"Task '{task_id}' remains in_progress. IMPORTANT: next session should /rpi-task close, /rpi-task pause, or /rpi-task abort first.",
        )
        return 0

    append_event(
        log_dir,
        event_log,
        {
            "ts": now_iso,
            "event": "stop_block",
            "task_id": task_id,
            "attempts": state_count,
            "max_attempts": max_stop_blocks,
            "reason": reason,
        },
    )
    emit_stop_decision("block", f"{reason} (stop block attempt {state_count}/{max_stop_blocks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
