#!/usr/bin/env python3
"""PreToolUse full decision core.

This engine owns the entire pre-tool decision pipeline for:
- Edit/Write/MultiEdit
- Bash

Checks covered:
- Active task / spec_refs / TDD policy
- Risk matrix (via risk_assess.sh)
- Spec link enforcement
- Linkage spec and linkage integrity enforcement
- Autonomy budget
- Precode guardrails with cached signature
- UX pre-check hook bridge
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import guardrails_tool as guardrails
import file_lock


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


def load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


def read_hook_input() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raw = os.environ.get("CLAUDE_HOOK_INPUT", "") or os.environ.get("ANTHROPIC_HOOK_INPUT", "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def file_mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def regex_matches(pattern: str, text: str) -> bool:
    if not pattern:
        return False
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return False


def platform_family() -> str:
    plat = sys.platform.lower()
    if plat.startswith("win"):
        return "windows"
    if plat.startswith("darwin"):
        return "macos"
    if plat.startswith("linux"):
        return "linux"
    return "unknown"


def jq_install_hint() -> str:
    family = platform_family()
    if family == "windows":
        if shutil.which("winget"):
            return "winget install jq"
        if shutil.which("choco"):
            return "choco install jq -y"
        if shutil.which("scoop"):
            return "scoop install jq"
        return "Install jq for Windows and ensure 'jq' is in PATH."
    if family == "macos":
        return "brew install jq"
    if family == "linux":
        if shutil.which("apt-get"):
            return "sudo apt-get update && sudo apt-get install -y jq"
        if shutil.which("dnf"):
            return "sudo dnf install -y jq"
        if shutil.which("yum"):
            return "sudo yum install -y jq"
        if shutil.which("apk"):
            return "sudo apk add jq"
        if shutil.which("pacman"):
            return "sudo pacman -Sy --noconfirm jq"
        if shutil.which("zypper"):
            return "sudo zypper --non-interactive install jq"
        return "Install jq with your distro package manager, then retry."
    return "Install jq and ensure it is available on PATH."


def emit_pretool_decision(decision: str, reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def is_test_path(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|__tests__)/", path)) or bool(re.search(r"\.(test|spec)\.[^/]+$", path))


def is_code_path(path: str) -> bool:
    return bool(
        re.search(
            r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|java|kt|rb|rs|php|cs|swift|scala|sh|sql)$",
            path,
        )
    )


def is_framework_internal_path(path: str) -> bool:
    return path.startswith(".claude/") or "/.claude/" in path


def is_planning_path(path: str) -> bool:
    patterns = [".rpi-outfile/", ".rpi-blueprint/"]
    for prefix in patterns:
        if path.startswith(prefix) or f"/{prefix}" in path:
            return True
    base = path.rsplit("/", 1)[-1]
    return base in {"README.md", "prd.md", "CLAUDE.md", "QUICKSTART.md"}


def bash_command_is_test_command(cmd: str) -> bool:
    pattern = (
        r"(pytest|go[ \t]+test|cargo[ \t]+test|mvn[ \t]+test|gradle[ \t]+test|vitest|jest|playwright[ \t]+test|"
        r"(^|[ \t])npm[ \t]+(run[ \t]+)?test([ \t]|$)|"
        r"(^|[ \t])pnpm[ \t]+(run[ \t]+)?test([ \t]|$)|"
        r"(^|[ \t])yarn[ \t]+test([ \t]|$))"
    )
    return bool(re.search(pattern, cmd))


def bash_command_has_control_operators(cmd: str) -> bool:
    return bool(re.search(r"(&&|\|\||;|\||\$\(|`|<|>)", cmd))


def bash_command_is_workflow_maintenance_command(cmd: str) -> bool:
    cmd_stripped = re.sub(r"^[ \t]*cd[ \t]+[^&]*&&[ \t]*", "", cmd)
    cmd_stripped = re.sub(r"[ \t]*[0-9]*>&[0-9]*[ \t]*$", "", cmd_stripped)
    cmd_stripped = re.sub(r"[ \t]*[0-9]*>[ \t]*/dev/null[ \t]*$", "", cmd_stripped)

    if bash_command_has_control_operators(cmd_stripped):
        return False

    if re.match(r"^[ \t]*((bash|sh)[ \t]+)?(\./)?\.claude/hooks/[A-Za-z0-9_.-]+([ \t].*)?$", cmd_stripped):
        return True
    if re.match(r"^[ \t]*((bash|sh)[ \t]+)?(\./)?\.claude/workflow/rpi\.sh([ \t].*)?$", cmd_stripped):
        return True
    return False


def bash_command_is_quality_check_command(cmd: str) -> bool:
    return bool(re.search(r"(lint|typecheck|tsc|ruff[ \t]+check|mypy|eslint|stylelint|flake8)", cmd))


def bash_command_targets_code(cmd: str) -> bool:
    normalized = normalize_path(cmd)
    if bash_command_is_workflow_maintenance_command(normalized):
        return False

    ext_pattern = r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|java|kt|rb|rs|php|cs|swift|scala|sh|sql)([^A-Za-z0-9_]|$)"
    if re.search(ext_pattern, normalized):
        return True
    if re.search(r"(src|app|apps|packages|lib|server|backend|frontend)/", normalized):
        return True
    if re.search(r"(^|[ \t])(git[ \t]+apply|patch([ \t]|$))", normalized):
        return True
    return False


def bash_command_targets_tests(cmd: str) -> bool:
    normalized = normalize_path(cmd)
    return bool(re.search(r"(tests?|__tests__)/", normalized)) or bool(re.search(r"\.(test|spec)\.[A-Za-z0-9_]+", normalized))


def bash_command_has_write_intent(cmd: str) -> bool:
    if re.search(r">[ \t]*[^ \t]+", cmd):
        return True
    if re.search(r"(tee|sed[ \t]+-i|perl[ \t]+-i|truncate|touch|mv|cp|rm|install|git[ \t]+apply|patch([ \t]|$)|apply_patch)[ \t]", cmd):
        return True
    if re.search(r"(^|[ \t])(gofmt|goimports)[ \t]+-w([ \t]|$)", cmd):
        return True
    return False


def bash_command_is_opaque_codegen(cmd: str) -> bool:
    normalized = normalize_path(cmd)
    lowered = normalized.lower()

    if bash_command_is_workflow_maintenance_command(normalized):
        return False

    if re.search(r"(^|[ \t])(codemod|rewrite|refactor|scaffold|generate|migrate|autofix|fixup)([ \t]|$)", lowered):
        return True

    if re.search(r"(^|[ \t])(git[ \t]+apply|patch([ \t]|$)|apply_patch([ \t]|$))", normalized):
        return True

    if re.search(r"(^|[ \t])(python([0-9.]*)?|node|ruby|perl|bash|sh)([ \t]|$)", normalized):
        if bash_command_is_test_command(normalized) or bash_command_is_quality_check_command(normalized):
            return False

        if re.search(r"(^|[ \t])(python([0-9.]*)?|node|ruby|perl|bash|sh)[ \t]+(-v|--version|-h|--help)([ \t]|$)", lowered):
            return False
        if re.search(r"(^|[ \t])python([0-9.]*)?[ \t]+-c([ \t]|$)", lowered):
            return False
        if re.search(r"(^|[ \t])node[ \t]+(-e|-p)([ \t]|$)", lowered):
            return False
        if re.search(r"(^|[ \t])(bash|sh)[ \t]+-c([ \t]|$)", lowered):
            return False
        if re.search(r"(^|[ \t])(python([0-9.]*)?|node|ruby|perl|bash|sh)[ \t]+[^ \t]*(read[_-]?only|readonly)[^ \t]*([ \t]|$)", lowered):
            return False
        if re.search(r"(read[_-]?only|--dry-run|--check)", lowered):
            return False
        return True

    return False


def bash_command_mutates_repo(cmd: str) -> bool:
    return bash_command_has_write_intent(cmd) or bash_command_is_opaque_codegen(cmd)


@dataclass
class CorePaths:
    project_dir: Path
    workflow_dir: Path
    hooks_dir: Path
    config_dir: Path
    output_dir: Path
    spec_dir: Path
    state_dir: Path
    log_dir: Path
    runtime_file: Path
    current_task_file: Path
    event_log: Path
    links_file: Path
    discovery_file: Path
    spec_file: Path
    tasks_file: Path
    linkage_spec_file: Path
    architecture_rules_file: Path


def build_paths(project_dir: Path) -> CorePaths:
    workflow_dir = project_dir / ".claude" / "workflow"
    hooks_dir = project_dir / ".claude" / "hooks"
    config_dir = workflow_dir / "config"
    output_dir = project_dir / ".rpi-outfile"
    spec_dir = output_dir / "specs"
    state_dir = output_dir / "state"
    log_dir = output_dir / "logs"
    return CorePaths(
        project_dir=project_dir,
        workflow_dir=workflow_dir,
        hooks_dir=hooks_dir,
        config_dir=config_dir,
        output_dir=output_dir,
        spec_dir=spec_dir,
        state_dir=state_dir,
        log_dir=log_dir,
        runtime_file=config_dir / "runtime.json",
        current_task_file=state_dir / "current_task.json",
        event_log=log_dir / "events.jsonl",
        links_file=state_dir / "spec" / "links.json",
        discovery_file=spec_dir / "l0" / "discovery.md",
        spec_file=spec_dir / "l0" / "spec.md",
        tasks_file=spec_dir / "l0" / "tasks.md",
        linkage_spec_file=spec_dir / "l0" / "module-linkage.md",
        architecture_rules_file=config_dir / "architecture.rules.json",
    )


class PreToolUseCore:
    def __init__(self, paths: CorePaths, payload: Dict[str, Any]):
        self.paths = paths
        self.payload = payload
        self.tool_name = str_value(payload.get("tool_name", ""))
        self.runtime = load_json_file(paths.runtime_file)
        self.current_task = load_json_file(paths.current_task_file)
        self.harness_enabled = self.runtime_bool("harness_enabled", True)

    def runtime_raw(self, key: str, default: Any) -> Any:
        value = self.runtime.get(key, default)
        return default if value is None else value

    def runtime_str(self, key: str, default: str) -> str:
        return str_value(self.runtime_raw(key, default), default)

    def runtime_bool(self, key: str, default: bool) -> bool:
        return bool_value(self.runtime_raw(key, default), default)

    def runtime_int(self, key: str, default: int) -> int:
        return int_value(self.runtime_raw(key, default), default)

    def append_event(self, event: Dict[str, Any]) -> None:
        if not self.paths.log_dir.exists():
            return
        append_jsonl_line(self.paths.event_log, json.dumps(event, ensure_ascii=False))

    def event_warn(self, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {"ts": utc_now(), "event": "pre_tool_warn", "tool": self.tool_name, "reason": reason}
        if extra:
            payload.update(extra)
        self.append_event(payload)

    def event_block(self, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {"ts": utc_now(), "event": "pre_tool_block", "tool": self.tool_name, "reason": reason}
        if extra:
            payload.update(extra)
        self.append_event(payload)

    def task_context(self) -> Dict[str, Any]:
        task_id = str_value(self.current_task.get("task_id", ""))
        status = str_value(self.current_task.get("status", "idle"), "idle")
        tdd = self.current_task.get("tdd", {})
        if not isinstance(tdd, dict):
            tdd = {}
        red_written = bool_value(tdd.get("red_test_written", False), False)

        spec_refs = self.current_task.get("spec_refs")
        raw = "0"
        count = 0
        non_numeric = False

        if isinstance(spec_refs, list):
            count = len(spec_refs)
            raw = str(count)
        elif isinstance(spec_refs, dict):
            count = len(spec_refs)
            raw = str(count)
        elif isinstance(spec_refs, int):
            count = max(spec_refs, 0)
            raw = str(spec_refs)
        elif isinstance(spec_refs, float):
            raw = str(spec_refs)
            if spec_refs.is_integer() and spec_refs >= 0:
                count = int(spec_refs)
            else:
                non_numeric = True
                count = 0
        elif isinstance(spec_refs, str):
            raw = spec_refs
            if re.fullmatch(r"[0-9]+", spec_refs.strip()):
                count = int(spec_refs.strip())
            else:
                non_numeric = True
                count = 0
        elif spec_refs is None:
            raw = "0"
            count = 0
        else:
            raw = str(spec_refs)
            non_numeric = True
            count = 0

        return {
            "task_id": task_id,
            "status": status,
            "spec_count_raw": raw,
            "spec_count": count,
            "spec_count_non_numeric": non_numeric,
            "red_written": red_written,
        }

    @staticmethod
    def has_active_task(ctx: Dict[str, Any]) -> bool:
        return bool(ctx.get("task_id")) and str_value(ctx.get("status", "idle")) == "in_progress"

    def maybe_warn_spec_count_non_numeric(self, ctx: Dict[str, Any], path: str = "", command: str = "") -> None:
        if not bool_value(ctx.get("spec_count_non_numeric", False), False):
            return
        event: Dict[str, Any] = {
            "raw_spec_count": str_value(ctx.get("spec_count_raw", "")),
        }
        if path:
            event["path"] = path
        if command:
            event["command"] = command
        self.event_warn("spec_refs length is non-numeric, fallback to 0", event)

    def emit_risk_decision_if_needed(
        self,
        risk_tool: str,
        risk_value: str,
        context_field: str,
        context_value: str,
        reason_prefix: str,
    ) -> Optional[Tuple[str, str]]:
        if not self.harness_enabled:
            return None

        risk_enabled = self.runtime_bool("risk_matrix_enabled", True)
        strict_mode_val = self.runtime_bool("strict_mode", True)

        try:
            risk_json = guardrails.assess_risk(
                project_dir=self.paths.project_dir,
                tool=risk_tool,
                value=risk_value,
            )
        except Exception as exc:
            reason = f"Risk policy engine error: {exc}"
            self.event_warn(reason, {"context": {"field": context_field, "value": context_value}})
            if risk_enabled and strict_mode_val:
                return "deny", "Blocked: risk matrix is enabled in strict mode, but risk assessor engine failed."
            return None

        decision = str_value(risk_json.get("decision", "allow"), "allow")
        if decision == "allow":
            return None

        level = str_value(risk_json.get("level", "R0"), "R0")
        reason = str_value(risk_json.get("reason", "Risk policy requires manual review"), "Risk policy requires manual review")
        rule_id = str_value(risk_json.get("rule_id", ""))
        reason = f"{reason_prefix} {reason}"
        payload_extra = {
            "risk_level": level,
            "risk_rule": rule_id,
            "context": {"field": context_field, "value": context_value},
        }
        if decision == "deny":
            self.event_block(reason, payload_extra)
        else:
            self.event_warn(reason, payload_extra)
        return decision, reason

    def enforce_spec_link_if_needed(
        self,
        task_id: str,
        context_field: str,
        context_value: str,
    ) -> Optional[Tuple[str, str]]:
        if not self.harness_enabled:
            return None
        if not self.runtime_bool("spec_link_enforce", False):
            return None

        # Avoid rebuilding links on every hot-path invocation when specs/task state are unchanged.
        links_ready = False
        links_mtime = file_mtime_ns(self.paths.links_file)
        source_mtime = max(
            file_mtime_ns(self.paths.discovery_file),
            file_mtime_ns(self.paths.spec_file),
            file_mtime_ns(self.paths.tasks_file),
            file_mtime_ns(self.paths.current_task_file),
        )
        if self.paths.links_file.exists() and links_mtime >= source_mtime:
            links_ready = True

        if not links_ready:
            try:
                guardrails.build_spec_links(self.paths.project_dir, quiet=True)
            except Exception as exc:
                self.event_warn(
                    f"spec link build failed: {exc}",
                    {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
                )

        spec_refs_raw = self.current_task.get("spec_refs", [])
        if isinstance(spec_refs_raw, list):
            spec_refs = sorted([str(x).strip() for x in spec_refs_raw if str(x).strip()])
        else:
            spec_refs = []
        links_sig = hash_text(
            "\n".join(
                [
                    f"task={task_id}",
                    f"links={self.signature_line_for_file(self.paths.links_file)}",
                    f"spec_refs={','.join(spec_refs)}",
                ]
            )
            + "\n"
        )
        cached_guardrails = self.current_task.get("guardrails", {})
        if not isinstance(cached_guardrails, dict):
            cached_guardrails = {}
        cached_spec_link = cached_guardrails.get("spec_link", {})
        if not isinstance(cached_spec_link, dict):
            cached_spec_link = {}
        cached_sig = str_value(cached_spec_link.get("signature", ""))
        cached_status = str_value(cached_spec_link.get("status", ""))
        cached_note = str_value(cached_spec_link.get("note", ""))
        if cached_sig and cached_sig == links_sig:
            if cached_status == "pass":
                return None
            if cached_status == "fail":
                reason = (
                    f"Blocked: spec link check failed ({cached_note or 'missing spec link binding'}). "
                    "Run /rpi-spec link after fixing spec_refs."
                )
                self.event_block(
                    reason,
                    {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
                )
                return "deny", reason

        if not self.paths.links_file.exists():
            reason = "Blocked: spec link graph missing. Run /rpi-spec link to build traceability graph before coding."
            self.event_block(
                reason,
                {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
            )
            self.task_set_guardrail(
                "spec_link",
                {
                    "status": "fail",
                    "signature": links_sig,
                    "verified_at": utc_now(),
                    "note": "spec link graph missing",
                    "bind_count": 0,
                },
            )
            return "deny", reason

        links_json = load_json_file(self.paths.links_file)
        edges = links_json.get("edges", [])
        bind_count = 0
        if isinstance(edges, list):
            sid = f"session:{task_id}"
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                if str_value(edge.get("from", "")) == sid and str_value(edge.get("relation", "")) == "binds_spec_ref":
                    bind_count += 1

        if bind_count < 1:
            reason = "Blocked: task has no bound spec link edges. Add spec_refs and run /rpi-spec link before coding."
            self.event_block(
                reason,
                {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
            )
            self.task_set_guardrail(
                "spec_link",
                {
                    "status": "fail",
                    "signature": links_sig,
                    "verified_at": utc_now(),
                    "note": "task has no bound spec link edges",
                    "bind_count": bind_count,
                },
            )
            return "deny", reason
        self.task_set_guardrail(
            "spec_link",
            {
                "status": "pass",
                "signature": links_sig,
                "verified_at": utc_now(),
                "note": f"binds_spec_ref edges={bind_count}",
                "bind_count": bind_count,
            },
        )
        return None

    def enforce_linkage_spec_if_needed(
        self,
        task_id: str,
        context_field: str,
        context_value: str,
    ) -> Optional[Tuple[str, str]]:
        if not self.runtime_bool("require_linkage_spec", False):
            return None

        if not self.paths.linkage_spec_file.exists():
            reason = (
                "Blocked: runtime requires linkage spec but .rpi-outfile/specs/l0/module-linkage.md is missing. "
                "Run /rpi-check skeleton-init (or complete module-linkage.md) before coding."
            )
            self.event_block(
                reason,
                {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
            )
            return "deny", reason

        text = self.paths.linkage_spec_file.read_text(encoding="utf-8", errors="ignore")
        required_groups = [
            ["模块联动关系", "Module Linkage", "Module Interaction"],
            ["数据流向", "Data Flow"],
            ["技术实现标准", "Technical Standards", "Implementation Standards"],
        ]
        missing_sections: List[str] = []
        for group in required_groups:
            matched = any(alias in text for alias in group)
            if not matched:
                missing_sections.append(group[0])

        if missing_sections:
            reason = (
                "Blocked: linkage spec is incomplete (missing: "
                + " ".join(missing_sections)
                + "). Complete module-linkage.md, then rerun /rpi-check linkage."
            )
            self.event_block(
                reason,
                {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
            )
            return "deny", reason
        return None

    def enforce_linkage_integrity_if_needed(
        self,
        task_id: str,
        context_field: str,
        context_value: str,
    ) -> Optional[Tuple[str, str]]:
        if not self.runtime_bool("linkage_strict_mode", False):
            return None

        try:
            result = guardrails.check_linkage(self.paths.project_dir, quiet=True)
        except Exception as exc:
            reason = f"Blocked: linkage_strict_mode check engine failed ({exc})."
            self.event_block(reason, {"task_id": task_id, "context": {"field": context_field, "value": context_value}})
            return "deny", reason

        if str_value(result.get("status", "fail")) != "pass":
            reason = "Blocked: linkage_strict_mode check failed. Fix module linkage issues first, then run /rpi-check linkage."
            self.event_block(
                reason,
                {"task_id": task_id, "context": {"field": context_field, "value": context_value}},
            )
            return "deny", reason
        return None

    def count_post_tool_events_since(self, since_iso: str) -> int:
        if not self.paths.event_log.exists():
            return 0
        count = 0
        try:
            with self.paths.event_log.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    ts = str_value(obj.get("ts", ""))
                    if ts >= since_iso and str_value(obj.get("event", "")) == "post_tool_use":
                        count += 1
        except OSError:
            return 0
        return count

    def task_tool_event_count(self) -> Optional[int]:
        autonomy = self.current_task.get("autonomy")
        if not isinstance(autonomy, dict):
            return None
        raw = autonomy.get("tool_event_count")
        count = int_value(raw, -1)
        if count < 0:
            return None
        return count

    def enforce_autonomy_budget_if_needed(
        self,
        task_id: str,
        context_field: str,
        context_value: str,
    ) -> Optional[Tuple[str, str]]:
        if not self.harness_enabled:
            return None

        budget_mode = self.runtime_str("autonomy_budget_mode", "off")
        if budget_mode == "off":
            return None

        created_at = str_value(self.current_task.get("created_at", ""))
        if not created_at:
            return None

        max_minutes = self.runtime_int("autonomy_max_minutes", 0)
        max_events = self.runtime_int("autonomy_max_tool_events", 0)
        reason = ""

        created_epoch = iso_to_epoch(created_at)
        now_epoch = int(time.time())
        if created_epoch > 0 and max_minutes > 0:
            elapsed_minutes = (now_epoch - created_epoch) // 60
            if elapsed_minutes >= max_minutes:
                reason = f"Autonomy budget exceeded: task active {elapsed_minutes} minutes (limit={max_minutes}m)."

        if not reason and max_events > 0:
            event_count = self.task_tool_event_count()
            if event_count is None:
                event_count = self.count_post_tool_events_since(created_at)
            if event_count >= max_events:
                reason = f"Autonomy budget exceeded: tool events={event_count} (limit={max_events})."

        if not reason:
            return None

        reason = f"{reason} Close or split task {task_id} before continuing."
        extra = {"task_id": task_id, "context": {"field": context_field, "value": context_value}}
        if budget_mode == "enforce":
            self.event_block(reason, extra)
            return "deny", reason

        self.event_warn(reason, extra)
        return "ask", reason

    def signature_line_for_file(self, path: Path) -> str:
        if path.exists():
            return f"{path}:{file_mtime(path)}"
        return f"{path}:MISSING"

    def spec_guardrail_signature_hash(self) -> str:
        scan_exclude_dirs_raw = self.runtime_raw("architecture_scan_exclude_dirs", [])
        if isinstance(scan_exclude_dirs_raw, list):
            scan_exclude_dirs = ",".join(sorted([str(x).strip() for x in scan_exclude_dirs_raw if str(x).strip()]))
        elif isinstance(scan_exclude_dirs_raw, str):
            scan_exclude_dirs = scan_exclude_dirs_raw.strip()
        else:
            scan_exclude_dirs = ""
        lines = [
            f"precode_guard_mode={self.runtime_str('precode_guard_mode', 'enforce')}",
            f"architecture_enforce={str(self.runtime_bool('architecture_enforce', False)).lower()}",
            f"architecture_require_rules={str(self.runtime_bool('architecture_require_rules', False)).lower()}",
            f"architecture_scan_max_files={self.runtime_int('architecture_scan_max_files', 2000)}",
            f"architecture_scan_exclude_dirs={scan_exclude_dirs}",
            f"spec_link_enforce={str(self.runtime_bool('spec_link_enforce', False)).lower()}",
            f"require_linkage_spec={str(self.runtime_bool('require_linkage_spec', False)).lower()}",
            f"linkage_strict_mode={str(self.runtime_bool('linkage_strict_mode', False)).lower()}",
            f"ddd_lite_mode={self.runtime_str('ddd_lite_mode', 'warn')}",
            f"ddd_min_glossary_terms={self.runtime_int('ddd_min_glossary_terms', 6)}",
            f"ddd_min_bounded_contexts={self.runtime_int('ddd_min_bounded_contexts', 2)}",
            f"ddd_min_invariants={self.runtime_int('ddd_min_invariants', 3)}",
            f"mvp_priority_override_mode={self.runtime_str('mvp_priority_override_mode', 'warn')}",
            f"mvp_weighted_coverage_tolerance={self.runtime_int('mvp_weighted_coverage_tolerance', 10)}",
            f"mvp_max_promote_non_core={self.runtime_int('mvp_max_promote_non_core', 1)}",
            self.signature_line_for_file(self.paths.discovery_file),
            self.signature_line_for_file(self.paths.spec_file),
            self.signature_line_for_file(self.paths.tasks_file),
            self.signature_line_for_file(self.paths.linkage_spec_file),
            self.signature_line_for_file(self.paths.architecture_rules_file),
        ]
        return hash_text("\n".join(lines) + "\n")

    def task_set_guardrail(self, guardrail_name: str, payload: Dict[str, Any]) -> None:
        if not self.paths.current_task_file.exists():
            return
        if not isinstance(self.current_task, dict) or not self.current_task:
            return
        if not guardrail_name:
            return
        if not isinstance(payload, dict):
            return

        guardrails = self.current_task.get("guardrails")
        if not isinstance(guardrails, dict):
            guardrails = {}
            self.current_task["guardrails"] = guardrails

        guardrails[guardrail_name] = payload
        self.current_task["last_updated_at"] = utc_now()
        write_json_atomic(self.paths.current_task_file, self.current_task)

    def task_set_precode_guardrail(self, status: str, signature: str, note: str) -> None:
        self.task_set_guardrail(
            "precode",
            {
                "status": status,
                "signature": signature,
                "verified_at": utc_now(),
                "note": note,
            },
        )

    def enforce_precode_guardrails_if_needed(
        self,
        task_id: str,
        context_field: str,
        context_value: str,
    ) -> Optional[Tuple[str, str]]:
        mode = self.runtime_str("precode_guard_mode", "enforce")
        if mode == "off":
            return None

        signature = self.spec_guardrail_signature_hash()
        guardrail_state = self.current_task.get("guardrails", {})
        if not isinstance(guardrail_state, dict):
            guardrail_state = {}
        precode = guardrail_state.get("precode", {})
        if not isinstance(precode, dict):
            precode = {}

        cached_status = str_value(precode.get("status", ""))
        cached_signature = str_value(precode.get("signature", ""))
        cached_note = str_value(precode.get("note", ""))

        if cached_signature and cached_signature == signature:
            if cached_status == "pass":
                return None
            if cached_status == "fail":
                reason = (
                    f"Precode guardrails failed: {cached_note or 'spec checks failed'}. "
                    "Run /rpi-task start to refresh snapshot after fixing specs."
                )
                extra = {"task_id": task_id, "context": {"field": context_field, "value": context_value}}
                if mode == "enforce":
                    self.event_block(reason, extra)
                    return "deny", reason
                self.event_warn(reason, extra)
                return None

        architecture_enforce = self.runtime_bool("architecture_enforce", False)
        architecture_require_rules = self.runtime_bool("architecture_require_rules", False)
        try:
            bundle = guardrails.check_precode_bundle(
                project_dir=self.paths.project_dir,
                include_architecture=self.harness_enabled and architecture_enforce,
                architecture_require_rules=architecture_require_rules,
            )
            failures_raw = bundle.get("failures", [])
            failures = [str(x).strip() for x in failures_raw] if isinstance(failures_raw, list) else []
            failures = [x for x in failures if x]
        except Exception as exc:
            failures = [f"precode bundle engine error ({exc})"]

        if failures:
            reason = "; ".join(failures)
            self.task_set_precode_guardrail("fail", signature, reason)
            final_reason = f"Blocked: precode guardrails failed ({reason}). Fix specs/checks, then retry /rpi-task start."
            extra = {"task_id": task_id, "context": {"field": context_field, "value": context_value}}
            if mode == "enforce":
                self.event_block(final_reason, extra)
                return "deny", final_reason
            self.event_warn(final_reason, extra)
            return None

        self.task_set_precode_guardrail("pass", signature, "precode guard checks passed")
        return None

    def enforce_tdd_for_path_if_needed(self, task_id: str, path: str, red_written: bool) -> Optional[Tuple[str, str]]:
        if not is_code_path(path) or is_test_path(path):
            return None

        mode = self.runtime_str("tdd_mode", "strict").lower()
        if mode == "off":
            return None

        exempt_regex = self.runtime_str("tdd_exempt_path_regex", "")
        if regex_matches(exempt_regex, path):
            return None

        if red_written:
            return None

        reason = f"TDD evidence missing for production code path ({path}). Write and run a failing test first."
        if mode in {"recommended", "warn"}:
            self.event_warn(reason, {"task_id": task_id, "path": path})
            return None

        self.event_block(reason, {"task_id": task_id, "path": path})
        return (
            "deny",
            "Blocked by TDD guard: no Red evidence yet. Write and run a failing test first, then edit production code.",
        )

    def enforce_tdd_for_bash_if_needed(self, task_id: str, cmd: str, red_written: bool) -> Optional[Tuple[str, str]]:
        if red_written:
            return None
        if bash_command_is_test_command(cmd) or bash_command_targets_tests(cmd):
            return None

        mode = self.runtime_str("tdd_mode", "strict").lower()
        if mode == "off":
            return None

        exempt_regex = self.runtime_str("tdd_exempt_command_regex", "")
        if regex_matches(exempt_regex, cmd):
            return None

        reason = "TDD evidence missing before mutating code via Bash command."
        if mode in {"recommended", "warn"}:
            self.event_warn(reason, {"task_id": task_id, "command": cmd})
            return None

        self.event_block(reason, {"task_id": task_id, "command": cmd})
        return (
            "deny",
            "Blocked by TDD guard: no Red evidence yet. Write and run a failing test first, then mutate production code.",
        )

    def enforce_ux_if_needed(self, target_path: str) -> Optional[Tuple[str, str]]:
        try:
            result = guardrails.ux_precheck(
                project_dir=self.paths.project_dir,
                target_path=target_path,
                tool_name=self.tool_name,
            )
        except Exception as exc:
            self.event_warn(f"ux precheck engine error: {exc}", {"path": target_path})
            return None

        warnings = result.get("warnings", [])
        if isinstance(warnings, list):
            for item in warnings:
                self.event_warn(str(item), {"path": target_path})

        if str_value(result.get("status", "pass")) != "deny":
            return None
        reason = str_value(result.get("reason", ""), "")
        if reason:
            return "deny", reason
        return "deny", f"UX compliance check failed for {target_path}. Run /rpi-check ux for details."

    def handle_edit(self) -> Optional[Tuple[str, str]]:
        target_path = normalize_path(str_value(((self.payload.get("tool_input") or {}).get("file_path", ""))))

        if is_framework_internal_path(target_path):
            return None

        risk_decision = self.emit_risk_decision_if_needed("Edit", target_path, "path", target_path, "Risk policy:")
        if risk_decision is not None:
            return risk_decision

        planning_path = is_planning_path(target_path)
        task_ctx = self.task_context()
        task_id = str_value(task_ctx.get("task_id", ""))
        status = str_value(task_ctx.get("status", "idle"), "idle")
        spec_count = int_value(task_ctx.get("spec_count", 0), 0)
        red_written = bool_value(task_ctx.get("red_written", False), False)

        if not self.has_active_task(task_ctx):
            if planning_path:
                return None
            reason = "Blocked: no active RPI task — code changes must trace to a task and spec. Run /rpi-task start first (example: /rpi-task start 001)."
            self.event_block(reason)
            return "deny", reason

        self.maybe_warn_spec_count_non_numeric(task_ctx, path=target_path)

        decision = self.enforce_autonomy_budget_if_needed(task_id, "path", target_path)
        if decision is not None:
            return decision

        if is_code_path(target_path):
            for check in (
                self.enforce_spec_link_if_needed,
                self.enforce_linkage_spec_if_needed,
                self.enforce_linkage_integrity_if_needed,
                self.enforce_precode_guardrails_if_needed,
            ):
                decision = check(task_id, "path", target_path)
                if decision is not None:
                    return decision

        if is_code_path(target_path) and spec_count == 0:
            reason = (
                "Blocked: spec_refs is empty — code must trace to a spec for auditability. "
                "Add a spec path via /rpi-task start <task-id> --spec <path>, or update current_task.json manually."
            )
            self.event_block(reason, {"path": target_path})
            return "deny", reason

        decision = self.enforce_tdd_for_path_if_needed(task_id, target_path, red_written)
        if decision is not None:
            return decision

        decision = self.enforce_ux_if_needed(target_path)
        if decision is not None:
            return decision
        return None

    def handle_bash(self) -> Optional[Tuple[str, str]]:
        cmd = str_value(((self.payload.get("tool_input") or {}).get("command", "")))

        if re.search(r"(^|[ \t])cd[ \t]+/d([ \t]|$)", cmd):
            reason = (
                "Blocked: 'cd /d' is Windows CMD syntax, not valid in POSIX shell. "
                "Use a full POSIX path (e.g. /d/Project/...) or run scripts from the project root."
            )
            self.event_block(reason, {"command": cmd})
            return "deny", reason

        risk_decision = self.emit_risk_decision_if_needed("Bash", cmd, "command", cmd, "Risk policy:")
        if risk_decision is not None:
            return risk_decision

        if bash_command_is_workflow_maintenance_command(cmd):
            return None

        requires_task_context = bash_command_mutates_repo(cmd) and (bash_command_targets_code(cmd) or bash_command_is_opaque_codegen(cmd))
        if not requires_task_context:
            return None

        task_ctx = self.task_context()
        task_id = str_value(task_ctx.get("task_id", ""))
        spec_count = int_value(task_ctx.get("spec_count", 0), 0)
        red_written = bool_value(task_ctx.get("red_written", False), False)

        if not self.has_active_task(task_ctx):
            reason = (
                "Blocked: no active RPI task — this Bash command mutates code and needs task context for traceability. "
                "Run /rpi-task start first (example: /rpi-task start 001)."
            )
            self.event_block(reason, {"command": cmd})
            return "deny", reason

        self.maybe_warn_spec_count_non_numeric(task_ctx, command=cmd)

        decision = self.enforce_autonomy_budget_if_needed(task_id, "command", cmd)
        if decision is not None:
            return decision

        for check in (
            self.enforce_spec_link_if_needed,
            self.enforce_linkage_spec_if_needed,
            self.enforce_linkage_integrity_if_needed,
            self.enforce_precode_guardrails_if_needed,
        ):
            decision = check(task_id, "command", cmd)
            if decision is not None:
                return decision

        if spec_count == 0:
            reason = (
                "Blocked: spec_refs is empty — code must trace to a spec for auditability. "
                "Add a spec path via /rpi-task start <task-id> --spec <path>, or update current_task.json manually."
            )
            self.event_block(reason, {"command": cmd})
            return "deny", reason

        decision = self.enforce_tdd_for_bash_if_needed(task_id, cmd, red_written)
        if decision is not None:
            return decision

        return None

    def run(self) -> int:
        if self.tool_name in {"Edit", "Write", "MultiEdit"}:
            decision = self.handle_edit()
        elif self.tool_name == "Bash":
            decision = self.handle_bash()
        else:
            decision = None

        if decision is not None:
            emit_pretool_decision(decision[0], decision[1])
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PreToolUse full decision core")
    parser.add_argument("--project-dir", default="", help="Project root path")
    args = parser.parse_args()

    script_file = Path(__file__).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else resolve_project_dir(script_file.parent)
    paths = build_paths(project_dir)
    payload = read_hook_input()

    try:
        return PreToolUseCore(paths, payload).run()
    except Exception as exc:  # pragma: no cover
        emit_pretool_decision("deny", f"PreToolUse core internal error: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
