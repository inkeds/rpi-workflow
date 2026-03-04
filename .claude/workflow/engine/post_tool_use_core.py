#!/usr/bin/env python3
"""PostToolUse hook core (Python)."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
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


def read_hook_input() -> Dict[str, Any]:
    raw = ""
    try:
        raw = os.read(0, 1_000_000).decode("utf-8", errors="ignore")
    except Exception:
        raw = ""
    if not raw.strip():
        raw = os.environ.get("CLAUDE_HOOK_INPUT", "") or os.environ.get("ANTHROPIC_HOOK_INPUT", "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def normalize_path(path: str) -> str:
    return (path or "").replace("\\", "/")


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


def str_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    return default


def extract_exit_code(payload: Dict[str, Any]) -> tuple[str, str]:
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict) and "exit_code" in tool_response:
        raw = tool_response.get("exit_code")
        raw_s = str_value(raw, "").strip()
        if raw_s and raw_s.lstrip("-").isdigit():
            return raw_s, "hook_payload"
        return "1", "fallback_non_numeric"

    result = payload.get("result")
    if isinstance(result, dict) and "exit_code" in result:
        raw = result.get("exit_code")
        raw_s = str_value(raw, "").strip()
        if raw_s and raw_s.lstrip("-").isdigit():
            return raw_s, "hook_payload"
        return "1", "fallback_non_numeric"

    return "1", "fallback_missing"


def bash_command_targets_specs(cmd: str) -> bool:
    normalized = normalize_path(cmd)
    return ".rpi-outfile/specs/" in normalized or bool(pre_core.re.search(r"(^|[ \t])specs/", normalized))


def bash_command_has_targeted_test_selector(cmd: str) -> bool:
    return bool(
        pre_core.re.search(
            r"(tests?/|__tests__/|\.test\.|\.spec\.|--testPathPattern|--testNamePattern|(^|[ \t])-t[ \t]|::)",
            cmd,
        )
    )


def append_event(log_dir: Path, event_log: Path, obj: Dict[str, Any]) -> None:
    if not log_dir.exists():
        return
    append_jsonl_line(event_log, json.dumps(obj, ensure_ascii=False))


def append_gate(log_dir: Path, gate_log: Path, obj: Dict[str, Any]) -> None:
    if not log_dir.exists():
        return
    append_jsonl_line(gate_log, json.dumps(obj, ensure_ascii=False))


def update_active_task_autonomy(current_task_file: Path, ts: str) -> None:
    if not current_task_file.exists():
        return

    def mutate_once() -> None:
        current_task = load_json_file(current_task_file)
        if not current_task:
            return
        task_id = str_value(current_task.get("task_id", "")).strip()
        status = str_value(current_task.get("status", "idle"), "idle")
        if not task_id or status != "in_progress":
            return

        autonomy = current_task.get("autonomy")
        if not isinstance(autonomy, dict):
            autonomy = {}

        event_count = int_value(autonomy.get("tool_event_count"), 0)
        if event_count < 0:
            event_count = 0
        autonomy["tool_event_count"] = event_count + 1
        autonomy["last_tool_event_at"] = ts
        current_task["autonomy"] = autonomy
        current_task["last_updated_at"] = ts
        write_json_atomic(current_task_file, current_task)

    with file_lock.exclusive_lock(current_task_file):
        mutate_once()


def main() -> int:
    parser = argparse.ArgumentParser(description="PostToolUse hook core")
    parser.add_argument("--project-dir", default="", help="Project root path")
    args = parser.parse_args()

    script_file = Path(__file__).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else resolve_project_dir(script_file.parent)
    output_dir = project_dir / ".rpi-outfile"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    config_dir = project_dir / ".claude" / "workflow" / "config"

    current_task_file = state_dir / "current_task.json"
    event_log = log_dir / "events.jsonl"
    gate_log = log_dir / "gate-results.jsonl"
    runtime_file = config_dir / "runtime.json"

    if not output_dir.exists():
        return 0
    payload = read_hook_input()
    tool_name = str_value(payload.get("tool_name", ""))
    ts = utc_now()

    if tool_name in {"Edit", "Write", "MultiEdit"}:
        target_path = normalize_path(str_value(((payload.get("tool_input") or {}).get("file_path", "")))
)
        append_event(
            log_dir,
            event_log,
            {
                "ts": ts,
                "event": "post_tool_use",
                "tool": tool_name,
                "path": target_path,
            },
        )
        update_active_task_autonomy(current_task_file, ts)
        return 0

    if tool_name != "Bash":
        return 0

    command = str_value(((payload.get("tool_input") or {}).get("command", ""))
)
    raw_exit = None
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        raw_exit = tool_response.get("exit_code")
    if raw_exit is None:
        result = payload.get("result")
        if isinstance(result, dict):
            raw_exit = result.get("exit_code")

    exit_code, exit_code_source = extract_exit_code(payload)
    raw_exit_s = str_value(raw_exit, "").strip()
    if raw_exit_s and not raw_exit_s.lstrip("-").isdigit():
        append_event(
            log_dir,
            event_log,
            {
                "ts": ts,
                "event": "post_tool_warn",
                "tool": tool_name,
                "command": command,
                "reason": "non-numeric exit_code in hook payload; fallback to 1",
                "raw_exit_code": raw_exit_s,
            },
        )
    elif not raw_exit_s:
        append_event(
            log_dir,
            event_log,
            {
                "ts": ts,
                "event": "post_tool_warn",
                "tool": tool_name,
                "command": command,
                "reason": "missing exit_code in hook payload; fallback to 1",
            },
        )

    mutates_repo = pre_core.bash_command_mutates_repo(command)
    targets_code = pre_core.bash_command_targets_code(command)
    targets_specs = bash_command_targets_specs(command)
    targets_tests = pre_core.bash_command_targets_tests(command)
    opaque_codegen = pre_core.bash_command_is_opaque_codegen(command)
    is_test_cmd = pre_core.bash_command_is_test_command(command)

    append_event(
        log_dir,
        event_log,
        {
            "ts": ts,
            "event": "post_tool_use",
            "tool": tool_name,
            "command": command,
            "exit_code": int(exit_code),
            "exit_code_source": exit_code_source,
            "mutates_repo": mutates_repo,
            "targets_code": targets_code,
            "targets_specs": targets_specs,
            "targets_tests": targets_tests,
            "opaque_codegen": opaque_codegen,
            "is_test_command": is_test_cmd,
        },
    )
    update_active_task_autonomy(current_task_file, ts)

    if not (is_test_cmd and current_task_file.exists()):
        return 0

    test_status = "pass" if exit_code == "0" else "fail"
    append_gate(
        log_dir,
        gate_log,
        {
            "ts": ts,
            "gate": "test_command",
            "command": command,
            "status": test_status,
            "exit_code": int(exit_code),
        },
    )

    runtime = load_json_file(runtime_file)
    allow_generic_red = bool_value(runtime.get("allow_generic_red", False), False)
    red_targeted = test_status == "fail" and (bash_command_has_targeted_test_selector(command) or allow_generic_red)

    current_task = load_json_file(current_task_file)
    if not current_task:
        return 0

    tdd = current_task.get("tdd")
    if not isinstance(tdd, dict):
        tdd = {}
        current_task["tdd"] = tdd

    tdd["latest_test_status"] = test_status
    tdd["last_test_command"] = command
    if test_status == "fail" and red_targeted:
        tdd["red_test_written"] = True
        tdd["red_test_targeted"] = True
        tdd["red_test_evidence"] = command
        tdd["red_test_at"] = ts
    current_task["last_updated_at"] = ts
    write_json_atomic(current_task_file, current_task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
