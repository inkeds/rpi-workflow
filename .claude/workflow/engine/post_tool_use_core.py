#!/usr/bin/env python3
"""PostToolUse hook core (Python)."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


EXIT_CODE_KEYS = {"exit_code", "exitCode", "return_code", "returnCode", "returncode"}
SUCCESS_TRUE_VALUES = {"ok", "pass", "passed", "success", "succeeded", "completed", "complete", "done"}
SUCCESS_FALSE_VALUES = {"error", "fail", "failed", "failure", "denied", "blocked", "timeout", "timed_out", "cancelled", "canceled"}
FAILURE_TEXT_RE = pre_core.re.compile(
    r"(^|[\r\n\t :])("
    r"error|failed|failure|traceback|exception|fatal|denied|blocked|unauthorized|forbidden|"
    r"no such file|not found|command not found|permission denied|syntax error|usage:|invalid input|"
    r"cannot |can't |timed out|timeout"
    r")($|[\r\n\t :])",
    pre_core.re.IGNORECASE,
)


def iter_exit_search_roots(payload: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
    preferred_keys = [
        "tool_response",
        "toolResponse",
        "tool_result",
        "toolResult",
        "result",
        "response",
        "output",
        "tool_output",
        "toolOutput",
        "hookSpecificOutput",
    ]
    seen: set[int] = set()
    for key in preferred_keys:
        value = payload.get(key)
        if value is None:
            continue
        obj_id = id(value)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        yield key, value
    yield "payload", payload


def find_key_values(value: Any, keys: set[str], path: str = "", depth: int = 0) -> List[Tuple[str, Any]]:
    if depth > 8:
        return []
    rows: List[Tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in keys:
                rows.append((child_path, child))
            rows.extend(find_key_values(child, keys, child_path, depth + 1))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_path = f"{path}[{idx}]"
            rows.extend(find_key_values(child, keys, child_path, depth + 1))
    return rows


def status_to_exit_code(raw: Any) -> Tuple[bool, str]:
    if isinstance(raw, bool):
        return True, "0" if raw else "1"
    raw_s = str_value(raw, "").strip().lower()
    if raw_s in SUCCESS_TRUE_VALUES:
        return True, "0"
    if raw_s in SUCCESS_FALSE_VALUES:
        return True, "1"
    return False, ""


def infer_exit_code_from_tool_response(payload: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    tool_response = payload.get("tool_response")
    if not isinstance(tool_response, dict):
        return None

    interrupted = bool_value(tool_response.get("interrupted"), False)
    if interrupted:
        return "1", "hook_tool_response:interrupted", "true"

    command = str_value(((payload.get("tool_input") or {}).get("command", ""))).strip()
    stdout = str_value(tool_response.get("stdout", "")).strip()
    stderr = str_value(tool_response.get("stderr", "")).strip()
    combined = "\n".join(part for part in (stderr, stdout) if part).strip()
    helpish_command = bool(pre_core.re.search(r"(^|[ \t])(--help|-h|help|--version|-V|version)([ \t]|$)", command))

    if combined and FAILURE_TEXT_RE.search(combined) and not helpish_command:
        return "1", "hook_tool_response:text_heuristic", combined[:200]
    if stderr and not stdout:
        return "1", "hook_tool_response:stderr_only", stderr[:200]
    return "0", "hook_tool_response:non_interrupted", stdout[:200] or stderr[:200]


def _extract_transcript_tool_error_once(payload: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    transcript_path = str_value(payload.get("transcript_path", "")).strip()
    tool_use_id = str_value(payload.get("tool_use_id", "")).strip()
    if not transcript_path or not tool_use_id:
        return None

    path = Path(transcript_path)
    if not path.is_file():
        return None

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    for raw_line in reversed(lines[-400:]):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            row = json.loads(raw_line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue

        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue
            if str_value(item.get("type", "")).strip() != "tool_result":
                continue
            if str_value(item.get("tool_use_id", "")).strip() != tool_use_id:
                continue
            if "is_error" not in item:
                continue
            is_error = bool_value(item.get("is_error"), False)
            return ("1" if is_error else "0"), f"transcript_tool_result:{tool_use_id}", str(is_error).lower()
    return None


def extract_transcript_tool_error(
    payload: Dict[str, Any], retries: int = 0, retry_delay_s: float = 0.0
) -> Optional[Tuple[str, str, str]]:
    for attempt in range(retries + 1):
        found = _extract_transcript_tool_error_once(payload)
        if found is not None:
            return found
        if attempt < retries and retry_delay_s > 0:
            time.sleep(retry_delay_s)
    return None


def extract_exit_code(payload: Dict[str, Any]) -> tuple[str, str, str]:
    first_non_numeric = ""
    first_non_numeric_path = ""

    for root_name, root_value in iter_exit_search_roots(payload):
        for path, raw in find_key_values(root_value, EXIT_CODE_KEYS, root_name):
            raw_s = str_value(raw, "").strip()
            if raw_s and raw_s.lstrip("-").isdigit():
                return raw_s, f"hook_payload:{path}", raw_s
            if raw_s and not first_non_numeric:
                first_non_numeric = raw_s
                first_non_numeric_path = path

    if first_non_numeric:
        return "1", f"fallback_non_numeric:{first_non_numeric_path}", first_non_numeric

    for root_name, root_value in iter_exit_search_roots(payload):
        for path, raw in find_key_values(root_value, {"success", "ok", "status", "state", "outcome", "is_error", "isError"}, root_name):
            key = path.rsplit(".", 1)[-1]
            if key in {"is_error", "isError"}:
                if isinstance(raw, bool):
                    return ("1" if raw else "0"), f"hook_payload_status:{path}", str(raw).lower()
                raw_s = str_value(raw, "").strip().lower()
                if raw_s in {"true", "false"}:
                    return ("1" if raw_s == "true" else "0"), f"hook_payload_status:{path}", raw_s
                continue
            matched, code = status_to_exit_code(raw)
            if matched:
                return code, f"hook_payload_status:{path}", str_value(raw, "").strip()

    transcript_exit = extract_transcript_tool_error(payload, retries=12, retry_delay_s=0.05)
    if transcript_exit is not None:
        return transcript_exit

    tool_response_exit = infer_exit_code_from_tool_response(payload)
    if tool_response_exit is not None:
        return tool_response_exit

    return "1", "fallback_missing", ""


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

    command = str_value(((payload.get("tool_input") or {}).get("command", "")))
    exit_code, exit_code_source, raw_exit_s = extract_exit_code(payload)
    if exit_code_source.startswith("fallback_non_numeric:"):
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
    elif exit_code_source == "fallback_missing":
        append_event(
            log_dir,
            event_log,
            {
                "ts": ts,
                "event": "post_tool_warn",
                "tool": tool_name,
                "command": command,
                "reason": "missing exit_code/status in hook payload; fallback to 1",
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
