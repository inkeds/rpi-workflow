#!/usr/bin/env python3
"""UserPromptSubmit hook core (Python)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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


def _tail_jsonl(path: Path, *, max_lines: int, max_bytes: int = 65536) -> List[Dict[str, Any]]:
    if not path.exists() or max_lines < 1:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            read_bytes = min(size, max_bytes)
            handle.seek(-read_bytes, 2)
            payload = handle.read()
    except OSError:
        return []

    text = payload.decode("utf-8", errors="ignore")
    rows: List[Dict[str, Any]] = []
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
        if len(rows) >= max_lines:
            break
    return rows


def _recent_failure_window(event_log: Path, gate_log: Path, task_id: str, limit: int = 3) -> List[str]:
    max_items = max(1, limit)
    out: List[str] = []
    seen: set[str] = set()

    def push(item: str) -> None:
        text = item.strip()
        if not text or text in seen:
            return
        seen.add(text)
        out.append(text)

    for row in _tail_jsonl(gate_log, max_lines=40):
        if str(row.get("status", "")) != "fail":
            continue
        phase = str(row.get("phase", "")).strip()
        gate = str(row.get("gate", "unknown")).strip()
        reason = str(row.get("message", "")).strip()
        detail = f"gate:{gate}"
        if phase:
            detail += f" phase={phase}"
        if reason:
            detail += f" reason={reason[:100]}"
        push(detail)
        if len(out) >= max_items:
            return out

    for row in _tail_jsonl(event_log, max_lines=80):
        event_name = str(row.get("event", "")).strip()
        if event_name not in {"pre_tool_block", "stop_block", "quality_gate"}:
            continue
        row_task = str(row.get("task_id", "")).strip()
        if task_id and row_task and row_task != task_id:
            continue
        if event_name == "quality_gate":
            status = str(row.get("status", "")).strip()
            if status and status != "fail":
                continue
        reason = str(row.get("reason", "")).strip() or str(row.get("message", "")).strip()
        detail = f"event:{event_name}"
        if reason:
            detail += f" reason={reason[:100]}"
        push(detail)
        if len(out) >= max_items:
            return out
    return out


def _compact_phase_checklist(raw_text: str, max_lines: int = 24) -> str:
    lines: List[str] = []
    for raw in raw_text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        # Keep headings and actionable checklist bullets, skip verbose prose blocks.
        if stripped.startswith("#"):
            lines.append(stripped)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            lines.append(stripped)
        elif any(stripped.startswith(f"{idx}. ") for idx in range(1, 10)):
            lines.append(stripped)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def read_hook_input() -> Dict[str, Any]:
    raw = ""
    try:
        raw = pre_core.sys.stdin.read()
    except Exception:
        raw = ""
    if not raw.strip():
        raw = pre_core.os.environ.get("CLAUDE_HOOK_INPUT", "") or pre_core.os.environ.get("ANTHROPIC_HOOK_INPUT", "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="UserPromptSubmit hook core")
    parser.add_argument("--project-dir", default="", help="Project root path")
    args = parser.parse_args()

    script_file = Path(__file__).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else resolve_project_dir(script_file.parent)

    output_dir = project_dir / ".rpi-outfile"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    injection_dir = project_dir / ".claude" / "workflow" / "injections"
    runtime_file = project_dir / ".claude" / "workflow" / "config" / "runtime.json"
    phase_file = state_dir / "project_phase.json"
    current_task_file = state_dir / "current_task.json"
    event_log = log_dir / "events.jsonl"

    payload = read_hook_input()
    phase_data = load_json_file(phase_file)
    current_task = load_json_file(current_task_file)
    runtime = load_json_file(runtime_file)

    phase = str_value(phase_data.get("phase", "M0"), "M0")
    ratio = str_value(phase_data.get("spec_ratio", "6:4"), "6:4")
    phase_check_file = injection_dir / f"{phase.lower()}.md"

    task_id = str_value(current_task.get("task_id", ""))
    status = str_value(current_task.get("status", "idle"), "idle")
    phase_state = current_task.get("phase_state", {})
    current_action = str_value((phase_state or {}).get("current_action", "idle"), "idle")

    prompt_raw = str_value(payload.get("prompt", "") or payload.get("user_prompt", ""))
    prompt_excerpt = prompt_raw.replace("\n", " ")[:180]

    append_event(
        log_dir,
        event_log,
        {
            "ts": utc_now(),
            "event": "user_prompt_submit",
            "phase": phase,
            "task_id": task_id,
            "status": status,
            "prompt_excerpt": prompt_excerpt,
        },
    )

    message = (
        "[RPI Strong Injection]\n"
        f"- phase: {phase} (Vibe:Spec {ratio})\n"
        f"- active_task: {task_id if task_id else 'none'}\n"
        f"- task_status: {status}\n"
        f"- current_action: {current_action}\n"
        "- hard_rules:\n"
        "  1) Follow RPI: Requirement -> Plan -> Implement\n"
        "  2) Facts/Assumptions/Open Questions must be explicit\n"
        "  3) Do not implement without spec_refs\n"
        "  4) TDD first for code changes\n"
        "  5) Leave trace logs for every phase transition and gate"
    )

    context_refs = current_task.get("context_refs", [])
    if isinstance(context_refs, list):
        rows: List[str] = []
        for item in context_refs[:3]:
            item_s = str_value(item, "").strip()
            if item_s:
                rows.append(f"- {item_s}")
        if rows:
            message += "\n\n[Context Pack]\n" + "\n".join(rows)

    recent_failures = _recent_failure_window(event_log, output_dir / "logs" / "gate-results.jsonl", task_id, limit=3)
    if task_id and status != "idle" and recent_failures:
        message += "\n\n[Recent Failure Window]\n" + "\n".join([f"- {item}" for item in recent_failures])

    auto_inject_ux = bool_value(runtime.get("auto_inject_ux_context", False), False)
    auto_inject_linkage = bool_value(runtime.get("auto_inject_linkage_context", False), False)

    ux_spec = project_dir / ".rpi-outfile" / "specs" / "l0" / "ux-spec.md"
    ref_module = project_dir / ".rpi-outfile" / "specs" / "l0" / "reference-module.md"
    linkage_spec = project_dir / ".rpi-outfile" / "specs" / "l0" / "module-linkage.md"

    if auto_inject_ux:
        ux_rows = []
        if ux_spec.exists():
            ux_rows.append("- ux-spec.md (UX 交互标准)")
        if ref_module.exists():
            ux_rows.append("- reference-module.md (标杆模块)")
        if ux_rows:
            message += "\n\n[UX Context]\n" + "\n".join(ux_rows)

    if auto_inject_linkage and linkage_spec.exists():
        message += "\n\n[Linkage Context]\n- module-linkage.md (模块联动规范)"

    if phase_check_file.exists():
        try:
            with phase_check_file.open("r", encoding="utf-8") as handle:
                raw_checklist = handle.read()
            phase_checklist = _compact_phase_checklist(raw_checklist, max_lines=24)
            if phase_checklist:
                message += "\n\n[Phase Checklist]\n" + phase_checklist
        except OSError:
            pass

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
