#!/usr/bin/env python3
"""Task flow engine for profile/start/close/gates-auto.

Subcommands:
- profile [list|show|apply <profile>|<profile>]
- start [args...]
- close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<note>"
- gates-auto [M0|M1|M2] [--max-retries N] [--auto-fix|--no-auto-fix] [--quiet]
- quality-gate [M0|M1|M2]
- artifact-status [--json]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import file_lock
import guardrails_tool as guardrails


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_compact_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_project_dir(start: Path) -> Path:
    cur = start.resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / ".claude" / "workflow").is_dir():
            return cand
    return cur


def normalize_path(text: str) -> str:
    return (text or "").replace("\\", "/")


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_json_obj(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, data: Any) -> None:
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


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=False)
    file_lock.append_line_locked(path, line)


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
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"-?[0-9]+", stripped):
            return int(stripped)
    return default


def str_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def normalize_phase(raw: str) -> str:
    v = (raw or "").upper().strip().replace(" ", "")
    return v if v in {"M0", "M1", "M2"} else ""


def normalize_task_id(raw: str) -> str:
    v = (raw or "").upper().strip().replace(" ", "")
    if not v:
        return ""
    m = re.fullmatch(r"TASK-?0*([0-9]{1,4})", v)
    if m:
        return f"TASK-{int(m.group(1)):03d}"
    m = re.fullmatch(r"T0*([0-9]{1,4})", v)
    if m:
        return f"TASK-{int(m.group(1)):03d}"
    m = re.fullmatch(r"0*([0-9]{1,4})", v)
    if m:
        return f"TASK-{int(m.group(1)):03d}"
    if re.fullmatch(r"[A-Z0-9_-]+", v):
        return v
    return ""


def phase_ratio(phase: str) -> str:
    if phase == "M0":
        return "6:4"
    if phase == "M1":
        return "3:7"
    if phase == "M2":
        return "2:8"
    return "3:7"


@dataclass
class Paths:
    project_dir: Path
    workflow_dir: Path
    config_dir: Path
    output_dir: Path
    spec_dir: Path
    state_dir: Path
    log_dir: Path
    phase_file: Path
    current_task_file: Path
    task_stack_file: Path
    runtime_file: Path
    event_log: Path
    gate_log: Path
    profiles_dir: Path


def build_paths(project_dir: Path) -> Paths:
    workflow_dir = project_dir / ".claude" / "workflow"
    config_dir = workflow_dir / "config"
    output_dir = project_dir / ".rpi-outfile"
    spec_dir = output_dir / "specs"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    return Paths(
        project_dir=project_dir,
        workflow_dir=workflow_dir,
        config_dir=config_dir,
        output_dir=output_dir,
        spec_dir=spec_dir,
        state_dir=state_dir,
        log_dir=log_dir,
        phase_file=state_dir / "project_phase.json",
        current_task_file=state_dir / "current_task.json",
        task_stack_file=state_dir / "task_stack.json",
        runtime_file=config_dir / "runtime.json",
        event_log=log_dir / "events.jsonl",
        gate_log=log_dir / "gate-results.jsonl",
        profiles_dir=config_dir / "profiles",
    )


def default_runtime() -> Dict[str, Any]:
    return {
        "profile_name": "balanced-enterprise",
        "harness_enabled": True,
        "strict_mode": False,
        "start_require_ready": False,
        "close_require_spec_sync": False,
        "allow_generic_red": True,
        "risk_matrix_enabled": True,
        "risk_profile_override": "",
        "autonomy_budget_mode": "warn",
        "autonomy_max_minutes": 240,
        "autonomy_max_tool_events": 300,
        "architecture_enforce": False,
        "architecture_require_rules": False,
        "architecture_scan_max_files": 2000,
        "architecture_scan_exclude_dirs": [
            ".git",
            "node_modules",
            "vendor",
            "dist",
            "build",
            ".next",
            "coverage",
            "tmp",
            ".venv",
            "venv",
            "__pycache__",
        ],
        "spec_state_required": True,
        "spec_link_enforce": False,
        "risk_high_requires_approval": True,
        "gates_auto_retry_enabled": True,
        "gates_auto_retry_max": 3,
        "gates_auto_fix_on_fail": True,
        "auto_rpi_enabled": False,
        "auto_rpi_max_rounds": 1,
        "auto_rpi_max_minutes": 20,
        "auto_rpi_max_failures": 1,
        "auto_rpi_max_tool_events": 120,
        "auto_rpi_auto_fix": False,
        "agent_memory_auto_update": True,
        "agent_review_enabled": True,
        "a2a_auto_merge_non_core": True,
        "a2a_allow_commit": False,
        "audit_report_enabled": True,
        "anti_entropy_auto_fix": False,
        "trace_grade_required": False,
        "audit_pack_required_on_close": False,
        "mvp_coverage_threshold_a": 40,
        "mvp_coverage_threshold_b": 80,
        "mvp_coverage_threshold_c": 100,
        "mvp_low_confidence_ratio_max": 30,
        "ddd_lite_mode": "warn",
        "ddd_min_glossary_terms": 6,
        "ddd_min_bounded_contexts": 2,
        "ddd_min_invariants": 3,
        "mvp_priority_override_mode": "warn",
        "mvp_weighted_coverage_tolerance": 10,
        "mvp_max_promote_non_core": 1,
        "precode_guard_mode": "warn",
        "tdd_mode": "recommended",
        "tdd_exempt_path_regex": r"(^|/)(infra|ops|scripts|migrations|docker|\.github)/|(^|/)Dockerfile$|\.ya?ml$|\.toml$|\.ini$",
        "tdd_exempt_command_regex": r"(^|[[:space:]])(docker|kubectl|terraform|ansible|helm)([[:space:]]|$)",
        "stop_loop_max_blocks": 4,
        "stop_loop_timeout_minutes": 30,
        "frontend_ux_strict": False,
        "require_ux_spec": False,
        "require_linkage_spec": False,
        "require_reference_module": False,
        "auto_inject_ux_context": True,
        "auto_inject_linkage_context": True,
        "linkage_strict_mode": False,
    }


def write_idle_task(paths: Paths, phase: str = "M0") -> None:
    payload = {
        "task_id": "",
        "phase": phase,
        "status": "idle",
        "enforce_stop_gate": True,
        "spec_refs": [],
        "context_refs": [],
        "notes": [],
        "phase_state": {"current_action": "idle", "next_actions": []},
        "classification": {"root_cause": "unknown", "note": ""},
        "tdd": {
            "red_test_written": False,
            "red_test_targeted": False,
            "red_test_evidence": "",
            "red_test_at": "",
            "latest_test_status": "unknown",
            "last_test_command": "",
        },
        "quality_gate": {
            "last_run_status": "unknown",
            "last_run_phase": "",
            "last_run_at": "",
            "last_verify_status": "unknown",
            "last_verify_count": 0,
        },
        "autonomy": {
            "tool_event_count": 0,
            "last_tool_event_at": "",
        },
        "guardrails": {"precode": {"status": "unknown", "signature": "", "verified_at": "", "note": ""}},
        "created_at": "",
        "last_updated_at": utc_now(),
        "owner": "",
    }
    write_json_atomic(paths.current_task_file, payload)


def ensure_layout(paths: Paths) -> None:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.spec_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    if not paths.phase_file.exists():
        write_json_atomic(paths.phase_file, {"phase": "M0", "spec_ratio": "6:4", "updated_at": utc_now()})

    if not paths.current_task_file.exists():
        write_idle_task(paths, "M0")

    if not paths.task_stack_file.exists():
        write_json_atomic(paths.task_stack_file, [])

    if not paths.runtime_file.exists():
        write_json_atomic(paths.runtime_file, default_runtime())

    if not paths.event_log.exists():
        paths.event_log.touch()
    if not paths.gate_log.exists():
        paths.gate_log.touch()


def load_runtime(paths: Paths) -> Dict[str, Any]:
    return read_json_obj(paths.runtime_file)


def runtime_get(runtime: Dict[str, Any], key: str, default: Any) -> Any:
    val = runtime.get(key, default)
    return default if val is None else val


def append_event(paths: Paths, event: Dict[str, Any]) -> None:
    append_jsonl(paths.event_log, event)


def append_gate(paths: Paths, row: Dict[str, Any]) -> None:
    append_jsonl(paths.gate_log, row)


def deep_merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        merged = copy.deepcopy(a)
        for k, v in b.items():
            if k in merged:
                merged[k] = deep_merge(merged[k], v)
            else:
                merged[k] = copy.deepcopy(v)
        return merged
    return copy.deepcopy(b)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_python_capture(argv: List[str], cwd: Path) -> Tuple[int, str, str]:
    def decode_output(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, (bytes, bytearray)):
            return str(raw)
        data = bytes(raw)
        encodings: List[str] = []
        env_enc = os.environ.get("PYTHONIOENCODING", "").split(":", 1)[0].strip()
        if env_enc:
            encodings.append(env_enc)
        preferred = locale.getpreferredencoding(False)
        if preferred:
            encodings.append(preferred)
        encodings.extend(["utf-8", "utf-8-sig", "gb18030", "cp936", "cp1252"])

        seen = set()
        ordered = []
        for enc in encodings:
            key = enc.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(enc)

        for enc in ordered:
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
            except LookupError:
                continue
        return data.decode("utf-8", errors="replace")

    proc = subprocess.run(
        argv,
        check=False,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    return int(proc.returncode), decode_output(proc.stdout), decode_output(proc.stderr)


def resolve_shell_argv(command: str) -> List[str]:
    if shutil.which("bash"):
        return ["bash", "-lc", command]
    if shutil.which("sh"):
        return ["sh", "-lc", command]
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", command]
    return ["sh", "-lc", command]


def resolve_python_executable() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def automation_engine_path(paths: Paths) -> Path:
    return paths.workflow_dir / "engine" / "automation_tool.py"


def run_automation_capture(paths: Paths, subcommand: str, args: List[str]) -> Tuple[int, str, str]:
    engine = automation_engine_path(paths)
    if not engine.exists():
        return 127, "", f"missing automation engine: {engine}"
    exe = resolve_python_executable()
    return run_python_capture(
        [exe, str(engine), "--project-dir", str(paths.project_dir), subcommand, *args],
        cwd=paths.project_dir,
    )


def run_task_flow_capture(paths: Paths, subcommand: str, args: List[str]) -> Tuple[int, str, str]:
    exe = resolve_python_executable()
    return run_python_capture(
        [exe, str(Path(__file__).resolve()), "--project-dir", str(paths.project_dir), subcommand, *args],
        cwd=paths.project_dir,
    )


def parse_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return []
    return rows


def split_csv(raw: str) -> List[str]:
    parts = [x.strip() for x in (raw or "").split(",")]
    return [x for x in parts if x]


def _ref_key(raw: str) -> str:
    value = str_value(raw, "").strip()
    if not value:
        return ""
    return value.split("#", 1)[0].strip().lower()


def compact_ref_list(items: Sequence[str], max_items: int = 3) -> List[str]:
    limit = max(1, int_value(max_items, 3))
    out: List[str] = []
    idx_by_key: Dict[str, int] = {}
    for raw in items:
        ref = str_value(raw, "").strip()
        if not ref:
            continue
        key = _ref_key(ref)
        if not key:
            continue
        if key in idx_by_key:
            existing_idx = idx_by_key[key]
            if "#" not in out[existing_idx] and "#" in ref:
                out[existing_idx] = ref
            continue
        idx_by_key[key] = len(out)
        out.append(ref)
        if len(out) >= limit:
            break
    return out


def minimal_context_refs(spec_refs: Sequence[str], context_refs: Sequence[str], max_items: int = 3) -> List[str]:
    merged: List[str] = []
    for item in spec_refs:
        merged.append(str_value(item, ""))
    for item in context_refs:
        merged.append(str_value(item, ""))
    compact = compact_ref_list(merged, max_items=max_items)
    if compact:
        return compact
    fallback = compact_ref_list(context_refs, max_items=max_items)
    if fallback:
        return fallback
    return compact_ref_list(spec_refs, max_items=max_items)


def _task_capsule_dir(paths: Paths) -> Path:
    return paths.state_dir / "context"


def _portable_contract_dir(paths: Paths) -> Path:
    return paths.state_dir / "portable"


def _read_discovery_contract_summary(paths: Paths) -> Dict[str, Any]:
    state_file = paths.state_dir / "spec" / "state.json"
    source: Dict[str, Any] = {}
    if state_file.exists():
        source = read_json_obj(state_file)

    discovery = source.get("discovery", {})
    if not isinstance(discovery, dict):
        discovery = {}
    fields = discovery.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}

    def list_field(name: str) -> List[str]:
        raw = fields.get(name, [])
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for item in raw:
            text = str_value(item, "").strip()
            if text:
                out.append(text)
        return out

    return {
        "direction": str_value(fields.get("direction", ""), ""),
        "abc_scope": str_value(fields.get("abc_scope", ""), ""),
        "coverage_target": str_value(fields.get("coverage_target", ""), ""),
        "weighted_coverage_target": str_value(fields.get("weighted_coverage_target", ""), ""),
        "m0_must": list_field("m0_must"),
        "m0_wont": list_field("m0_wont"),
        "success_metrics": list_field("success_metrics"),
    }


def _phase_gate_policy(paths: Paths, phase: str) -> Dict[str, Any]:
    cfg = read_json_obj(paths.config_dir / "gates.json")
    phase_gates = ((cfg.get("phase_gates") or {}).get(phase) or [])
    verify_cfg = cfg.get("verify", {})
    verify_default = (verify_cfg.get("default") if isinstance(verify_cfg, dict) else []) or []
    verify_phase = (verify_cfg.get(phase) if isinstance(verify_cfg, dict) else []) or []
    commands = cfg.get("commands", {})
    if not isinstance(commands, dict):
        commands = {}

    def normalize_verify(items: Any, prefix: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        if not isinstance(items, list):
            return rows
        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                name = str_value(item.get("name", ""), "") or f"{prefix}_{idx}"
                command = str_value(item.get("command", ""), "")
            else:
                name = f"{prefix}_{idx}"
                command = str_value(item, "")
            if command:
                rows.append({"name": name, "command": command})
        return rows

    verify_rows = normalize_verify(verify_default, "verify_default") + normalize_verify(verify_phase, f"verify_{phase}")
    gate_rows: List[Dict[str, str]] = []
    if isinstance(phase_gates, list):
        for item in phase_gates:
            if isinstance(item, dict):
                name = str_value(item.get("name", ""), "")
                command = str_value(item.get("command", ""), "")
            else:
                name = str_value(item, "")
                command = str_value(commands.get(name, ""), "")
            if not name:
                continue
            gate_rows.append({"name": name, "command": command or "__REQUIRED__"})

    return {"verify": verify_rows, "phase_gates": gate_rows}


def _portable_evidence_template() -> Dict[str, Any]:
    return {
        "version": 1,
        "task_id": "",
        "phase": "M0",
        "summary": "",
        "spec_refs": [],
        "changes": [
            {
                "path": "",
                "type": "code|test|spec|config",
                "linked_spec_ref": "",
                "reason": "",
            }
        ],
        "tdd_evidence": {
            "red": {
                "command": "",
                "status": "fail",
                "excerpt": "",
            },
            "green": {
                "command": "",
                "status": "pass",
                "excerpt": "",
            },
        },
        "gate_evidence": {
            "command": "bash .claude/workflow/rpi.sh gates run M0",
            "status": "pass|fail",
            "failed_gates": [],
        },
        "trace": {
            "event_refs": [],
            "gate_refs": [],
        },
        "decision": {
            "result": "pass|fail",
            "root_cause": "spec_missing|execution_deviation|both|unknown",
            "note": "",
        },
    }


def _mutation_policy(paths: Paths, task_id: str) -> Dict[str, Any]:
    task_scope_prefix = f".rpi-outfile/specs/tasks/{task_id}" if task_id else ".rpi-outfile/specs/tasks"
    return {
        "allow_write_prefixes": [
            "src/",
            "app/",
            "apps/",
            "packages/",
            "lib/",
            "server/",
            "backend/",
            "frontend/",
            "tests/",
            task_scope_prefix,
            ".rpi-outfile/specs/l0/",
            ".rpi-outfile/specs/phases/",
            ".rpi-outfile/state/",
        ],
        "deny_write_prefixes": [
            ".git/",
            ".claude/workflow/engine/",
            ".claude/workflow/config/profiles/",
        ],
        "deny_write_files": [
            ".claude/workflow/config/runtime.json",
            ".claude/workflow/config/gates.json",
        ],
    }


def write_portable_contract(
    paths: Paths,
    task_payload: Dict[str, Any],
    *,
    transition: str,
    reason: str = "",
    result: str = "",
    root_cause: str = "",
    note: str = "",
) -> Path:
    runtime = load_runtime(paths)
    phase = normalize_phase(str_value(task_payload.get("phase", ""), "")) or normalize_phase(
        str_value(read_json_obj(paths.phase_file).get("phase", "M0"), "M0")
    )
    if phase not in {"M0", "M1", "M2"}:
        phase = "M0"
    task_id = str_value(task_payload.get("task_id", ""), "")
    status = str_value(task_payload.get("status", "idle"), "idle")

    spec_refs_raw = task_payload.get("spec_refs", [])
    context_refs_raw = task_payload.get("context_refs", [])
    spec_refs = compact_ref_list(spec_refs_raw if isinstance(spec_refs_raw, list) else [], max_items=3)
    context_refs = minimal_context_refs(
        spec_refs,
        context_refs_raw if isinstance(context_refs_raw, list) else [],
        max_items=3,
    )

    gate_policy = _phase_gate_policy(paths, phase)
    discovery = _read_discovery_contract_summary(paths)

    contract = {
        "contract_version": 1,
        "generated_at": utc_now(),
        "transition": transition,
        "task": {
            "task_id": task_id,
            "phase": phase,
            "status": status,
            "owner": str_value(task_payload.get("owner", ""), ""),
            "created_at": str_value(task_payload.get("created_at", ""), ""),
            "last_updated_at": str_value(task_payload.get("last_updated_at", ""), ""),
        },
        "goal_scope": {
            "direction": str_value(discovery.get("direction", ""), ""),
            "abc_scope": str_value(discovery.get("abc_scope", ""), ""),
            "coverage_target": str_value(discovery.get("coverage_target", ""), ""),
            "weighted_coverage_target": str_value(discovery.get("weighted_coverage_target", ""), ""),
            "m0_must": discovery.get("m0_must", []),
            "m0_wont": discovery.get("m0_wont", []),
            "success_metrics": discovery.get("success_metrics", []),
        },
        "context_budget": {
            "spec_refs": spec_refs,
            "context_refs": context_refs,
            "max_refs": 3,
        },
        "workflow_policy": {
            "required_flow": ["Requirement", "Plan", "Implement"],
            "must_bind_spec_refs_before_code_edit": True,
            "tdd_mode": str_value(runtime_get(runtime, "tdd_mode", "recommended"), "recommended"),
            "precode_guard_mode": str_value(runtime_get(runtime, "precode_guard_mode", "warn"), "warn"),
            "strict_mode": bool_value(runtime_get(runtime, "strict_mode", False), False),
            "start_require_ready": bool_value(runtime_get(runtime, "start_require_ready", False), False),
            "close_require_spec_sync": bool_value(runtime_get(runtime, "close_require_spec_sync", False), False),
            "architecture_enforce": bool_value(runtime_get(runtime, "architecture_enforce", False), False),
            "spec_link_enforce": bool_value(runtime_get(runtime, "spec_link_enforce", False), False),
        },
        "gate_policy": gate_policy,
        "mutation_policy": _mutation_policy(paths, task_id),
        "evidence_requirements": [
            "Provide failing test evidence before production code changes when tdd_mode != off.",
            "Provide passing test/gate evidence before task close.",
            "Every code change must reference one spec_ref or context_ref.",
            "Do not expand scope beyond m0_must without explicit phase/direction update.",
        ],
        "handoff": {
            "reason": reason,
            "result": result,
            "root_cause": root_cause,
            "note": note,
            "event_log": ".rpi-outfile/logs/events.jsonl",
            "gate_log": ".rpi-outfile/logs/gate-results.jsonl",
            "task_capsule": ".rpi-outfile/state/context/task_capsule.json",
            "evidence_template": ".rpi-outfile/state/portable/evidence_template.json",
        },
    }
    contract_hash = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    contract["contract_hash"] = contract_hash

    contract_dir = _portable_contract_dir(paths)
    contract_dir.mkdir(parents=True, exist_ok=True)
    contract_file = contract_dir / "contract.latest.json"
    write_json_atomic(contract_file, contract)
    write_json_atomic(contract_dir / "evidence_template.json", _portable_evidence_template())
    write_json_atomic(
        contract_dir / "README.json",
        {
            "contract_file": ".rpi-outfile/state/portable/contract.latest.json",
            "evidence_template": ".rpi-outfile/state/portable/evidence_template.json",
            "note": "Portable contract package for external AI/coding tools.",
        },
    )
    return contract_file


def _recent_failure_window(paths: Paths, task_id: str, limit: int = 3) -> List[str]:
    window: List[str] = []
    max_items = max(1, int_value(limit, 3))
    seen: set[str] = set()

    def push(item: str) -> None:
        text = item.strip()
        if not text:
            return
        if text in seen:
            return
        seen.add(text)
        window.append(text)

    for row in reversed(parse_jsonl(paths.gate_log)):
        if str_value(row.get("status", ""), "") != "fail":
            continue
        gate = str_value(row.get("gate", ""), "unknown")
        phase = str_value(row.get("phase", ""), "")
        msg = str_value(row.get("message", ""), "")
        detail = f"gate:{gate}"
        if phase:
            detail += f" phase={phase}"
        if msg:
            detail += f" reason={msg}"
        push(detail)
        if len(window) >= max_items:
            return window

    for row in reversed(parse_jsonl(paths.event_log)):
        event_name = str_value(row.get("event", ""), "")
        if event_name not in {"pre_tool_block", "stop_block", "quality_gate"}:
            continue
        row_task = str_value(row.get("task_id", ""), "")
        if task_id and row_task and row_task != task_id:
            continue
        status = str_value(row.get("status", ""), "")
        if event_name == "quality_gate" and status and status != "fail":
            continue
        reason = str_value(row.get("reason", ""), "") or str_value(row.get("message", ""), "")
        detail = f"event:{event_name}"
        if reason:
            detail += f" reason={reason[:120]}"
        push(detail)
        if len(window) >= max_items:
            return window
    return window


def write_task_capsule(
    paths: Paths,
    task_payload: Dict[str, Any],
    *,
    transition: str,
    reason: str = "",
    result: str = "",
    root_cause: str = "",
    note: str = "",
    spec_sync_status: str = "",
    code_edit_events: int = 0,
    spec_edit_events: int = 0,
) -> Path:
    cap_dir = _task_capsule_dir(paths)
    cap_dir.mkdir(parents=True, exist_ok=True)
    capsule_file = cap_dir / "task_capsule.json"

    task_id = str_value(task_payload.get("task_id", ""), "")
    phase = str_value(task_payload.get("phase", "M0"), "M0")
    status = str_value(task_payload.get("status", "idle"), "idle")
    spec_refs_raw = task_payload.get("spec_refs", [])
    context_refs_raw = task_payload.get("context_refs", [])
    spec_refs = compact_ref_list(spec_refs_raw if isinstance(spec_refs_raw, list) else [], max_items=3)
    context_refs = minimal_context_refs(
        spec_refs,
        context_refs_raw if isinstance(context_refs_raw, list) else [],
        max_items=3,
    )
    phase_state = task_payload.get("phase_state", {})
    if not isinstance(phase_state, dict):
        phase_state = {}
    quality_gate = task_payload.get("quality_gate", {})
    if not isinstance(quality_gate, dict):
        quality_gate = {}
    tdd = task_payload.get("tdd", {})
    if not isinstance(tdd, dict):
        tdd = {}

    capsule = {
        "version": 1,
        "generated_at": utc_now(),
        "transition": transition,
        "reason": reason,
        "task": {
            "task_id": task_id,
            "phase": phase,
            "status_before_cleanup": status,
            "owner": str_value(task_payload.get("owner", ""), ""),
            "created_at": str_value(task_payload.get("created_at", ""), ""),
        },
        "scope": {
            "spec_refs": spec_refs,
            "context_refs": context_refs,
            "current_action": str_value(phase_state.get("current_action", ""), ""),
            "next_actions": phase_state.get("next_actions", []) if isinstance(phase_state.get("next_actions"), list) else [],
        },
        "quality": {
            "result": result,
            "root_cause": root_cause,
            "note": note,
            "spec_sync_status": spec_sync_status,
            "code_edit_events": int_value(code_edit_events, 0),
            "spec_edit_events": int_value(spec_edit_events, 0),
            "tdd_latest_status": str_value(tdd.get("latest_test_status", "unknown"), "unknown"),
            "quality_gate_status": str_value(quality_gate.get("last_run_status", "unknown"), "unknown"),
        },
        "failure_window": _recent_failure_window(paths, task_id, limit=3),
    }
    write_json_atomic(capsule_file, capsule)
    return capsule_file


def has_substantive_lines(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if re.fullmatch(r"[-*]\s*", line):
            continue
        if re.fullmatch(r"[0-9]+\.\s*", line):
            continue
        if line in {"待输入", "待确认", "TBD", "N/A", "-", "TODO"}:
            continue
        return True
    return False


def has_task_ids(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return re.search(r"task[ -]?0*[0-9]{1,4}", text, flags=re.IGNORECASE) is not None


def cmd_artifact_status(paths: Paths, argv: Sequence[str]) -> int:
    output_json = False
    for token in argv:
        if token == "--json":
            output_json = True
        elif token in {"--help", "-h"}:
            print("Usage: bash .claude/workflow/rpi.sh check artifact [--json]")
            print("")
            print("Evaluate L0 artifact lifecycle state:")
            print("- blocked: dependency not satisfied")
            print("- ready: dependencies satisfied but artifact not done")
            print("- done: artifact completed and passes minimal checks")
            return 0

    project = paths.project_dir
    artifacts = [
        {"id": "mvp_skeleton", "file": ".rpi-outfile/specs/l0/mvp-skeleton.md", "deps": []},
        {"id": "discovery", "file": ".rpi-outfile/specs/l0/discovery.md", "deps": []},
        {"id": "epic", "file": ".rpi-outfile/specs/l0/epic.md", "deps": ["discovery"]},
        {"id": "spec", "file": ".rpi-outfile/specs/l0/spec.md", "deps": ["discovery"]},
        {"id": "milestones", "file": ".rpi-outfile/specs/l0/milestones.md", "deps": ["discovery"]},
        {"id": "tasks", "file": ".rpi-outfile/specs/l0/tasks.md", "deps": ["discovery"]},
        {"id": "spec_state", "file": ".rpi-outfile/state/spec/state.json", "deps": ["discovery", "spec", "tasks"]},
    ]
    required_for_start = ["discovery", "spec", "tasks", "spec_state"]
    status_map: Dict[str, str] = {}
    issue_map: Dict[str, str] = {}

    for item in artifacts:
        aid = item["id"]
        rel_file = item["file"]
        deps = item["deps"]
        abs_file = project / rel_file

        blocked = False
        for dep in deps:
            if status_map.get(dep) != "done":
                status_map[aid] = "blocked"
                issue_map[aid] = f"dependency_not_done:{dep}"
                blocked = True
                break
        if blocked:
            continue

        if aid == "mvp_skeleton":
            if abs_file.exists():
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "missing_file"
            continue

        if aid == "discovery":
            if not abs_file.exists():
                status_map[aid] = "ready"
                issue_map[aid] = "missing_file"
            elif str_value(guardrails.check_discovery(project, quiet=True).get("status", "fail")) == "pass":
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "incomplete_content"
            continue

        if aid == "spec":
            if not abs_file.exists():
                status_map[aid] = "ready"
                issue_map[aid] = "missing_file"
            elif str_value(guardrails.check_contract_spec(project, quiet=True).get("status", "fail")) == "pass":
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "incomplete_contract"
            continue

        if aid == "tasks":
            if not abs_file.exists():
                status_map[aid] = "ready"
                issue_map[aid] = "missing_file"
            elif has_task_ids(abs_file):
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "missing_task_ids"
            continue

        if aid == "spec_state":
            if not abs_file.exists():
                if guardrails.build_spec_state(project, quiet=True) == 0 and abs_file.exists():
                    if isinstance(read_json(abs_file), dict):
                        status_map[aid] = "done"
                        issue_map[aid] = ""
                    else:
                        status_map[aid] = "ready"
                        issue_map[aid] = "invalid_json"
                else:
                    status_map[aid] = "ready"
                    issue_map[aid] = "missing_file"
            elif isinstance(read_json(abs_file), dict):
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "invalid_json"
            continue

        if aid in {"epic", "milestones"}:
            if not abs_file.exists():
                status_map[aid] = "ready"
                issue_map[aid] = "missing_file"
            elif has_substantive_lines(abs_file):
                status_map[aid] = "done"
                issue_map[aid] = ""
            else:
                status_map[aid] = "ready"
                issue_map[aid] = "placeholder_only"
            continue

        if abs_file.exists():
            status_map[aid] = "done"
            issue_map[aid] = ""
        else:
            status_map[aid] = "ready"
            issue_map[aid] = "missing_file"

    apply_ready = all(status_map.get(x) == "done" for x in required_for_start)
    overall_state = "draft"
    if any(status_map.get(item["id"]) == "blocked" for item in artifacts):
        overall_state = "blocked"
    elif apply_ready:
        overall_state = "ready"

    result_artifacts = []
    next_ready: List[str] = []
    for item in artifacts:
        aid = item["id"]
        st = status_map.get(aid, "ready")
        issue = issue_map.get(aid, "")
        row = {
            "id": aid,
            "file": item["file"],
            "status": st,
            "dependsOn": item["deps"],
            "issues": [issue] if issue else [],
        }
        result_artifacts.append(row)
        if st == "ready":
            next_ready.append(aid)

    if output_json:
        payload = {
            "state": overall_state,
            "applyReady": apply_ready,
            "applyRequires": required_for_start,
            "artifacts": result_artifacts,
            "nextReady": next_ready,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(f"Artifact state: {overall_state} (applyReady={str(apply_ready).lower()})")
    for item in result_artifacts:
        aid = item["id"]
        st = item["status"]
        issue = item["issues"][0] if item["issues"] else ""
        if issue:
            print(f"- {aid}: {st} [{issue}] ({item['file']})")
        else:
            print(f"- {aid}: {st} ({item['file']})")
    return 0


def extract_phase_from_text(raw: str) -> str:
    m = re.search(r"M[0-2]", (raw or "").upper())
    return m.group(0) if m else ""


def extract_task_from_text(raw: str) -> str:
    upper = (raw or "").upper()
    m = re.search(r"TASK[ -]?0*[0-9]{1,4}", upper)
    if m:
        return normalize_task_id(m.group(0).replace(" ", ""))
    m = re.search(r"T0*[0-9]{1,4}", upper)
    if m:
        return normalize_task_id(m.group(0))
    m = re.search(r"任务0*[0-9]{1,4}", upper)
    if m:
        return normalize_task_id(m.group(0).replace("任务", ""))
    return ""


def first_task_from_spec(paths: Paths, phase: str) -> str:
    tasks_file = paths.project_dir / ".rpi-outfile" / "specs" / "l0" / "tasks.md"
    if not tasks_file.exists():
        return ""
    text = tasks_file.read_text(encoding="utf-8", errors="ignore")
    scoped_text = text
    if re.search(rf"^#{{1,6}}\s*{re.escape(phase)}\b", text, flags=re.MULTILINE | re.IGNORECASE):
        lines = text.splitlines()
        capture = False
        buf: List[str] = []
        for line in lines:
            if re.match(rf"^#{{1,6}}\s*{re.escape(phase)}([ :：]|$)", line, flags=re.IGNORECASE):
                capture = True
                continue
            if capture and re.match(r"^#{1,6}\s*M[0-2]([ :：]|$)", line, flags=re.IGNORECASE):
                break
            if capture:
                buf.append(line)
        scoped_text = "\n".join(buf)
    m = re.search(r"task[ -]?0*[0-9]{1,4}", scoped_text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"task[ -]?0*[0-9]{1,4}", text, flags=re.IGNORECASE)
    if not m:
        return ""
    token = m.group(0).replace(" ", "").upper()
    return normalize_task_id(token)


def looks_like_spec_refs(raw: str) -> bool:
    if not raw:
        return False
    if raw.startswith(".rpi-outfile/specs/"):
        return True
    if raw.endswith(".md") or ".md#" in raw or "," in raw:
        return True
    return False


def infer_spec_refs(paths: Paths, task_id: str, explicit_refs: str) -> str:
    if explicit_refs:
        return explicit_refs
    refs: List[str] = []
    master_file = paths.project_dir / ".rpi-outfile" / "specs" / "00_master_spec.md"
    discovery_file = paths.project_dir / ".rpi-outfile" / "specs" / "l0" / "discovery.md"
    tasks_file = paths.project_dir / ".rpi-outfile" / "specs" / "l0" / "tasks.md"
    if master_file.exists():
        refs.append(".rpi-outfile/specs/00_master_spec.md")
    if discovery_file.exists():
        refs.append(".rpi-outfile/specs/l0/discovery.md")
    if tasks_file.exists():
        pretty_task = task_id.replace("TASK", "Task")
        m = re.fullmatch(r"TASK-0*([0-9]{1,4})", task_id)
        if m:
            num = int(m.group(1))
            task_text = tasks_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(rf"task[ -]?0*{num}\b", task_text, flags=re.IGNORECASE):
                refs.append(f".rpi-outfile/specs/l0/tasks.md#{pretty_task}")
            else:
                refs.append(".rpi-outfile/specs/l0/tasks.md")
        else:
            refs.append(".rpi-outfile/specs/l0/tasks.md")
    if not refs:
        refs = [".rpi-outfile/specs/l0/spec.md"]
    return ",".join(refs)


def signature_line_for_file(path: Path) -> str:
    if path.exists():
        return f"{path}:{file_mtime(path)}"
    return f"{path}:MISSING"


def precode_signature_hash(paths: Paths, runtime: Dict[str, Any]) -> str:
    discovery_file = paths.spec_dir / "l0" / "discovery.md"
    spec_file = paths.spec_dir / "l0" / "spec.md"
    tasks_file = paths.spec_dir / "l0" / "tasks.md"
    linkage_file = paths.spec_dir / "l0" / "module-linkage.md"
    arch_rules_file = paths.config_dir / "architecture.rules.json"
    scan_excludes_raw = runtime_get(runtime, "architecture_scan_exclude_dirs", [])
    if isinstance(scan_excludes_raw, list):
        scan_excludes = ",".join(sorted([str(x).strip() for x in scan_excludes_raw if str(x).strip()]))
    elif isinstance(scan_excludes_raw, str):
        scan_excludes = scan_excludes_raw.strip()
    else:
        scan_excludes = ""

    lines = [
        f"precode_guard_mode={str_value(runtime_get(runtime, 'precode_guard_mode', 'enforce'))}",
        f"architecture_enforce={str(bool_value(runtime_get(runtime, 'architecture_enforce', False))).lower()}",
        f"architecture_require_rules={str(bool_value(runtime_get(runtime, 'architecture_require_rules', False))).lower()}",
        f"architecture_scan_max_files={int_value(runtime_get(runtime, 'architecture_scan_max_files', 2000), 2000)}",
        f"architecture_scan_exclude_dirs={scan_excludes}",
        f"spec_link_enforce={str(bool_value(runtime_get(runtime, 'spec_link_enforce', False))).lower()}",
        f"require_linkage_spec={str(bool_value(runtime_get(runtime, 'require_linkage_spec', False))).lower()}",
        f"linkage_strict_mode={str(bool_value(runtime_get(runtime, 'linkage_strict_mode', False))).lower()}",
        f"ddd_lite_mode={str_value(runtime_get(runtime, 'ddd_lite_mode', 'warn'))}",
        f"ddd_min_glossary_terms={int_value(runtime_get(runtime, 'ddd_min_glossary_terms', 6), 6)}",
        f"ddd_min_bounded_contexts={int_value(runtime_get(runtime, 'ddd_min_bounded_contexts', 2), 2)}",
        f"ddd_min_invariants={int_value(runtime_get(runtime, 'ddd_min_invariants', 3), 3)}",
        f"mvp_priority_override_mode={str_value(runtime_get(runtime, 'mvp_priority_override_mode', 'warn'))}",
        f"mvp_weighted_coverage_tolerance={int_value(runtime_get(runtime, 'mvp_weighted_coverage_tolerance', 10), 10)}",
        f"mvp_max_promote_non_core={int_value(runtime_get(runtime, 'mvp_max_promote_non_core', 1), 1)}",
        signature_line_for_file(discovery_file),
        signature_line_for_file(spec_file),
        signature_line_for_file(tasks_file),
        signature_line_for_file(linkage_file),
        signature_line_for_file(arch_rules_file),
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def cmd_profile(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    runtime = load_runtime(paths)
    cmd = argv[0] if argv else "show"
    profile_dir = paths.profiles_dir

    def list_profiles() -> List[str]:
        if not profile_dir.exists():
            return []
        names = [p.stem for p in profile_dir.glob("*.json") if p.is_file()]
        return sorted(set(names))

    def show_profile() -> None:
        name = str_value(runtime_get(runtime, "profile_name", ""), "")
        if not name:
            name = "(unset)"
        print(f"Active profile: {name}")
        if paths.runtime_file.exists():
            raw = read_json_obj(paths.runtime_file)
            out = {
                "profile_name": str_value(raw.get("profile_name", ""), ""),
                "harness_enabled": bool_value(raw.get("harness_enabled", True), True),
                "strict_mode": raw.get("strict_mode"),
                "start_require_ready": raw.get("start_require_ready"),
                "close_require_spec_sync": raw.get("close_require_spec_sync"),
                "architecture_enforce": bool_value(raw.get("architecture_enforce", False), False),
                "architecture_scan_max_files": int_value(raw.get("architecture_scan_max_files", 2000), 2000),
                "architecture_scan_exclude_dirs": raw.get("architecture_scan_exclude_dirs", []),
                "spec_link_enforce": bool_value(raw.get("spec_link_enforce", False), False),
                "ddd_lite_mode": str_value(raw.get("ddd_lite_mode", "warn"), "warn"),
                "mvp_priority_override_mode": str_value(raw.get("mvp_priority_override_mode", "warn"), "warn"),
                "mvp_weighted_coverage_tolerance": int_value(raw.get("mvp_weighted_coverage_tolerance", 10), 10),
                "mvp_max_promote_non_core": int_value(raw.get("mvp_max_promote_non_core", 1), 1),
                "precode_guard_mode": str_value(raw.get("precode_guard_mode", "enforce"), "enforce"),
                "tdd_mode": str_value(raw.get("tdd_mode", "strict"), "strict"),
                "gates_auto_retry_enabled": bool_value(raw.get("gates_auto_retry_enabled", False), False),
                "gates_auto_retry_max": int_value(raw.get("gates_auto_retry_max", 0), 0),
                "auto_rpi_enabled": bool_value(raw.get("auto_rpi_enabled", False), False),
                "auto_rpi_max_rounds": int_value(raw.get("auto_rpi_max_rounds", 0), 0),
                "agent_memory_auto_update": bool_value(raw.get("agent_memory_auto_update", False), False),
                "agent_review_enabled": bool_value(raw.get("agent_review_enabled", False), False),
                "a2a_auto_merge_non_core": bool_value(raw.get("a2a_auto_merge_non_core", False), False),
                "a2a_allow_commit": bool_value(raw.get("a2a_allow_commit", False), False),
                "risk_high_requires_approval": bool_value(raw.get("risk_high_requires_approval", False), False),
                "audit_report_enabled": bool_value(raw.get("audit_report_enabled", False), False),
                "audit_pack_required_on_close": bool_value(raw.get("audit_pack_required_on_close", False), False),
                "stop_loop_max_blocks": int_value(raw.get("stop_loop_max_blocks", 0), 0),
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))

    def apply_profile(profile: str) -> int:
        preset_file = profile_dir / f"{profile}.json"
        if not preset_file.exists():
            print(f"Unknown profile: {profile}", file=sys.stderr)
            print("Available profiles:", file=sys.stderr)
            for item in list_profiles():
                print(item, file=sys.stderr)
            return 1
        base = read_json_obj(paths.runtime_file)
        preset = read_json_obj(preset_file)
        merged = deep_merge(base, preset)
        merged["profile_name"] = profile
        merged["profile_applied_at"] = utc_now()
        write_json_atomic(paths.runtime_file, merged)
        append_event(paths, {"ts": utc_now(), "event": "profile_applied", "profile": profile})
        print(f"Applied profile: {profile}")
        nonlocal runtime
        runtime = merged
        show_profile()
        return 0

    if cmd in {"-h", "--help", "help"}:
        print("Usage: bash .claude/workflow/rpi.sh mode profile [list|show|<profile>|apply <profile>]")
        print("")
        print("Profiles:")
        print("  strict-regulated")
        print("  balanced-enterprise")
        print("  auto-lab")
        return 0
    if cmd == "list":
        for item in list_profiles():
            print(item)
        return 0
    if cmd == "show":
        show_profile()
        return 0
    if cmd == "apply":
        if len(argv) < 2:
            print("Usage: bash .claude/workflow/rpi.sh mode profile [list|show|<profile>|apply <profile>]", file=sys.stderr)
            return 1
        return apply_profile(argv[1])
    if cmd in {"strict-regulated", "balanced-enterprise", "auto-lab"}:
        return apply_profile(cmd)
    print("Usage: bash .claude/workflow/rpi.sh mode profile [list|show|<profile>|apply <profile>]", file=sys.stderr)
    return 1


def cmd_gates_auto(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    runtime = load_runtime(paths)
    phase = ""
    max_retries_raw = ""
    auto_fix_raw = ""
    quiet = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token in {"M0", "M1", "M2"}:
            phase = token
            i += 1
            continue
        if token == "--max-retries":
            if i + 1 >= len(args):
                print("Unknown argument: --max-retries", file=sys.stderr)
                return 1
            max_retries_raw = args[i + 1]
            i += 2
            continue
        if token == "--auto-fix":
            auto_fix_raw = "true"
            i += 1
            continue
        if token == "--no-auto-fix":
            auto_fix_raw = "false"
            i += 1
            continue
        if token == "--quiet":
            quiet = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            print("Usage: bash .claude/workflow/rpi.sh gates run [M0|M1|M2] [--max-retries N] [--auto-fix|--no-auto-fix] [--quiet]")
            print("")
            print("Run quality gate with bounded auto-retry and root-cause tagging.")
            return 0
        print(f"Unknown argument: {token}", file=sys.stderr)
        return 1

    if not phase:
        phase = normalize_phase(str_value(read_json_obj(paths.phase_file).get("phase", "M0"), "M0")) or "M0"
    if phase not in {"M0", "M1", "M2"}:
        print(f"Invalid phase: {phase} (must be M0|M1|M2)", file=sys.stderr)
        return 1

    retry_enabled = bool_value(runtime_get(runtime, "gates_auto_retry_enabled", False), False)
    max_retries = int_value(max_retries_raw, int_value(runtime_get(runtime, "gates_auto_retry_max", 0), 0))
    auto_fix = bool_value(auto_fix_raw, bool_value(runtime_get(runtime, "gates_auto_fix_on_fail", False), False))
    if not retry_enabled:
        max_retries = 0
    if max_retries < 0:
        print(f"Invalid max retries: {max_retries}", file=sys.stderr)
        return 1

    def infer_failure_root_cause(since_iso: str) -> Tuple[str, List[str]]:
        rows = parse_jsonl(paths.gate_log)
        failed = []
        for row in rows:
            ts = str_value(row.get("ts", ""))
            if since_iso and ts < since_iso:
                continue
            if str_value(row.get("status", "")) != "fail":
                continue
            gate = str_value(row.get("gate", ""))
            if gate:
                failed.append(gate)
        failed = sorted(set(failed))
        joined = " ".join(failed).lower()
        if re.search(r"discovery|contract|scope|spec_state|bootstrap", joined):
            return "spec_incomplete", failed
        if re.search(r"architecture", joined):
            return "architecture_violation", failed
        if re.search(r"test|lint|typecheck|security", joined):
            return "implementation_quality", failed
        return "unknown", failed

    def apply_auto_fix(root_cause: str) -> bool:
        if not auto_fix:
            return False
        if root_cause == "spec_incomplete":
            guardrails.build_spec_state(paths.project_dir, quiet=True)
            guardrails.verify_spec_state(paths.project_dir, scope="all", quiet=True)
            return True
        rc, _, _ = run_automation_capture(paths, "anti-entropy", ["--auto-fix"])
        _ = rc
        return True

    attempt = 0
    while True:
        attempt_ts = utc_now()
        rc = cmd_quality_gate(paths, [phase])
        gate_output = ""
        if rc == 0:
            append_event(paths, {"ts": utc_now(), "event": "quality_gate_auto", "phase": phase, "attempt": attempt, "status": "pass"})
            if not quiet and gate_output:
                print(gate_output.rstrip("\n"))
            return 0

        if attempt >= max_retries:
            append_event(paths, {"ts": utc_now(), "event": "quality_gate_auto", "phase": phase, "attempt": attempt, "status": "fail"})
            if gate_output:
                print(gate_output.rstrip("\n"), file=sys.stderr)
            return rc if rc != 0 else 1

        root_cause, failed_gates = infer_failure_root_cause(attempt_ts)
        fixed = apply_auto_fix(root_cause)
        append_event(
            paths,
            {
                "ts": utc_now(),
                "event": "quality_gate_retry",
                "phase": phase,
                "attempt": attempt,
                "root_cause": root_cause,
                "failed_gates": failed_gates,
                "auto_fix": auto_fix,
                "fixed": fixed,
            },
        )
        if not quiet:
            print(f"quality gate retry: attempt={attempt} root_cause={root_cause} auto_fix={str(auto_fix).lower()} fixed={str(fixed).lower()}", file=sys.stderr)
        attempt += 1


def cmd_quality_gate(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    phase = argv[0] if argv else normalize_phase(str_value(read_json_obj(paths.phase_file).get("phase", "M0"), "M0")) or "M0"
    if phase not in {"M0", "M1", "M2"}:
        print(f"Invalid phase: {phase} (must be M0|M1|M2)", file=sys.stderr)
        return 1

    config_file = paths.config_dir / "gates.json"
    if not config_file.exists():
        print(f"Missing gate config: {config_file}", file=sys.stderr)
        return 1
    config = read_json_obj(config_file)
    phase_gates_raw = ((config.get("phase_gates") or {}).get(phase) or [])
    verify_default = ((config.get("verify") or {}).get("default") or [])
    verify_phase = ((config.get("verify") or {}).get(phase) or [])
    if not isinstance(phase_gates_raw, list):
        phase_gates_raw = []
    if not isinstance(verify_default, list):
        verify_default = []
    if not isinstance(verify_phase, list):
        verify_phase = []

    if len(phase_gates_raw) == 0:
        print(f"No gates configured for phase {phase}", file=sys.stderr)
        return 1

    ts = utc_now()
    overall = "pass"
    verify_status = "skipped"
    verify_failed = False
    verify_count = 0
    gate_count = 0

    def run_check(layer: str, gate: str, command: str) -> None:
        nonlocal overall, verify_failed
        if not command or command == "__REQUIRED__":
            status = "fail"
            exit_code = 127
            message = "Gate command not configured in .claude/workflow/config/gates.json"
        else:
            proc = subprocess.run(
                resolve_shell_argv(command),
                check=False,
                cwd=str(paths.project_dir),
            )
            exit_code = int(proc.returncode)
            if exit_code == 0:
                status = "pass"
                message = "ok"
            else:
                status = "fail"
                message = "command failed"

        append_gate(
            paths,
            {
                "ts": ts,
                "phase": phase,
                "layer": layer,
                "gate": gate,
                "command": command,
                "status": status,
                "message": message,
                "exit_code": exit_code,
            },
        )

        if status != "pass":
            overall = "fail"
            if layer == "verify":
                verify_failed = True

    def run_verify_item(item: Any, fallback_name: str) -> None:
        nonlocal verify_count
        if isinstance(item, str):
            name = fallback_name
            command = item
        elif isinstance(item, dict):
            name = str_value(item.get("name", ""), "") or fallback_name
            command = str_value(item.get("command", ""), "")
        else:
            name = fallback_name
            command = ""
        run_check("verify", name, command)
        verify_count += 1

    if verify_default or verify_phase:
        verify_status = "pass"

    for idx, item in enumerate(verify_default, start=1):
        run_verify_item(item, f"verify_default_{idx}")
    for idx, item in enumerate(verify_phase, start=1):
        run_verify_item(item, f"verify_{phase}_{idx}")

    if verify_count > 0:
        verify_status = "fail" if verify_failed else "pass"

    commands_map = config.get("commands", {})
    if not isinstance(commands_map, dict):
        commands_map = {}

    for gate_item in phase_gates_raw:
        if isinstance(gate_item, dict):
            gate_name = str_value(gate_item.get("name", ""), "") or f"gate_{gate_count}"
            gate_cmd = str_value(gate_item.get("command", ""), "") or "__REQUIRED__"
        else:
            gate_name = str(gate_item)
            gate_cmd = str_value(commands_map.get(gate_name, "__REQUIRED__"), "__REQUIRED__")
        run_check("gate", gate_name, gate_cmd)
        gate_count += 1

    current = read_json_obj(paths.current_task_file)
    current.setdefault("quality_gate", {})
    if not isinstance(current.get("quality_gate"), dict):
        current["quality_gate"] = {}
    current["quality_gate"]["last_run_status"] = overall
    current["quality_gate"]["last_run_phase"] = phase
    current["quality_gate"]["last_run_at"] = ts
    current["quality_gate"]["last_verify_status"] = verify_status
    current["quality_gate"]["last_verify_count"] = verify_count
    if str_value(current.get("status", ""), "") == "in_progress":
        current.setdefault("phase_state", {})
        if not isinstance(current.get("phase_state"), dict):
            current["phase_state"] = {}
        current["phase_state"]["current_action"] = "check_passed" if overall == "pass" else "check_failed"
        if overall == "pass":
            current["phase_state"]["next_actions"] = ["close"]
        else:
            current["phase_state"]["next_actions"] = ["implement", "check", "close"]
    current["last_updated_at"] = ts
    write_json_atomic(paths.current_task_file, current)

    append_event(
        paths,
        {
            "ts": ts,
            "event": "quality_gate",
            "phase": phase,
            "status": overall,
            "verify_status": verify_status,
            "verify_count": verify_count,
            "gate_count": gate_count,
        },
    )

    if overall != "pass":
        print(f"Quality gate failed for phase {phase}", file=sys.stderr)
        return 1

    if verify_count > 0:
        print(f"Quality gate passed for phase {phase} (verify={verify_status}, checks={verify_count}+{gate_count})")
    else:
        print(f"Quality gate passed for phase {phase}")
    return 0


def cmd_start(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    runtime = load_runtime(paths)
    args = list(argv)
    raw_input = " ".join(args).strip()
    arg1 = args[0] if len(args) > 0 else ""
    arg2 = args[1] if len(args) > 1 else ""
    arg3 = args[2] if len(args) > 2 else ""
    arg4 = args[3] if len(args) > 3 else ""

    if arg1 in {"--help", "-h", "help"}:
        print("Usage (explicit):")
        print('  bash .claude/workflow/rpi.sh task start <task_id> <M0|M1|M2> "<spec1,spec2,...>" [owner]')
        print("")
        print("Usage (auto-complete):")
        print("  bash .claude/workflow/rpi.sh task start 001")
        print("  bash .claude/workflow/rpi.sh task start M0")
        print('  bash .claude/workflow/rpi.sh task start "开始M0的task001"')
        print("  bash .claude/workflow/rpi.sh task start")
        return 0

    phase_from_arg1 = normalize_phase(arg1)
    phase_from_arg2 = normalize_phase(arg2)
    phase_from_text = extract_phase_from_text(raw_input)
    phase = ""
    if phase_from_arg2:
        phase = phase_from_arg2
    elif phase_from_arg1:
        phase = phase_from_arg1
    elif phase_from_text:
        phase = phase_from_text
    else:
        phase = normalize_phase(str_value(read_json_obj(paths.phase_file).get("phase", "M0"), "M0")) or "M0"
    if phase not in {"M0", "M1", "M2"}:
        print(f"Invalid phase detected: {phase}", file=sys.stderr)
        return 1

    task_input = arg2 if phase_from_arg1 else arg1
    task_id = normalize_task_id(task_input)
    if not task_id:
        task_id = extract_task_from_text(raw_input)
    if not task_id:
        task_id = first_task_from_spec(paths, phase)
    if not task_id:
        task_id = "TASK-001"

    current = read_json_obj(paths.current_task_file)
    existing_task_id = str_value(current.get("task_id", ""), "")
    existing_status = str_value(current.get("status", "idle"), "idle")
    if existing_task_id and existing_status == "in_progress":
        print(f"Start blocked: active task already in progress ({existing_task_id}).", file=sys.stderr)
        print("Use /rpi-task close to complete it, or /rpi-task pause \"<reason>\" before starting a new task.", file=sys.stderr)
        return 1

    owner = "claude"
    spec_refs_csv = ""
    if phase_from_arg1:
        if looks_like_spec_refs(arg2):
            spec_refs_csv = arg2
            if arg3:
                owner = arg3
        else:
            if looks_like_spec_refs(arg3):
                spec_refs_csv = arg3
                if arg4:
                    owner = arg4
            elif arg3:
                owner = arg3
    elif phase_from_arg2:
        if looks_like_spec_refs(arg3):
            spec_refs_csv = arg3
            if arg4:
                owner = arg4
        elif arg3:
            owner = arg3
    else:
        if looks_like_spec_refs(arg2):
            spec_refs_csv = arg2
            if arg3:
                owner = arg3
        elif looks_like_spec_refs(arg3):
            spec_refs_csv = arg3
            if arg4:
                owner = arg4
        else:
            if arg4:
                owner = arg4
            elif arg2:
                owner = arg2

    spec_refs_csv = infer_spec_refs(paths, task_id, spec_refs_csv)

    task_context_dir = paths.project_dir / ".rpi-outfile" / "specs" / "tasks" / task_id
    if not task_context_dir.exists():
        task_context_dir.mkdir(parents=True, exist_ok=True)
        (task_context_dir / "context-pack.md").write_text(
            f"# {task_id} Context Pack（可选）\n\n"
            "可选放置任务级上下文清单，优先级高于全局清单：\n\n"
            f"- .rpi-outfile/specs/tasks/{task_id}/implement.jsonl\n"
            f"- .rpi-outfile/specs/tasks/{task_id}/check.jsonl\n"
            f"- .rpi-outfile/specs/tasks/{task_id}/debug.jsonl\n\n"
            "JSONL 示例：\n"
            '{"file":".rpi-outfile/specs/l0/spec.md","reason":"contract boundary for this task"}\n',
            encoding="utf-8",
        )

    rc_ctx, out_ctx, _ = run_automation_capture(paths, "resolve-context-refs", ["implement", spec_refs_csv, phase, task_id])
    context_refs_csv = out_ctx.strip() if rc_ctx == 0 else ""
    if not context_refs_csv:
        context_refs_csv = spec_refs_csv
    spec_refs_compact = compact_ref_list(split_csv(spec_refs_csv), max_items=3)
    context_refs_compact = minimal_context_refs(spec_refs_compact, split_csv(context_refs_csv), max_items=3)
    spec_refs_csv = ",".join(spec_refs_compact)
    context_refs_csv = ",".join(context_refs_compact)

    strict_mode = bool_value(runtime_get(runtime, "strict_mode", True), True)
    start_require_ready = bool_value(runtime_get(runtime, "start_require_ready", True), True)
    spec_state_required = bool_value(runtime_get(runtime, "spec_state_required", True), True)
    precode_guard_mode = str_value(runtime_get(runtime, "precode_guard_mode", "enforce"), "enforce")

    if start_require_ready:
        rc_art, out_art, _ = run_task_flow_capture(paths, "artifact-status", ["--json"])
        if rc_art in {0, 1} and out_art.strip():
            try:
                art = json.loads(out_art)
            except Exception:
                art = {}
            if isinstance(art, dict):
                apply_ready = bool_value(art.get("applyReady", False), False)
                artifact_state = str_value(art.get("state", "unknown"), "unknown")
                if not apply_ready:
                    next_ready = art.get("nextReady", [])
                    if isinstance(next_ready, list):
                        next_text = ",".join([str(x) for x in next_ready if str(x).strip()])
                    else:
                        next_text = ""
                    if not next_text:
                        next_text = "discovery,spec,tasks"
                    if strict_mode:
                        append_event(
                            paths,
                            {"ts": utc_now(), "event": "rpi_start_blocked", "artifact_state": artifact_state, "next_ready": next_text},
                        )
                        print(f"Start blocked by strict_mode: artifacts are not apply-ready (state={artifact_state}).", file=sys.stderr)
                        print(f"Complete required artifacts first. Suggested next: {next_text}", file=sys.stderr)
                        print("Run: /rpi-check discovery /rpi-check contract /rpi-check scope", file=sys.stderr)
                        return 1
                    print(f"Warning: artifacts are not apply-ready (state={artifact_state}). Continuing because strict_mode=false.", file=sys.stderr)

    if spec_state_required:
        build_rc = guardrails.build_spec_state(paths.project_dir, quiet=True)
        if build_rc != 0:
            if strict_mode:
                append_event(paths, {"ts": utc_now(), "event": "rpi_start_blocked", "reason": "spec_state_build_failed"})
                print("Start blocked by strict_mode: failed to build machine-readable spec state.", file=sys.stderr)
                print("Run: /rpi-spec build and fix related spec files, then retry.", file=sys.stderr)
                return 1
            print("Warning: failed to build machine-readable spec state, continue because strict_mode=false.", file=sys.stderr)

    precode_signature = precode_signature_hash(paths, runtime)
    precode_status = "off"
    precode_note = "precode guard disabled"
    if precode_guard_mode != "off":
        precode_status = "pass"
        precode_note = "precode guard checks passed at start"
        architecture_enforce = bool_value(runtime_get(runtime, "architecture_enforce", False), False)
        architecture_require_rules = bool_value(runtime_get(runtime, "architecture_require_rules", False), False)
        precode_result = guardrails.check_precode_bundle(
            project_dir=paths.project_dir,
            include_architecture=architecture_enforce,
            architecture_require_rules=architecture_require_rules,
        )
        failures_raw = precode_result.get("failures", [])
        failures = [str(x).strip() for x in failures_raw] if isinstance(failures_raw, list) else []
        failures = [x for x in failures if x]

        if failures:
            precode_status = "fail"
            precode_note = "; ".join(failures)
            if precode_guard_mode == "enforce":
                append_event(
                    paths,
                    {
                        "ts": utc_now(),
                        "event": "rpi_start_blocked",
                        "reason": "precode_guard_failed",
                        "detail": precode_note,
                    },
                )
                print(f"Start blocked: precode guard checks failed ({precode_note}).", file=sys.stderr)
                print("Run: /rpi-check discovery /rpi-check contract /rpi-check scope /rpi-check architecture", file=sys.stderr)
                return 1
            print(
                f"Warning: precode guard checks failed at start ({precode_note}), continue because precode_guard_mode={precode_guard_mode}.",
                file=sys.stderr,
            )

    ts = utc_now()
    ratio = phase_ratio(phase)
    stop_state_file = paths.state_dir / "stop_loop_state.json"
    if stop_state_file.exists():
        stop_state_file.unlink()

    task_payload = {
        "task_id": task_id,
        "phase": phase,
        "status": "in_progress",
        "enforce_stop_gate": True,
        "spec_refs": spec_refs_compact,
        "context_refs": context_refs_compact,
        "notes": [],
        "phase_state": {"current_action": "implement", "next_actions": ["implement", "check", "close"]},
        "classification": {"root_cause": "unknown", "note": ""},
        "tdd": {
            "red_test_written": False,
            "red_test_targeted": False,
            "red_test_evidence": "",
            "red_test_at": "",
            "latest_test_status": "unknown",
            "last_test_command": "",
        },
        "quality_gate": {
            "last_run_status": "unknown",
            "last_run_phase": "",
            "last_run_at": "",
            "last_verify_status": "unknown",
            "last_verify_count": 0,
        },
        "autonomy": {
            "tool_event_count": 0,
            "last_tool_event_at": "",
        },
        "guardrails": {
            "precode": {
                "status": precode_status,
                "signature": precode_signature,
                "verified_at": ts,
                "note": precode_note,
            }
        },
        "created_at": ts,
        "last_updated_at": ts,
        "owner": owner,
    }
    write_json_atomic(paths.current_task_file, task_payload)
    write_json_atomic(paths.phase_file, {"phase": phase, "spec_ratio": ratio, "updated_at": ts})
    contract_file = write_portable_contract(paths, task_payload, transition="started")
    append_event(
        paths,
        {
            "ts": ts,
            "event": "rpi_start",
            "task_id": task_id,
            "phase": phase,
            "owner": owner,
            "spec_refs": spec_refs_csv,
            "context_refs": context_refs_csv,
            "portable_contract": str(contract_file),
        },
    )

    print("")
    print(f"Task started: {task_id} (phase: {phase})")
    print(f"Spec refs: {spec_refs_csv or 'none'}")
    print("")
    print("Context preview (first 5):")
    for item in task_payload["context_refs"][:5]:
        print(f"  - {item}")
    if not task_payload["context_refs"]:
        print("  (no context refs)")
    print("")
    print("Next: implement code with TDD (Red -> Green -> Refactor)")
    return 0


def cmd_close(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    if len(argv) < 2:
        print('Usage: bash .claude/workflow/rpi.sh task close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<note>"', file=sys.stderr)
        return 1

    runtime = load_runtime(paths)
    result = argv[0]
    root_cause_input = argv[1]
    note = " ".join(argv[2:]).strip()
    if result not in {"pass", "fail"}:
        print(f"Invalid result: {result} (must be pass|fail)", file=sys.stderr)
        return 1

    current = read_json_obj(paths.current_task_file)
    task_id = str_value(current.get("task_id", ""), "")
    phase = str_value(current.get("phase", "M0"), "M0")
    created_at = str_value(current.get("created_at", ""), "")
    if not task_id:
        print("No active task to close", file=sys.stderr)
        return 1

    infer_evidence: List[str] = []

    def infer_root_cause(since_iso: str) -> str:
        spec_signal = False
        exec_signal = False

        for row in parse_jsonl(paths.gate_log):
            if str_value(row.get("status", "")) != "fail":
                continue
            ts = str_value(row.get("ts", ""))
            if since_iso and ts and ts < since_iso:
                continue
            gate = str_value(row.get("gate", ""))
            if not gate:
                continue
            if gate in {"discovery_complete", "contract_spec_complete", "scope_guard_passed", "bootstrap_check"}:
                spec_signal = True
                infer_evidence.append(f"gate fail [spec]: {gate}")
            elif gate in {"unit_tests", "integration_tests", "contract_tests", "e2e_tests", "lint", "typecheck", "security_scan", "test_command"}:
                exec_signal = True
                infer_evidence.append(f"gate fail [exec]: {gate}")
            else:
                exec_signal = True
                infer_evidence.append(f"gate fail [exec]: {gate}")

        for row in parse_jsonl(paths.event_log):
            if str_value(row.get("event", "")) != "pre_tool_block":
                continue
            ts = str_value(row.get("ts", ""))
            if since_iso and ts and ts < since_iso:
                continue
            reason_lower = str_value(row.get("reason", ""), "").lower()
            short = reason_lower[:80]
            if re.search(r"discovery|contract|scope|spec", reason_lower):
                spec_signal = True
                infer_evidence.append(f"pre_tool_block [spec]: {short}")
            if re.search(r"tdd|test|lint|typecheck|quality.?gate", reason_lower):
                exec_signal = True
                infer_evidence.append(f"pre_tool_block [exec]: {short}")

        latest_test_status = str_value(((current.get("tdd") or {}).get("latest_test_status", "unknown")), "unknown")
        if latest_test_status == "fail":
            exec_signal = True
            infer_evidence.append("tdd.latest_test_status = fail")

        if spec_signal and exec_signal:
            return "both"
        if spec_signal:
            return "spec_missing"
        if exec_signal:
            return "execution_deviation"
        return "unknown"

    def calc_spec_sync_status(since_iso: str) -> Tuple[str, int, int]:
        if not since_iso or not paths.event_log.exists():
            return "unknown", 0, 0
        code_edits = 0
        spec_edits = 0
        code_ext = re.compile(r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|java|kt|rb|rs|php|cs|swift|scala|sh|sql)$")
        src_hint = re.compile(r"(^|/)(src|app|apps|packages|lib|server|backend|frontend)/")
        for row in parse_jsonl(paths.event_log):
            if str_value(row.get("event", "")) != "post_tool_use":
                continue
            ts = str_value(row.get("ts", ""))
            if ts < since_iso:
                continue
            path = str_value(row.get("path", ""), "")
            tool = str_value(row.get("tool", ""), "")
            mutates_repo = bool_value(row.get("mutates_repo", False), False)
            targets_code = bool_value(row.get("targets_code", False), False)
            opaque_codegen = bool_value(row.get("opaque_codegen", False), False)
            targets_specs = bool_value(row.get("targets_specs", False), False)

            is_code_edit = False
            if path:
                if not path.startswith(".rpi-outfile/specs/") and (code_ext.search(path) or src_hint.search(path)):
                    is_code_edit = True
            if tool == "Bash" and mutates_repo and (targets_code or opaque_codegen):
                is_code_edit = True
            if is_code_edit:
                code_edits += 1

            is_spec_edit = False
            if path.startswith(".rpi-outfile/specs/"):
                is_spec_edit = True
            if tool == "Bash" and mutates_repo and targets_specs:
                is_spec_edit = True
            if is_spec_edit:
                spec_edits += 1

        if code_edits == 0:
            return "in_sync", code_edits, spec_edits
        if spec_edits > 0:
            return "in_sync", code_edits, spec_edits
        return "stale", code_edits, spec_edits

    root_cause = root_cause_input
    inferred_root = False
    if not root_cause or root_cause == "auto":
        root_cause = infer_root_cause(created_at)
        inferred_root = True
        print(f"Root cause inferred: {root_cause}")
        if infer_evidence:
            print("Evidence:")
            for ev in infer_evidence:
                print(f"  - {ev}")
        else:
            print("Evidence: none (no gate failures or pre_tool_block events found)")

    if root_cause not in {"spec_missing", "execution_deviation", "both", "unknown"}:
        print(f"Invalid root cause: {root_cause}", file=sys.stderr)
        return 1

    spec_sync_status, code_edit_events, spec_edit_events = calc_spec_sync_status(created_at)
    strict_mode = bool_value(runtime_get(runtime, "strict_mode", True), True)
    close_require_spec_sync = bool_value(runtime_get(runtime, "close_require_spec_sync", True), True)
    audit_pack_required_on_close = bool_value(runtime_get(runtime, "audit_pack_required_on_close", False), False)
    if close_require_spec_sync and spec_sync_status == "stale":
        if strict_mode:
            print("Close blocked by strict_mode: code changed without spec write-back.", file=sys.stderr)
            print("Please update .rpi-outfile/specs/* and rerun /rpi-task close.", file=sys.stderr)
            return 1
        print("Warning: code changed without spec write-back, but continue because strict_mode=false.", file=sys.stderr)

    ts = utc_now()
    archive_dir = paths.log_dir / "tasks"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"{task_id}-{utc_compact_now()}.json"
    audit_pack_path = ""

    archive_payload = copy.deepcopy(current)
    archive_payload["status"] = "closed"
    archive_payload["result"] = result
    archive_payload.setdefault("classification", {})
    archive_payload["classification"]["root_cause"] = root_cause
    archive_payload["classification"]["note"] = note
    archive_payload["classification"]["inferred"] = inferred_root
    archive_payload["spec_sync"] = {
        "status": spec_sync_status,
        "code_edit_events": code_edit_events,
        "spec_edit_events": spec_edit_events,
    }
    archive_payload["audit_pack"] = audit_pack_path
    archive_payload["closed_at"] = ts
    archive_payload["last_updated_at"] = ts
    write_json_atomic(archive_file, archive_payload)

    if audit_pack_required_on_close:
        rc, out, _ = run_automation_capture(paths, "build-audit-pack", ["--task", task_id])
        if rc == 0:
            m = re.search(r"^Audit pack built:\s*(.+)$", out, flags=re.MULTILINE)
            if m:
                audit_pack_path = m.group(1).strip()
                payload = read_json_obj(archive_file)
                payload["audit_pack"] = audit_pack_path
                write_json_atomic(archive_file, payload)

    if bool_value(runtime_get(runtime, "agent_memory_auto_update", False), False):
        run_automation_capture(
            paths,
            "agent-memory-update",
            [
                "--task",
                task_id,
                "--result",
                result,
                "--root-cause",
                root_cause,
                "--note",
                note,
                "--archive",
                str(archive_file),
                "--quiet",
            ],
        )

    if bool_value(runtime_get(runtime, "audit_report_enabled", False), False):
        run_automation_capture(
            paths,
            "audit-report",
            ["--task", task_id, "--days", "365", "--output", ".rpi-outfile/audit/reports"],
        )

    capsule_file = write_task_capsule(
        paths,
        current,
        transition="closed",
        result=result,
        root_cause=root_cause,
        note=note,
        spec_sync_status=spec_sync_status,
        code_edit_events=code_edit_events,
        spec_edit_events=spec_edit_events,
    )
    contract_file = write_portable_contract(
        paths,
        current,
        transition="closed",
        result=result,
        root_cause=root_cause,
        note=note,
    )
    write_idle_task(paths, phase)
    append_event(
        paths,
        {
            "ts": ts,
            "event": "rpi_close",
            "task_id": task_id,
            "phase": phase,
            "result": result,
            "root_cause": root_cause,
            "inferred_root": inferred_root,
            "note": note,
            "spec_sync": spec_sync_status,
            "code_edit_events": code_edit_events,
            "spec_edit_events": spec_edit_events,
            "audit_pack": audit_pack_path,
            "archive": str(archive_file),
            "task_capsule": str(capsule_file),
            "portable_contract": str(contract_file),
        },
    )

    print(f"RPI task closed: task_id={task_id} result={result} root_cause={root_cause}")
    print(f"spec_sync={spec_sync_status} (code_edits={code_edit_events} spec_edits={spec_edit_events})")
    print(f"Archived: {archive_file}")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RPI task flow engine")
    parser.add_argument("--project-dir", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_profile = sub.add_parser("profile")
    p_profile.add_argument("args", nargs="*")

    p_start = sub.add_parser("start")
    p_start.add_argument("args", nargs="*")

    p_close = sub.add_parser("close")
    p_close.add_argument("args", nargs="*")

    p_gates = sub.add_parser("gates-auto")
    p_gates.add_argument("args", nargs="*")

    p_quality = sub.add_parser("quality-gate")
    p_quality.add_argument("args", nargs="*")

    p_artifact = sub.add_parser("artifact-status")
    p_artifact.add_argument("--json", action="store_true", dest="json_output")
    p_artifact.add_argument("args", nargs="*")

    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    ns = parse_args(argv)
    project_dir = Path(ns.project_dir).resolve() if ns.project_dir else resolve_project_dir(Path(__file__).resolve().parent)
    paths = build_paths(project_dir)

    if ns.cmd == "profile":
        return cmd_profile(paths, ns.args)
    if ns.cmd == "start":
        return cmd_start(paths, ns.args)
    if ns.cmd == "close":
        return cmd_close(paths, ns.args)
    if ns.cmd == "gates-auto":
        return cmd_gates_auto(paths, ns.args)
    if ns.cmd == "quality-gate":
        return cmd_quality_gate(paths, ns.args)
    if ns.cmd == "artifact-status":
        args = list(ns.args)
        if getattr(ns, "json_output", False):
            args = ["--json", *args]
        return cmd_artifact_status(paths, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
