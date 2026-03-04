#!/usr/bin/env python3
"""Guardrails engine for risk/spec/architecture/linkage checks.

This module serves two purposes:
1) CLI backend for legacy script entrypoints (thin shell wrappers).
2) Direct callable functions for pre_tool_use_core to avoid Bash subprocess cost.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import file_lock
import spec_state_tool


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_project_dir(start: Path) -> Path:
    cur = start.resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / ".claude" / "workflow").is_dir():
            return cand
    return cur


def normalize_path(text: str) -> str:
    return (text or "").replace("\\", "/")


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
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value.strip())
    return default


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


def write_json(path: Path, data: Any) -> None:
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


def posix_ere_to_python(pattern: str) -> str:
    out = pattern
    out = out.replace("[:space:]", r"\s")
    out = out.replace("[:digit:]", "0-9")
    out = out.replace("[:alnum:]", "A-Za-z0-9")
    out = out.replace("[:alpha:]", "A-Za-z")
    out = out.replace("[:lower:]", "a-z")
    out = out.replace("[:upper:]", "A-Z")
    out = out.replace("[:word:]", r"\w")
    return out


def regex_search_posix(pattern: str, value: str) -> bool:
    if not pattern:
        return False
    translated = posix_ere_to_python(pattern)
    try:
        return re.search(translated, value) is not None
    except re.error:
        return False


@dataclass
class GuardPaths:
    project_dir: Path
    workflow_dir: Path
    config_dir: Path
    output_dir: Path
    spec_dir: Path
    state_dir: Path
    state_spec_dir: Path
    log_dir: Path
    runtime_file: Path
    current_task_file: Path
    event_log: Path
    risk_file: Path
    architecture_rules_file: Path
    links_file: Path
    state_file: Path
    discovery_file: Path
    spec_file: Path
    tasks_file: Path
    module_linkage_file: Path
    ux_spec_file: Path


def build_paths(project_dir: Path) -> GuardPaths:
    workflow_dir = project_dir / ".claude" / "workflow"
    config_dir = workflow_dir / "config"
    output_dir = project_dir / ".rpi-outfile"
    spec_dir = output_dir / "specs"
    state_dir = output_dir / "state"
    state_spec_dir = state_dir / "spec"
    log_dir = output_dir / "logs"
    return GuardPaths(
        project_dir=project_dir,
        workflow_dir=workflow_dir,
        config_dir=config_dir,
        output_dir=output_dir,
        spec_dir=spec_dir,
        state_dir=state_dir,
        state_spec_dir=state_spec_dir,
        log_dir=log_dir,
        runtime_file=config_dir / "runtime.json",
        current_task_file=state_dir / "current_task.json",
        event_log=log_dir / "events.jsonl",
        risk_file=config_dir / "risk_matrix.json",
        architecture_rules_file=config_dir / "architecture.rules.json",
        links_file=state_spec_dir / "links.json",
        state_file=state_spec_dir / "state.json",
        discovery_file=spec_dir / "l0" / "discovery.md",
        spec_file=spec_dir / "l0" / "spec.md",
        tasks_file=spec_dir / "l0" / "tasks.md",
        module_linkage_file=spec_dir / "l0" / "module-linkage.md",
        ux_spec_file=spec_dir / "l0" / "ux-spec.md",
    )


def ensure_layout(paths: GuardPaths) -> None:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.spec_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.state_spec_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    if not paths.event_log.exists():
        paths.event_log.touch()


def load_runtime(paths: GuardPaths) -> Dict[str, Any]:
    return read_json_obj(paths.runtime_file)


def runtime_bool(runtime: Dict[str, Any], key: str, default: bool) -> bool:
    return bool_value(runtime.get(key, default), default)


def runtime_str(runtime: Dict[str, Any], key: str, default: str) -> str:
    raw = runtime.get(key, default)
    if raw is None:
        return default
    return str(raw)


def runtime_list(runtime: Dict[str, Any], key: str, default: Sequence[str]) -> List[str]:
    raw = runtime.get(key, default)
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        parts = [x.strip() for x in raw.split(",")]
        return [x for x in parts if x]
    return [str(x).strip() for x in default if str(x).strip()]


def safe_path_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def rel_path_from_project(path: Path, project_root: Path) -> str:
    abs_path = safe_path_resolve(path)
    root = safe_path_resolve(project_root)
    try:
        return normalize_path(str(abs_path.relative_to(root)))
    except Exception:
        text = normalize_path(str(abs_path))
        prefix = normalize_path(str(root)).rstrip("/") + "/"
        if text.startswith(prefix):
            return text[len(prefix) :]
        return text


def current_branch(project_dir: Path) -> str:
    try:
        check = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--is-inside-work-tree"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check.returncode != 0:
            return ""
        branch = subprocess.run(
            ["git", "-C", str(project_dir), "branch", "--show-current"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (branch.stdout or "").strip()
    except Exception:
        return ""


def normalize_tool(tool: str) -> str:
    if tool in {"Write", "Edit", "MultiEdit"}:
        return "Edit"
    return tool


def risk_level_score(level: str) -> int:
    return {"R0": 0, "R1": 1, "R2": 2, "R3": 3}.get(level, 0)


def risk_decision_score(decision: str) -> int:
    return {"allow": 0, "ask": 1, "deny": 2}.get(decision, 0)


def profile_exists(matrix: Dict[str, Any], name: str) -> bool:
    profiles = matrix.get("profiles", {})
    return isinstance(profiles, dict) and name in profiles


def select_profile_by_selector(
    matrix: Dict[str, Any],
    selector_type: str,
    target: str,
) -> Optional[Tuple[str, str]]:
    selectors = matrix.get("selectors", {})
    rows = selectors.get(selector_type, []) if isinstance(selectors, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        pattern = str(row.get("pattern", "") or "")
        profile = str(row.get("profile", "") or "")
        if not pattern or not profile:
            continue
        if not profile_exists(matrix, profile):
            continue
        if regex_search_posix(pattern, target):
            return profile, f"{selector_type}:{pattern}"
    return None


def select_risk_profile(
    matrix: Dict[str, Any],
    runtime: Dict[str, Any],
    tool: str,
    value: str,
    branch: str,
    profile_override: str = "",
) -> Tuple[str, str]:
    if profile_override:
        return profile_override, "arg"

    runtime_override = runtime_str(runtime, "risk_profile_override", "").strip()
    if runtime_override and profile_exists(matrix, runtime_override):
        return runtime_override, "runtime"

    if tool == "Edit":
        selected = select_profile_by_selector(matrix, "path", value)
        if selected:
            return selected
    if tool == "Bash":
        selected = select_profile_by_selector(matrix, "command", value)
        if selected:
            return selected
    if branch:
        selected = select_profile_by_selector(matrix, "branch", branch)
        if selected:
            return selected

    default_profile = str(matrix.get("default_profile", "dev") or "dev")
    if not profile_exists(matrix, default_profile):
        default_profile = "dev"
    return default_profile, "default"


def assess_risk(
    project_dir: Path,
    tool: str,
    value: str,
    profile_override: str = "",
) -> Dict[str, Any]:
    paths = build_paths(project_dir)
    runtime = load_runtime(paths)
    matrix = read_json_obj(paths.risk_file)

    if not matrix:
        return {
            "matched": False,
            "level": "R0",
            "decision": "allow",
            "reason": "risk matrix not configured",
            "rule_id": "",
            "profile": "dev",
            "profile_source": "missing_file",
            "branch": "",
        }

    if profile_override and not profile_exists(matrix, profile_override):
        profiles = matrix.get("profiles", {})
        available = sorted(profiles.keys()) if isinstance(profiles, dict) else []
        detail = f" (available: {', '.join(available)})" if available else ""
        raise ValueError(f"Unknown profile: {profile_override}{detail}")

    matrix_enabled = bool_value(matrix.get("enabled", True), True)
    runtime_enabled = runtime_bool(runtime, "risk_matrix_enabled", True)
    if not matrix_enabled or not runtime_enabled:
        return {
            "matched": False,
            "level": "R0",
            "decision": "allow",
            "reason": "risk matrix disabled",
            "rule_id": "",
            "profile": "dev",
            "profile_source": "disabled",
            "branch": "",
        }

    normalized_tool = normalize_tool(tool)
    branch = current_branch(project_dir)
    profile, profile_source = select_risk_profile(
        matrix=matrix,
        runtime=runtime,
        tool=normalized_tool,
        value=value,
        branch=branch,
        profile_override=profile_override.strip(),
    )

    default_cfg = matrix.get("default", {})
    if not isinstance(default_cfg, dict):
        default_cfg = {}
    best_level = str(default_cfg.get("level", "R0") or "R0")
    best_decision = str(default_cfg.get("decision", "allow") or "allow")
    best_reason = str(default_cfg.get("reason", "No risk rule matched") or "No risk rule matched")
    best_rule_id = ""
    best_score = -1
    matched = False

    rules = matrix.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    for row in rules:
        if not isinstance(row, dict):
            continue
        rule_tool = str(row.get("tool", "any") or "any")
        pattern = str(row.get("pattern", "") or "")
        if rule_tool not in {"any", normalized_tool} or not pattern:
            continue
        if not regex_search_posix(pattern, value):
            continue

        matched = True
        level = str(row.get("level", "R1") or "R1")
        decision = str(row.get("decision", "ask") or "ask")
        reason = str(row.get("reason", "High-risk operation requires review") or "High-risk operation requires review")
        rule_id = str(row.get("id", "") or "")

        score = risk_level_score(level)
        best_decision_score = risk_decision_score(best_decision)
        cur_decision_score = risk_decision_score(decision)
        if score > best_score or (score == best_score and cur_decision_score > best_decision_score):
            best_score = score
            best_level = level
            best_decision = decision
            best_reason = reason
            best_rule_id = rule_id

    profiles = matrix.get("profiles", {})
    profile_cfg = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
    if not isinstance(profile_cfg, dict):
        profile_cfg = {}

    decision_overrides = profile_cfg.get("decision_overrides", {})
    if isinstance(decision_overrides, dict):
        override = str(decision_overrides.get(best_level, "") or "")
        if override:
            best_decision = override
            best_reason = f"{best_reason} (profile={profile} override level={best_level} => {best_decision})"

    force_levels_raw = profile_cfg.get("force_approval_levels", [])
    force_levels = force_levels_raw if isinstance(force_levels_raw, list) else []
    force_approval = best_level in {str(x) for x in force_levels}
    if force_approval and best_decision == "allow":
        best_decision = "ask"
        best_reason = f"{best_reason} (profile={profile} forced approval for level={best_level})"

    high_requires_approval = runtime_bool(runtime, "risk_high_requires_approval", True)
    if best_decision == "ask" and not high_requires_approval and not force_approval:
        best_decision = "allow"
        best_reason = f"{best_reason} (downgraded: risk_high_requires_approval=false)"

    return {
        "matched": matched,
        "level": best_level,
        "decision": best_decision,
        "reason": best_reason,
        "rule_id": best_rule_id,
        "profile": profile,
        "profile_source": profile_source,
        "branch": branch,
    }


def spec_state_paths(project_dir: Path) -> spec_state_tool.Paths:
    state_spec_dir = project_dir / ".rpi-outfile" / "state" / "spec"
    return spec_state_tool.Paths(
        project=project_dir,
        runtime_file=project_dir / ".claude" / "workflow" / "config" / "runtime.json",
        aliases_file=project_dir / ".claude" / "workflow" / "config" / "spec_aliases.json",
        state_spec_dir=state_spec_dir,
        state_file=state_spec_dir / "state.json",
        meta_file=state_spec_dir / "state.meta",
        verify_file=state_spec_dir / "verification.json",
        source_file=project_dir / ".rpi-outfile" / "specs" / "l0" / "spec-source.json",
        discovery_file=project_dir / ".rpi-outfile" / "specs" / "l0" / "discovery.md",
        spec_file=project_dir / ".rpi-outfile" / "specs" / "l0" / "spec.md",
        tasks_file=project_dir / ".rpi-outfile" / "specs" / "l0" / "tasks.md",
    )


def build_spec_state(project_dir: Path, quiet: bool = True) -> int:
    return spec_state_tool.build_state(spec_state_paths(project_dir), quiet=quiet, print_path=False, force=False)


def verify_spec_state(project_dir: Path, scope: str, quiet: bool = True) -> Dict[str, Any]:
    paths = spec_state_paths(project_dir)
    build_rc = spec_state_tool.build_state(paths, quiet=True, print_path=False, force=False)
    if build_rc != 0:
        return {"status": "error", "build_rc": build_rc, "errors": ["spec_state_build failed"], "warnings": []}

    rc = spec_state_tool.verify_state(paths, scope=scope, quiet=True, json_output=False)
    result = read_json_obj(paths.verify_file)
    errors = result.get("errors", [])
    if not isinstance(errors, list):
        errors = []
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    return {
        "status": "pass" if rc == 0 else "fail",
        "rc": rc,
        "errors": [str(x) for x in errors if str(x).strip()],
        "warnings": [str(x) for x in warnings if str(x).strip()],
    }


def build_spec_links(project_dir: Path, quiet: bool = True) -> Dict[str, Any]:
    paths = build_paths(project_dir)
    ensure_layout(paths)
    build_rc = build_spec_state(project_dir, quiet=True)
    if build_rc != 0:
        return {"status": "fail", "rc": build_rc, "message": "failed to build state before linking"}

    nodes: List[Dict[str, str]] = []
    edges: List[Dict[str, str]] = []

    def add_node(node_id: str, node_type: str, path: str, label: str) -> None:
        nodes.append({"id": node_id, "type": node_type, "path": path, "label": label})

    def add_edge(src: str, dst: str, relation: str) -> None:
        edges.append({"from": src, "to": dst, "relation": relation})

    add_node("spec:discovery", "spec", ".rpi-outfile/specs/l0/discovery.md", "Discovery")
    add_node("spec:spec", "spec", ".rpi-outfile/specs/l0/spec.md", "L0 Spec")
    add_node("spec:tasks", "spec", ".rpi-outfile/specs/l0/tasks.md", "Tasks")
    add_node("spec:milestones", "spec", ".rpi-outfile/specs/l0/milestones.md", "Milestones")
    add_node("spec:epic", "spec", ".rpi-outfile/specs/l0/epic.md", "Epic")

    add_edge("spec:discovery", "spec:spec", "defines_contract_input")
    add_edge("spec:discovery", "spec:tasks", "defines_scope_input")
    add_edge("spec:spec", "spec:tasks", "constrains_implementation")
    add_edge("spec:epic", "spec:milestones", "decomposes_to")
    add_edge("spec:milestones", "spec:tasks", "decomposes_to")

    state_json = read_json_obj(paths.state_file)
    task_ids_raw = state_json.get("tasks", {}).get("task_ids", [])
    task_ids = task_ids_raw if isinstance(task_ids_raw, list) else []
    for tid_any in task_ids:
        tid = str(tid_any).strip()
        if not tid:
            continue
        node_id = f"task:{tid}"
        add_node(node_id, "task", f".rpi-outfile/specs/l0/tasks.md#{tid}", tid)
        add_edge(node_id, "spec:tasks", "declared_in")
        add_edge(node_id, "spec:spec", "implements")

    current = read_json_obj(paths.current_task_file)
    active_task = str(current.get("task_id", "") or "")
    active_status = str(current.get("status", "idle") or "idle")
    if active_task and active_status == "in_progress":
        active_node = f"session:{active_task}"
        add_node(active_node, "session", ".rpi-outfile/state/current_task.json", f"Active Session {active_task}")

        spec_refs = current.get("spec_refs", [])
        if isinstance(spec_refs, list):
            for raw_ref in spec_refs:
                ref = str(raw_ref).strip()
                if not ref:
                    continue
                ref_id = "ref:" + re.sub(r"[^a-zA-Z0-9._:-]", "_", ref)
                add_node(ref_id, "spec_ref", ref, ref)
                add_edge(active_node, ref_id, "binds_spec_ref")

        context_refs = current.get("context_refs", [])
        if isinstance(context_refs, list):
            for raw_ref in context_refs:
                ref = str(raw_ref).strip()
                if not ref:
                    continue
                ref_id = "ctx:" + re.sub(r"[^a-zA-Z0-9._:-]", "_", ref)
                add_node(ref_id, "context_ref", ref, ref)
                add_edge(active_node, ref_id, "binds_context_ref")

    unique_nodes: List[Dict[str, str]] = []
    seen_nodes = set()
    for node in nodes:
        node_id = node["id"]
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        unique_nodes.append(node)

    unique_edges: List[Dict[str, str]] = []
    seen_edges = set()
    for edge in edges:
        key = (edge["from"], edge["to"], edge["relation"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        unique_edges.append(edge)

    payload = {
        "generated_at": utc_now(),
        "nodes": unique_nodes,
        "edges": unique_edges,
    }
    write_json(paths.links_file, payload)
    if not quiet:
        print(f"Built spec link graph: {paths.links_file}")
        print(f"nodes={len(unique_nodes)} edges={len(unique_edges)}")
    return {
        "status": "pass",
        "path": str(paths.links_file),
        "nodes": len(unique_nodes),
        "edges": len(unique_edges),
    }


def section_has_content(spec_text: str, heading: str) -> bool:
    lines = spec_text.splitlines()
    in_section = False
    heading_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$")
    section_label_only = {
        "In Scope:",
        "Out of Scope:",
        "核心实体：",
        "关键字段：",
        "输入契约：",
        "输出契约：",
        "错误码/失败语义：",
        "正向路径：",
        "异常路径：",
        "回退策略：",
        "性能预算：",
        "成本预算：",
        "稳定性预算：",
    }

    for raw in lines:
        clean = raw.replace("*", "")
        stripped = clean.strip()
        if heading_re.match(stripped):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", stripped) and not re.match(r"^###", stripped):
            in_section = False
        if not in_section:
            continue
        if not stripped:
            continue
        if re.match(r"^[-*]\s*$", stripped):
            continue
        if re.match(r"^[0-9]+\.\s*$", stripped):
            continue
        if re.match(r"^#{3,6}\s+", stripped):
            continue
        if stripped in section_label_only:
            continue
        return True
    return False


def check_contract_spec(project_dir: Path, quiet: bool = False) -> Dict[str, Any]:
    paths = build_paths(project_dir)
    errors: List[str] = []
    if not paths.spec_file.exists():
        errors.append("缺少文件 .rpi-outfile/specs/l0/spec.md")
    else:
        text = paths.spec_file.read_text(encoding="utf-8", errors="ignore")
        required_sections = [
            "架构边界",
            "数据模型",
            "接口契约",
            "关键流程",
            "验收与异常矩阵",
        ]
        for section in required_sections:
            if not section_has_content(text, section):
                errors.append(f"章节 [{section}] 内容不足，无法作为实现契约")

    if errors:
        if not quiet:
            print("contract spec check failed:", file=sys.stderr)
            for err in errors:
                print(f"- {err}", file=sys.stderr)
            print("Fix file: .rpi-outfile/specs/l0/spec.md", file=sys.stderr)
        return {"status": "fail", "errors": errors}

    if not quiet:
        print("contract spec check passed")
    return {"status": "pass", "errors": []}


def check_discovery(project_dir: Path, quiet: bool = False) -> Dict[str, Any]:
    result = verify_spec_state(project_dir, scope="discovery", quiet=True)
    if result.get("status") == "error":
        if not quiet:
            print("discovery check failed: unable to build spec state", file=sys.stderr)
        return {"status": "error", "errors": result.get("errors", []), "warnings": []}
    if result.get("status") == "pass":
        warnings = result.get("warnings", [])
        if not quiet:
            print("discovery check passed")
            if isinstance(warnings, list):
                for w in warnings:
                    print(f"- WARNING: {w}")
        return {"status": "pass", "errors": [], "warnings": warnings if isinstance(warnings, list) else []}

    errors = result.get("errors", [])
    if not quiet:
        print("discovery check failed:", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        print("Fix file: .rpi-outfile/specs/l0/discovery.md", file=sys.stderr)
    return {"status": "fail", "errors": errors, "warnings": result.get("warnings", [])}


def check_scope_guard(project_dir: Path, quiet: bool = False) -> Dict[str, Any]:
    result = verify_spec_state(project_dir, scope="scope_guard", quiet=True)
    if result.get("status") == "error":
        if not quiet:
            print("scope guard check failed: unable to build spec state", file=sys.stderr)
        return {"status": "error", "errors": result.get("errors", [])}
    if result.get("status") == "pass":
        if not quiet:
            print("scope guard check passed")
        return {"status": "pass", "errors": []}

    errors = result.get("errors", [])
    if not quiet:
        print("scope guard check failed:", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        print(
            "Fix files: .rpi-outfile/specs/l0/discovery.md .rpi-outfile/specs/l0/tasks.md .rpi-outfile/specs/l0/spec.md",
            file=sys.stderr,
        )
    return {"status": "fail", "errors": errors}


def line_is_reference(line: str) -> bool:
    if re.search(r"(import|from|require|bash[ \t]|sh[ \t])", line):
        return True
    if re.match(r"^[ \t]*source[ \t]+", line):
        return True
    if re.match(r"^[ \t]*\.[ \t]+", line):
        return True
    return False


def line_is_source(line: str) -> bool:
    return bool(re.match(r"^[ \t]*source[ \t]+", line) or re.match(r"^[ \t]*\.[ \t]+", line))


def file_matches_extensions(path: Path, include_extensions: Sequence[str]) -> bool:
    if not path.is_file():
        return False
    ext = path.suffix.lstrip(".").lower()
    allowed = {str(x).lstrip(".").lower() for x in include_extensions}
    return ext in allowed


def collect_rule_files(
    project_dir: Path,
    from_paths: Sequence[str],
    include_extensions: Sequence[str],
    exclude_dir_names: Sequence[str],
    max_files: int,
) -> Tuple[List[Path], bool]:
    files: List[Path] = []
    truncated = False
    project_root = safe_path_resolve(project_dir)
    exclude_set = {x.strip().lower() for x in exclude_dir_names if str(x).strip()}
    scan_limit = max_files if max_files > 0 else 0

    def push(path: Path) -> None:
        nonlocal truncated
        if scan_limit and len(files) >= scan_limit:
            truncated = True
            return
        files.append(path)

    for raw in from_paths:
        rel = str(raw or "").strip()
        if not rel:
            continue
        abs_path = project_dir / rel
        if abs_path.is_dir():
            for root, dirs, names in os.walk(abs_path, topdown=True):
                if exclude_set:
                    dirs[:] = [d for d in dirs if d.lower() not in exclude_set]
                for name in names:
                    item = Path(root) / name
                    if file_matches_extensions(item, include_extensions):
                        push(item)
                        if truncated:
                            break
                if truncated:
                    break
        elif file_matches_extensions(abs_path, include_extensions):
            push(abs_path)
        if truncated:
            break
    uniq = []
    seen = set()
    for p in files:
        s = str(safe_path_resolve(p))
        if s in seen:
            continue
        seen.add(s)
        uniq.append(p)
    uniq.sort(key=lambda item: rel_path_from_project(item, project_root))
    if scan_limit and len(uniq) > scan_limit:
        uniq = uniq[:scan_limit]
        truncated = True
    return uniq, truncated


def architecture_check(
    project_dir: Path,
    quiet: bool = False,
    json_output: bool = False,
    require_rules: bool = False,
) -> Dict[str, Any]:
    paths = build_paths(project_dir)
    ensure_layout(paths)
    rules_json = read_json_obj(paths.architecture_rules_file)
    runtime = load_runtime(paths)
    scan_max_files = int_value(runtime.get("architecture_scan_max_files", 2000), 2000)
    if scan_max_files < 0:
        scan_max_files = 0
    scan_exclude_dirs = runtime_list(
        runtime,
        "architecture_scan_exclude_dirs",
        [
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
    )

    def emit(
        status: str,
        message: str,
        violations: List[Dict[str, Any]],
        warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        warning_rows = warnings if isinstance(warnings, list) else []
        result = {"status": status, "message": message, "violations": violations, "warnings": warning_rows}
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        elif not quiet:
            if status == "pass":
                print(f"architecture check passed: {message}")
                for warning in warning_rows:
                    print(f"- WARN: {warning}")
            else:
                print(f"architecture check failed: {message}", file=sys.stderr)
                for row in violations:
                    print(
                        f"- [{row['severity']}] [{row['rule_id']}] {row['file']}:{row['line']} => {row['message']} | {row['match']}",
                        file=sys.stderr,
                    )
                for warning in warning_rows:
                    print(f"- WARN: {warning}", file=sys.stderr)
        return result

    if not paths.architecture_rules_file.exists():
        if require_rules:
            return emit("fail", "missing rules file", [])
        return emit("pass", "rules file not found (skip)", [])

    enabled = bool_value(rules_json.get("enabled", False), False)
    if not enabled:
        if require_rules:
            return emit("fail", "rules file exists but enabled=false", [])
        return emit("pass", "rules disabled (skip)", [])

    rules_raw = rules_json.get("rules", [])
    rules = rules_raw if isinstance(rules_raw, list) else []
    if len(rules) == 0:
        if require_rules:
            return emit("fail", "no architecture rules defined", [])
        return emit("pass", "no architecture rules defined (skip)", [])

    include_exts_raw = rules_json.get("include_extensions", ["sh"])
    include_exts = include_exts_raw if isinstance(include_exts_raw, list) and include_exts_raw else ["sh"]

    violations: List[Dict[str, Any]] = []
    scan_warnings: List[str] = []
    project_root = safe_path_resolve(project_dir)
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        rule_type = str(rule.get("type", "") or "")
        if rule_type not in {"import_forbid", "source_allowlist"}:
            continue

        rule_id = str(rule.get("id", f"rule_{idx}") or f"rule_{idx}")
        severity = str(rule.get("severity", "error") or "error")
        message = str(rule.get("message", "architecture rule violation") or "architecture rule violation")
        from_raw = rule.get("from", [])
        from_paths = from_raw if isinstance(from_raw, list) else []
        files, truncated = collect_rule_files(
            project_dir,
            [str(x) for x in from_paths],
            [str(x) for x in include_exts],
            scan_exclude_dirs,
            scan_max_files,
        )
        if truncated:
            scan_warnings.append(
                f"rule {rule_id}: scan reached limit ({scan_max_files}) — "
                "adjust runtime architecture_scan_max_files or architecture_scan_exclude_dirs if needed"
            )

        exclude_raw = rule.get("exclude_files", [])
        exclude_patterns = [str(x) for x in exclude_raw] if isinstance(exclude_raw, list) else []

        for src in files:
            rel_file = rel_path_from_project(src, project_root)
            excluded = any(fnmatch.fnmatch(rel_file, pat) for pat in exclude_patterns if pat)
            if excluded:
                continue

            try:
                lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            for line_no, line in enumerate(lines, start=1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue

                if rule_type == "import_forbid":
                    if not line_is_reference(line):
                        continue
                    forbid_raw = rule.get("forbid", [])
                    forbid_tokens = [str(x) for x in forbid_raw] if isinstance(forbid_raw, list) else []
                    for token in forbid_tokens:
                        if not token:
                            continue
                        py_token = token.replace("/", ".")
                        if token in line or py_token in line:
                            violations.append(
                                {
                                    "rule_id": rule_id,
                                    "file": rel_file,
                                    "line": line_no,
                                    "match": line,
                                    "severity": severity,
                                    "message": message,
                                }
                            )
                            break
                    continue

                if rule_type == "source_allowlist":
                    if not line_is_source(line):
                        continue
                    allow_raw = rule.get("allow", [])
                    allow_tokens = [str(x) for x in allow_raw] if isinstance(allow_raw, list) else []
                    allowed = any(token and token in line for token in allow_tokens)
                    if not allowed:
                        violations.append(
                            {
                                "rule_id": rule_id,
                                "file": rel_file,
                                "line": line_no,
                                "match": line,
                                "severity": severity,
                                "message": message,
                            }
                        )

    if violations:
        append_jsonl_line(
            paths.event_log,
            json.dumps(
                {
                    "ts": utc_now(),
                    "event": "architecture_check",
                    "status": "fail",
                    "violation_count": len(violations),
                    "warning_count": len(scan_warnings),
                },
                ensure_ascii=False,
            ),
        )
        return emit("fail", f"found {len(violations)} architecture violations", violations, scan_warnings)

    append_jsonl_line(
        paths.event_log,
        json.dumps(
            {
                "ts": utc_now(),
                "event": "architecture_check",
                "status": "pass",
                "warning_count": len(scan_warnings),
            },
            ensure_ascii=False,
        ),
    )
    return emit("pass", "no architecture violations", [], scan_warnings)


def check_precode_bundle(
    project_dir: Path,
    include_architecture: bool = False,
    architecture_require_rules: bool = False,
) -> Dict[str, Any]:
    failures: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {
        "spec_verify": "unknown",
        "contract": "unknown",
        "architecture": "skipped",
    }

    verify = verify_spec_state(project_dir, scope="all", quiet=True)
    verify_status = str(verify.get("status", "error"))
    if verify_status == "pass":
        details["spec_verify"] = "pass"
    elif verify_status == "fail":
        details["spec_verify"] = "fail"
        failures.append("discovery/scope guard check failed")
        verify_errors = verify.get("errors", [])
        if isinstance(verify_errors, list):
            warnings.extend([str(x).strip() for x in verify_errors if str(x).strip()])
    else:
        details["spec_verify"] = "error"
        failures.append("discovery/scope guard engine error")
        verify_errors = verify.get("errors", [])
        if isinstance(verify_errors, list):
            warnings.extend([str(x).strip() for x in verify_errors if str(x).strip()])

    contract = check_contract_spec(project_dir, quiet=True)
    if str(contract.get("status", "fail")) == "pass":
        details["contract"] = "pass"
    else:
        details["contract"] = "fail"
        failures.append("contract check failed")
        contract_errors = contract.get("errors", [])
        if isinstance(contract_errors, list):
            warnings.extend([str(x).strip() for x in contract_errors if str(x).strip()])

    if include_architecture:
        arch = architecture_check(
            project_dir=project_dir,
            quiet=True,
            json_output=False,
            require_rules=architecture_require_rules,
        )
        if str(arch.get("status", "fail")) == "pass":
            details["architecture"] = "pass"
        else:
            details["architecture"] = "fail"
            if architecture_require_rules:
                failures.append("architecture check failed (require-rules)")
            else:
                failures.append("architecture check failed")
        arch_warnings = arch.get("warnings", [])
        if isinstance(arch_warnings, list):
            warnings.extend([str(x).strip() for x in arch_warnings if str(x).strip()])

    uniq_warnings: List[str] = []
    seen_warning = set()
    for item in warnings:
        if item in seen_warning:
            continue
        seen_warning.add(item)
        uniq_warnings.append(item)

    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "warnings": uniq_warnings,
        "checks": details,
    }


def check_linkage(project_dir: Path, quiet: bool = False) -> Dict[str, Any]:
    paths = build_paths(project_dir)

    def log(line: str) -> None:
        if not quiet:
            print(line)

    if not paths.module_linkage_file.exists():
        if not quiet:
            log("ERROR: 缺少模块联动规范文件: .rpi-outfile/specs/l0/module-linkage.md")
            log("")
            log("请先完成以下步骤：")
            log("1. 使用 /rpi-check skeleton-init 初始化全局骨架")
            log("2. 定义模块职责边界和联动关系")
            log("3. 重新执行检查")
            log("")
            log("模板位置：.rpi-blueprint/specs/l0/module-linkage.template.md")
        return {
            "status": "fail",
            "pass_count": 0,
            "fail_count": 1,
            "issues": ["module-linkage.md missing"],
            "warnings": [],
        }

    current = read_json_obj(paths.current_task_file)
    task_id = str(current.get("task_id", "unknown") or "unknown")
    if task_id in {"", "unknown", "null"}:
        if not quiet:
            log("WARN: 无活动任务，跳过检查")
        return {"status": "pass", "pass_count": 0, "fail_count": 0, "issues": [], "warnings": ["no_active_task"]}

    text = paths.module_linkage_file.read_text(encoding="utf-8", errors="ignore")
    text_lower = text.lower()
    pass_count = 0
    fail_count = 0
    issues: List[str] = []
    warnings: List[str] = []

    def contains_any_alias(aliases: Sequence[str]) -> bool:
        for alias in aliases:
            norm = str(alias).strip().lower()
            if norm and norm in text_lower:
                return True
        return False

    must_sections: List[Tuple[Sequence[str], str]] = [
        (["模块联动关系", "module linkage", "module interaction"], "module-linkage.md 缺少联动关系表（Module Linkage）"),
        (["数据流向", "data flow"], "module-linkage.md 缺少数据流向规则（Data Flow）"),
        (["技术实现标准", "technical standards", "implementation standards"], "module-linkage.md 缺少技术实现标准（Technical Standards）"),
    ]
    for aliases, issue in must_sections:
        if contains_any_alias(aliases):
            pass_count += 1
        else:
            fail_count += 1
            issues.append(issue)

    if contains_any_alias(["异常处理", "exception handling", "error handling"]):
        pass_count += 1
    else:
        warnings.append("建议补充异常处理规范（Exception Handling）")

    if quiet:
        return {
            "status": "pass" if fail_count == 0 else "fail",
            "pass_count": pass_count,
            "fail_count": fail_count,
            "issues": issues,
            "warnings": warnings,
        }

    log("========================================")
    log("模块联动完整性检查")
    log("========================================")
    log("")
    log(f"检查任务：{task_id}")
    log("")
    log("检查 1：模块联动关系定义...")
    log("检查 2：数据流向规则...")
    log("检查 3：技术实现标准...")
    log("检查 4：异常处理规范...")
    if warnings:
        log("WARN: 建议补充异常处理规范")
    log("")
    log("========================================")
    log("检查结果")
    log("========================================")
    log("")

    if fail_count == 0:
        log("PASS: 模块联动完整性检查通过")
        log("")
        log(f"通过 {pass_count} 个检查")
        return {
            "status": "pass",
            "pass_count": pass_count,
            "fail_count": fail_count,
            "issues": issues,
            "warnings": warnings,
        }

    log(f"FAIL: 发现 {fail_count} 个问题：")
    log("")
    for idx, issue in enumerate(issues, start=1):
        log(f"{idx}. {issue}")
    log("")
    log(f"通过 {pass_count} 个检查")
    log("")
    log("请修复以上问题后重新检查")
    log("")
    log("参考：")
    log("- 模板：.rpi-blueprint/specs/l0/module-linkage.template.md")
    log("- 命令：/rpi-check skeleton-init")
    return {
        "status": "fail",
        "pass_count": pass_count,
        "fail_count": fail_count,
        "issues": issues,
        "warnings": warnings,
    }


def is_frontend_file(path: str) -> bool:
    normalized = normalize_path(path)
    if normalized.endswith((".vue", ".jsx", ".tsx", ".svelte")):
        return True
    return any(token in normalized for token in ("/components/", "/views/", "/pages/", "/layouts/"))


def ux_precheck(project_dir: Path, target_path: str, tool_name: str = "Edit") -> Dict[str, Any]:
    paths = build_paths(project_dir)
    runtime = load_runtime(paths)
    require_ux_spec = runtime_bool(runtime, "require_ux_spec", False)
    frontend_ux_strict = runtime_bool(runtime, "frontend_ux_strict", False)

    normalized = normalize_path(target_path)
    if not is_frontend_file(normalized):
        return {"status": "pass", "reason": "", "warnings": []}

    if require_ux_spec and not paths.ux_spec_file.exists():
        reason = (
            "Blocked: 前端任务缺少 UX 规范\n\n"
            "当前任务涉及前端代码修改，但未定义 UX 交互规范。\n\n"
            "请先完成以下步骤：\n"
            "1. 根据模板补全 .rpi-outfile/specs/l0/ux-spec.md\n"
            "2. 使用 /rpi-check ux 验证规范完整性\n"
            "3. 重新启动任务\n\n"
            "模板位置：.rpi-blueprint/specs/l0/ux-spec.template.md"
        )
        return {"status": "deny", "reason": reason, "warnings": []}

    warnings: List[str] = []
    if paths.ux_spec_file.exists() and not frontend_ux_strict:
        text = paths.ux_spec_file.read_text(encoding="utf-8", errors="ignore")
        if "表格 CRUD 标准实现" not in text:
            warnings.append("UX 规范不完整：建议补全“表格 CRUD 标准实现/禁止行为清单”后继续。")

    _ = tool_name  # reserved for parity with shell interface
    return {"status": "pass", "reason": "", "warnings": warnings}


def detect_project_dir(explicit: str) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return resolve_project_dir(Path(__file__).resolve().parent)


def cmd_risk_assess(ns: argparse.Namespace) -> int:
    if not ns.tool or not ns.value:
        print("tool and value are required", file=sys.stderr)
        return 1
    try:
        result = assess_risk(
            project_dir=detect_project_dir(ns.project_dir),
            tool=ns.tool,
            value=ns.value,
            profile_override=ns.profile or "",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if ns.json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result.get("decision", "allow"))
    return 0


def cmd_spec_link(ns: argparse.Namespace) -> int:
    result = build_spec_links(detect_project_dir(ns.project_dir), quiet=ns.quiet)
    return 0 if result.get("status") == "pass" else int_value(result.get("rc"), 1) or 1


def cmd_check_discovery(ns: argparse.Namespace) -> int:
    result = check_discovery(detect_project_dir(ns.project_dir), quiet=ns.quiet)
    return 0 if result.get("status") == "pass" else 1


def cmd_check_contract(ns: argparse.Namespace) -> int:
    result = check_contract_spec(detect_project_dir(ns.project_dir), quiet=ns.quiet)
    return 0 if result.get("status") == "pass" else 1


def cmd_check_scope(ns: argparse.Namespace) -> int:
    result = check_scope_guard(detect_project_dir(ns.project_dir), quiet=ns.quiet)
    return 0 if result.get("status") == "pass" else 1


def cmd_architecture_check(ns: argparse.Namespace) -> int:
    result = architecture_check(
        detect_project_dir(ns.project_dir),
        quiet=ns.quiet,
        json_output=ns.json_output,
        require_rules=ns.require_rules,
    )
    return 0 if result.get("status") == "pass" else 1


def cmd_linkage_check(ns: argparse.Namespace) -> int:
    result = check_linkage(detect_project_dir(ns.project_dir), quiet=ns.quiet)
    return 0 if result.get("status") == "pass" else 1


def cmd_ux_precheck(ns: argparse.Namespace) -> int:
    result = ux_precheck(
        project_dir=detect_project_dir(ns.project_dir),
        target_path=ns.target_path,
        tool_name=ns.tool_name,
    )
    for warning in result.get("warnings", []):
        print(f"WARN: {warning}", file=sys.stderr)
    if result.get("status") == "deny":
        print(result.get("reason", "UX precheck failed"), file=sys.stderr)
        return 1
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RPI guardrails engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_risk = sub.add_parser("risk-assess")
    p_risk.add_argument("--tool", default="")
    p_risk.add_argument("--value", default="")
    p_risk.add_argument("--profile", default="")
    p_risk.add_argument("--json", action="store_true", dest="json_output")
    p_risk.add_argument("--project-dir", default="")

    p_link = sub.add_parser("spec-link")
    p_link.add_argument("--quiet", action="store_true")
    p_link.add_argument("--project-dir", default="")

    p_discovery = sub.add_parser("check-discovery")
    p_discovery.add_argument("--quiet", action="store_true")
    p_discovery.add_argument("--project-dir", default="")

    p_contract = sub.add_parser("check-contract")
    p_contract.add_argument("--quiet", action="store_true")
    p_contract.add_argument("--project-dir", default="")

    p_scope = sub.add_parser("check-scope")
    p_scope.add_argument("--quiet", action="store_true")
    p_scope.add_argument("--project-dir", default="")

    p_arch = sub.add_parser("architecture-check")
    p_arch.add_argument("--quiet", action="store_true")
    p_arch.add_argument("--json", action="store_true", dest="json_output")
    p_arch.add_argument("--require-rules", action="store_true")
    p_arch.add_argument("--project-dir", default="")

    p_linkage = sub.add_parser("linkage-check")
    p_linkage.add_argument("--quiet", action="store_true")
    p_linkage.add_argument("--project-dir", default="")

    p_ux = sub.add_parser("ux-precheck")
    p_ux.add_argument("target_path")
    p_ux.add_argument("tool_name", nargs="?", default="Edit")
    p_ux.add_argument("--project-dir", default="")

    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    ns = parse_args(argv)
    if ns.cmd == "risk-assess":
        return cmd_risk_assess(ns)
    if ns.cmd == "spec-link":
        return cmd_spec_link(ns)
    if ns.cmd == "check-discovery":
        return cmd_check_discovery(ns)
    if ns.cmd == "check-contract":
        return cmd_check_contract(ns)
    if ns.cmd == "check-scope":
        return cmd_check_scope(ns)
    if ns.cmd == "architecture-check":
        return cmd_architecture_check(ns)
    if ns.cmd == "linkage-check":
        return cmd_linkage_check(ns)
    if ns.cmd == "ux-precheck":
        return cmd_ux_precheck(ns)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
