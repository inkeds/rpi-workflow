#!/usr/bin/env python3
"""Automation engine for workflow scripts migrated from Bash.

Subcommands:
- harness [show|on|off]
- run-evals [--suite all|capability|regression] [--quiet]
- suggest-gates [--profile minimal|standard|strict] [--write] [--explain]
- anti-entropy [--auto-fix] [--strict] [--json]
- build-audit-pack [--task <id>] [--output <dir>] [--tar] [--limit-events <n>]
- audit-report [--task <id>] [--days <n>] [--output <dir>] [--json]
- auto-rpi [--phase M0|M1|M2] [--max-rounds N] [--max-minutes M] [--max-failures N] [--max-tool-events N] [--auto-fix|--no-auto-fix] [--force]
- a2a-review [--base <ref>] [--head <ref>] [--auto-merge] [--quiet] [--json]
- agent-memory-update [--task <id>] [--result <pass|fail>] [--root-cause <value>] [--note <text>] [--archive <file>] [--force] [--quiet]
- abort-task "<reason>"
- pause-task "<reason>"
- resume-task [task_id]
- deepen-mvp [idea] [platform]
- spec-expand ["<confirmation text>"]  # empty args => auto-confirm from discovery/init_summary
- recover [list|restore ...]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import locale
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import file_lock
import guardrails_tool as guardrails
import artifact_recovery
import spec_state_tool
import task_flow_tool as task_flow


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


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_json_obj(path: Path) -> Dict[str, Any]:
    data = read_json(path, {})
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
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value.strip())
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


def parse_iso(ts: str) -> Optional[datetime]:
    text = str(ts or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def within_window(ts: str, cutoff: datetime) -> bool:
    dt = parse_iso(ts)
    return bool(dt and dt >= cutoff)


def parse_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def safe_print(text: str, stream: Any = sys.stdout) -> None:
    print(text, file=stream)


def shell_command(command: str, cwd: Path, quiet: bool = True) -> Tuple[int, str, str]:
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
        task_flow.resolve_shell_argv(command),
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        text=False if quiet else True,
        encoding=None if quiet else "utf-8",
    )
    if quiet:
        return int(proc.returncode), decode_output(proc.stdout), decode_output(proc.stderr)
    return int(proc.returncode), "", ""


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _ in handle)


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize_phase(raw: str, default: str = "M0") -> str:
    value = (raw or "").upper().strip().replace(" ", "")
    return value if value in {"M0", "M1", "M2"} else default


@dataclass
class Paths:
    base: task_flow.Paths
    spec_l0_dir: Path
    state_spec_dir: Path
    state_recovery_dir: Path
    state_recovery_index_file: Path
    state_agent_review_dir: Path
    state_agent_memory_dir: Path
    audit_root_dir: Path
    tasks_archive_dir: Path
    agents_file: Path


def build_paths(project_dir: Path) -> Paths:
    base = task_flow.build_paths(project_dir)
    return Paths(
        base=base,
        spec_l0_dir=base.spec_dir / "l0",
        state_spec_dir=base.state_dir / "spec",
        state_recovery_dir=base.state_dir / "recovery",
        state_recovery_index_file=base.state_dir / "recovery" / "index.jsonl",
        state_agent_review_dir=base.state_dir / "agent-review",
        state_agent_memory_dir=base.state_dir / "agent-memory",
        audit_root_dir=base.output_dir / "audit",
        tasks_archive_dir=base.log_dir / "tasks",
        agents_file=project_dir / "AGENTS.md",
    )


def ensure_layout(paths: Paths) -> None:
    task_flow.ensure_layout(paths.base)
    paths.spec_l0_dir.mkdir(parents=True, exist_ok=True)
    paths.state_spec_dir.mkdir(parents=True, exist_ok=True)
    paths.state_recovery_dir.mkdir(parents=True, exist_ok=True)
    paths.state_agent_review_dir.mkdir(parents=True, exist_ok=True)
    paths.state_agent_memory_dir.mkdir(parents=True, exist_ok=True)
    paths.audit_root_dir.mkdir(parents=True, exist_ok=True)
    paths.tasks_archive_dir.mkdir(parents=True, exist_ok=True)
    artifact_recovery.ensure_layout(paths.base.project_dir)
    if not paths.base.event_log.exists():
        paths.base.event_log.touch()
    if not paths.base.gate_log.exists():
        paths.base.gate_log.touch()


def load_runtime(paths: Paths) -> Dict[str, Any]:
    runtime = read_json_obj(paths.base.runtime_file)
    if runtime:
        return runtime
    return task_flow.default_runtime()


def append_event(paths: Paths, event: Dict[str, Any]) -> None:
    row = dict(event)
    if not str_value(row.get("ts", "")).strip():
        row["ts"] = utc_now()
    append_jsonl(paths.base.event_log, row)


def snapshot_before_mutation(paths: Paths, reason: str, targets: Sequence[Path], actor: str = "automation") -> List[Dict[str, Any]]:
    rows = artifact_recovery.snapshot_files(
        project_dir=paths.base.project_dir,
        targets=targets,
        reason=reason,
        actor=actor,
    )
    if rows:
        append_event(
            paths,
            {
                "event": "artifact_snapshot",
                "reason": reason,
                "count": len(rows),
                "targets": [str(x.get("target", "")) for x in rows[:10]],
            },
        )
    return rows


def cmd_harness(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    cmd = argv[0] if argv else "show"

    usage = "Usage: bash .claude/workflow/rpi.sh mode harness [show|on|off]"
    if cmd in {"-h", "--help", "help"}:
        safe_print(usage)
        safe_print("")
        safe_print("show  Print current harness state and key switches.")
        safe_print("on    Enable full Harness enhancement bundle.")
        safe_print("off   Disable Harness enhancement bundle (manual-first mode).")
        return 0

    runtime = load_runtime(paths)

    def show_state() -> None:
        out = {
            "harness_enabled": bool_value(runtime.get("harness_enabled", True), True),
            "strict_mode": bool_value(runtime.get("strict_mode", True), True),
            "start_require_ready": bool_value(runtime.get("start_require_ready", True), True),
            "close_require_spec_sync": bool_value(runtime.get("close_require_spec_sync", True), True),
            "risk_matrix_enabled": bool_value(runtime.get("risk_matrix_enabled", True), True),
            "architecture_enforce": bool_value(runtime.get("architecture_enforce", True), True),
            "spec_state_required": bool_value(runtime.get("spec_state_required", True), True),
            "spec_link_enforce": bool_value(runtime.get("spec_link_enforce", False), False),
            "ddd_lite_mode": str_value(runtime.get("ddd_lite_mode", "warn"), "warn"),
            "mvp_priority_override_mode": str_value(runtime.get("mvp_priority_override_mode", "warn"), "warn"),
            "precode_guard_mode": str_value(runtime.get("precode_guard_mode", "enforce"), "enforce"),
            "tdd_mode": str_value(runtime.get("tdd_mode", "strict"), "strict"),
            "gates_auto_retry_enabled": bool_value(runtime.get("gates_auto_retry_enabled", False), False),
            "gates_auto_retry_max": int_value(runtime.get("gates_auto_retry_max", 0), 0),
            "gates_auto_fix_on_fail": bool_value(runtime.get("gates_auto_fix_on_fail", False), False),
            "agent_memory_auto_update": bool_value(runtime.get("agent_memory_auto_update", False), False),
            "agent_review_enabled": bool_value(runtime.get("agent_review_enabled", False), False),
            "a2a_auto_merge_non_core": bool_value(runtime.get("a2a_auto_merge_non_core", False), False),
            "a2a_allow_commit": bool_value(runtime.get("a2a_allow_commit", False), False),
            "audit_report_enabled": bool_value(runtime.get("audit_report_enabled", False), False),
            "audit_pack_required_on_close": bool_value(runtime.get("audit_pack_required_on_close", True), True),
            "stop_loop_max_blocks": int_value(runtime.get("stop_loop_max_blocks", 10), 10),
        }
        safe_print(json.dumps(out, ensure_ascii=False, indent=2))

    def apply_overlay(overlay: Dict[str, Any]) -> None:
        merged = deep_merge(runtime, overlay)
        write_json_atomic(paths.base.runtime_file, merged)
        runtime.clear()
        runtime.update(merged)

    if cmd in {"show", "status"}:
        show_state()
        return 0
    if cmd == "on":
        apply_overlay(
            {
                "harness_enabled": True,
                "strict_mode": True,
                "start_require_ready": True,
                "close_require_spec_sync": True,
                "risk_matrix_enabled": True,
                "autonomy_budget_mode": "enforce",
                "architecture_enforce": True,
                "spec_state_required": True,
                "spec_link_enforce": True,
                "ddd_lite_mode": "enforce",
                "ddd_min_glossary_terms": 8,
                "ddd_min_bounded_contexts": 2,
                "ddd_min_invariants": 4,
                "mvp_priority_override_mode": "enforce",
                "mvp_weighted_coverage_tolerance": 5,
                "mvp_max_promote_non_core": 1,
                "precode_guard_mode": "enforce",
                "tdd_mode": "strict",
                "gates_auto_retry_enabled": True,
                "gates_auto_retry_max": 3,
                "gates_auto_fix_on_fail": True,
                "agent_memory_auto_update": True,
                "agent_review_enabled": True,
                "a2a_auto_merge_non_core": True,
                "a2a_allow_commit": False,
                "audit_report_enabled": True,
                "audit_pack_required_on_close": True,
            }
        )
        append_event(paths, {"event": "harness_toggle", "mode": "on"})
        safe_print("Harness mode: ON")
        show_state()
        return 0
    if cmd == "off":
        apply_overlay(
            {
                "harness_enabled": False,
                "strict_mode": False,
                "start_require_ready": False,
                "close_require_spec_sync": False,
                "risk_matrix_enabled": False,
                "autonomy_budget_mode": "off",
                "architecture_enforce": False,
                "spec_state_required": False,
                "spec_link_enforce": False,
                "ddd_lite_mode": "off",
                "mvp_priority_override_mode": "off",
                "precode_guard_mode": "off",
                "tdd_mode": "off",
                "gates_auto_retry_enabled": False,
                "gates_auto_retry_max": 0,
                "gates_auto_fix_on_fail": False,
                "agent_memory_auto_update": False,
                "agent_review_enabled": False,
                "a2a_auto_merge_non_core": False,
                "a2a_allow_commit": False,
                "audit_report_enabled": False,
                "audit_pack_required_on_close": False,
            }
        )
        append_event(paths, {"event": "harness_toggle", "mode": "off"})
        safe_print("Harness mode: OFF")
        show_state()
        return 0

    safe_print(f"Unknown harness action: {cmd}", stream=sys.stderr)
    safe_print(usage, stream=sys.stderr)
    return 1


def cmd_run_evals(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    suite = "all"
    quiet = False
    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--suite":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --suite", stream=sys.stderr)
                return 1
            suite = args[i + 1]
            i += 2
            continue
        if token == "--quiet":
            quiet = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe evals [--suite all|capability|regression] [--quiet]")
            safe_print("")
            safe_print("Run eval suites defined in .claude/workflow/config/evals.json")
            safe_print("Supported item fields:")
            safe_print("- name: eval case name")
            safe_print("- command: shell command to run")
            safe_print("- skip_if_missing: [\"relative/path\", ...]")
            safe_print("- allow_failure: true|false")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    config_file = paths.base.config_dir / "evals.json"
    if not config_file.exists():
        safe_print(f"Missing eval config: {config_file}", stream=sys.stderr)
        return 1

    config = read_json_obj(config_file)
    suites = config.get("suites", {})
    if not isinstance(suites, dict):
        suites = {}
    log_file = paths.base.log_dir / "evals-results.jsonl"

    if suite == "all":
        suites_to_run = ["capability", "regression"]
    elif suite in {"capability", "regression"}:
        suites_to_run = [suite]
    else:
        safe_print(f"Invalid suite: {suite}", stream=sys.stderr)
        return 1

    pass_count = 0
    fail_count = 0
    skip_count = 0
    soft_fail_count = 0
    overall_fail = False

    def run_item(suite_name: str, item: Dict[str, Any]) -> Tuple[str, int]:
        name = str_value(item.get("name", "unnamed_eval"), "unnamed_eval")
        command = str_value(item.get("command", ""), "")
        allow_failure = bool_value(item.get("allow_failure", False), False)
        status = "pass"
        exit_code = 0
        skip_reason = ""

        skip_if_missing = item.get("skip_if_missing", [])
        if not isinstance(skip_if_missing, list):
            skip_if_missing = []
        for rel in skip_if_missing:
            rel_path = str_value(rel, "").strip()
            if not rel_path:
                continue
            if not (paths.base.project_dir / rel_path).exists():
                status = "skip"
                skip_reason = f"missing:{rel_path}"
                break

        if status == "skip":
            append_jsonl(
                log_file,
                {
                    "ts": utc_now(),
                    "suite": suite_name,
                    "name": name,
                    "command": command,
                    "status": status,
                    "skip_reason": skip_reason,
                    "exit_code": exit_code,
                },
            )
            if not quiet:
                safe_print(f"[{suite_name}] {name} => skip ({skip_reason})")
            return status, exit_code

        if not command:
            status = "fail"
            exit_code = 127
        else:
            rc, _, _ = shell_command(command, paths.base.project_dir, quiet=False)
            exit_code = rc
            if rc == 0:
                status = "pass"
            elif allow_failure:
                status = "soft_fail"
            else:
                status = "fail"

        append_jsonl(
            log_file,
            {
                "ts": utc_now(),
                "suite": suite_name,
                "name": name,
                "command": command,
                "status": status,
                "exit_code": exit_code,
                "allow_failure": allow_failure,
            },
        )
        if not quiet:
            safe_print(f"[{suite_name}] {name} => {status}")
        return status, exit_code

    for suite_name in suites_to_run:
        rows = suites.get(suite_name, [])
        if not isinstance(rows, list):
            rows = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            status, _ = run_item(suite_name, item)
            if status == "pass":
                pass_count += 1
            elif status == "skip":
                skip_count += 1
            elif status == "soft_fail":
                soft_fail_count += 1
            elif status == "fail":
                fail_count += 1
                overall_fail = True
            else:
                pass_count += 1

    append_event(
        paths,
        {
            "event": "evals_run",
            "suite": suite,
            "pass": pass_count,
            "fail": fail_count,
            "skip": skip_count,
            "soft_fail": soft_fail_count,
        },
    )

    if not quiet:
        safe_print(f"Eval summary: pass={pass_count} fail={fail_count} skip={skip_count} soft_fail={soft_fail_count}")
    return 1 if overall_fail else 0


def cmd_suggest_gates(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    profile = "standard"
    write_mode = False
    explain_mode = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--profile":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --profile", stream=sys.stderr)
                return 1
            profile = args[i + 1]
            i += 2
            continue
        if token == "--write":
            write_mode = True
            i += 1
            continue
        if token == "--explain":
            explain_mode = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh gates preview [minimal|standard|strict]")
            safe_print("       bash .claude/workflow/rpi.sh gates setup [minimal|standard|strict]")
            safe_print("")
            safe_print("Options:")
            safe_print("  --profile   Gate profile (default: standard)")
            safe_print("  --explain   Print detection notes to stderr")
            safe_print("  --write     Write result to .claude/workflow/config/gates.json")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    if profile not in {"minimal", "standard", "strict"}:
        safe_print(f"Invalid profile: {profile} (must be minimal|standard|strict)", stream=sys.stderr)
        return 1

    commands: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    config_file = paths.base.config_dir / "gates.json"
    package_json_path = paths.base.project_dir / "package.json"
    pyproject_path = paths.base.project_dir / "pyproject.toml"

    def set_gate(gate: str, cmd: str, source: str) -> None:
        if cmd and gate not in commands:
            commands[gate] = cmd
            sources[gate] = source

    def package_json() -> Dict[str, Any]:
        return read_json_obj(package_json_path)

    def has_script(name: str) -> bool:
        pkg = package_json()
        scripts = pkg.get("scripts", {})
        return isinstance(scripts, dict) and name in scripts

    def detect_node_gates() -> None:
        if not package_json_path.exists():
            return

        runner = "npm run"
        audit_cmd = "npm audit --audit-level=high"
        if (paths.base.project_dir / "pnpm-lock.yaml").exists():
            runner = "pnpm run"
            audit_cmd = "pnpm audit --audit-level=high"
        elif (paths.base.project_dir / "yarn.lock").exists():
            runner = "yarn"
            audit_cmd = "yarn npm audit --recursive --all"

        if has_script("test"):
            set_gate("unit_tests", f"{runner} test", "package.json:scripts.test")

        for name in ["test:integration", "integration:test", "test:it"]:
            if has_script(name):
                set_gate("integration_tests", f"{runner} {name}", f"package.json:scripts.{name}")
                break

        for name in ["test:contract", "contract:test"]:
            if has_script(name):
                set_gate("contract_tests", f"{runner} {name}", f"package.json:scripts.{name}")
                break

        for name in ["test:e2e", "e2e", "e2e:test"]:
            if has_script(name):
                set_gate("e2e_tests", f"{runner} {name}", f"package.json:scripts.{name}")
                break

        for name in ["lint", "check:lint"]:
            if has_script(name):
                set_gate("lint", f"{runner} {name}", f"package.json:scripts.{name}")
                break

        for name in ["typecheck", "check-types", "types"]:
            if has_script(name):
                set_gate("typecheck", f"{runner} {name}", f"package.json:scripts.{name}")
                break

        set_gate("security_scan", audit_cmd, "detected package manager")

    def detect_python_gates() -> None:
        has_py = any(
            (paths.base.project_dir / marker).exists()
            for marker in ["pyproject.toml", "requirements.txt", "setup.py"]
        )
        if not has_py:
            return

        if (paths.base.project_dir / "tests" / "unit").is_dir():
            set_gate("unit_tests", "pytest -q tests/unit", "tests/unit directory")
        else:
            set_gate("unit_tests", "pytest -q", "python project default")

        if (paths.base.project_dir / "tests" / "integration").is_dir():
            set_gate("integration_tests", "pytest -q tests/integration", "tests/integration directory")
        if (paths.base.project_dir / "tests" / "e2e").is_dir():
            set_gate("e2e_tests", "pytest -q tests/e2e", "tests/e2e directory")

        pyproject_text = pyproject_path.read_text(encoding="utf-8", errors="ignore") if pyproject_path.exists() else ""
        if re.search(r"\bruff\b", pyproject_text):
            set_gate("lint", "ruff check .", "pyproject.toml contains ruff")
        if re.search(r"\bmypy\b", pyproject_text):
            set_gate("typecheck", "mypy .", "pyproject.toml contains mypy")

        set_gate("security_scan", "pip-audit", "python security baseline")

    def detect_nx_or_turbo_defaults() -> None:
        if (paths.base.project_dir / "turbo.json").exists():
            if (paths.base.project_dir / "pnpm-lock.yaml").exists():
                set_gate("unit_tests", "pnpm turbo run test --filter=...[HEAD^1]", "turbo.json + pnpm")
                set_gate("integration_tests", "pnpm turbo run test:integration --filter=...[HEAD^1]", "turbo.json + pnpm")
                set_gate("contract_tests", "pnpm turbo run test:contract --filter=...[HEAD^1]", "turbo.json + pnpm")
                set_gate("lint", "pnpm turbo run lint --filter=...[HEAD^1]", "turbo.json + pnpm")
                set_gate("typecheck", "pnpm turbo run typecheck --filter=...[HEAD^1]", "turbo.json + pnpm")
            else:
                set_gate("unit_tests", "npx turbo run test --filter=...[HEAD^1]", "turbo.json")
                set_gate("lint", "npx turbo run lint --filter=...[HEAD^1]", "turbo.json")
                set_gate("typecheck", "npx turbo run typecheck --filter=...[HEAD^1]", "turbo.json")

        if (paths.base.project_dir / "nx.json").exists():
            set_gate("unit_tests", "npx nx affected --target=test", "nx.json")
            set_gate("integration_tests", "npx nx affected --target=test-integration", "nx.json")
            set_gate("contract_tests", "npx nx affected --target=test-contract", "nx.json")
            set_gate("e2e_tests", "npx nx affected --target=e2e", "nx.json")
            set_gate("lint", "npx nx affected --target=lint", "nx.json")
            set_gate("typecheck", "npx nx affected --target=typecheck", "nx.json")

    def detect_workspace_fallback() -> None:
        has_workspace = False
        if (paths.base.project_dir / "pnpm-workspace.yaml").exists():
            has_workspace = True
            set_gate("unit_tests", "pnpm -r --if-present test", "pnpm workspace fallback")
            set_gate("integration_tests", "pnpm -r --if-present test:integration", "pnpm workspace fallback")
            set_gate("contract_tests", "pnpm -r --if-present test:contract", "pnpm workspace fallback")
            set_gate("e2e_tests", "pnpm -r --if-present test:e2e", "pnpm workspace fallback")
            set_gate("lint", "pnpm -r --if-present lint", "pnpm workspace fallback")
            set_gate("typecheck", "pnpm -r --if-present typecheck", "pnpm workspace fallback")
            set_gate("security_scan", "pnpm audit --audit-level=high", "pnpm workspace fallback")

        if has_workspace:
            return

        if not package_json_path.exists():
            return
        pkg = package_json()
        if "workspaces" not in pkg:
            return

        if (paths.base.project_dir / "yarn.lock").exists():
            set_gate("unit_tests", "yarn workspaces foreach -pt run test", "yarn workspace fallback")
            set_gate("integration_tests", "yarn workspaces foreach -pt run test:integration", "yarn workspace fallback")
            set_gate("contract_tests", "yarn workspaces foreach -pt run test:contract", "yarn workspace fallback")
            set_gate("e2e_tests", "yarn workspaces foreach -pt run test:e2e", "yarn workspace fallback")
            set_gate("lint", "yarn workspaces foreach -pt run lint", "yarn workspace fallback")
            set_gate("typecheck", "yarn workspaces foreach -pt run typecheck", "yarn workspace fallback")
            set_gate("security_scan", "yarn npm audit --recursive --all", "yarn workspace fallback")
        else:
            set_gate("unit_tests", "npm run -ws --if-present test", "npm workspace fallback")
            set_gate("integration_tests", "npm run -ws --if-present test:integration", "npm workspace fallback")
            set_gate("contract_tests", "npm run -ws --if-present test:contract", "npm workspace fallback")
            set_gate("e2e_tests", "npm run -ws --if-present test:e2e", "npm workspace fallback")
            set_gate("lint", "npm run -ws --if-present lint", "npm workspace fallback")
            set_gate("typecheck", "npm run -ws --if-present typecheck", "npm workspace fallback")
            set_gate("security_scan", "npm audit --audit-level=high", "npm workspace fallback")

    detect_node_gates()
    detect_python_gates()
    detect_nx_or_turbo_defaults()
    detect_workspace_fallback()

    def filter_available(candidates: Iterable[str]) -> List[str]:
        return [g for g in candidates if g in commands]

    base_candidates = ["unit_tests", "lint", "typecheck"]
    base_gates = filter_available(base_candidates)
    if not base_gates:
        all_keys = sorted(commands.keys())
        if all_keys:
            base_gates = [all_keys[0]]
        elif (paths.base.workflow_dir / "rpi.sh").exists():
            commands["bootstrap_check"] = "bash .claude/workflow/rpi.sh check bootstrap"
            sources["bootstrap_check"] = "empty project bootstrap fallback"
            base_gates = ["bootstrap_check"]
        else:
            commands["unit_tests"] = "__REQUIRED__"
            sources["unit_tests"] = "not detected"
            base_gates = ["unit_tests"]

    extra_m1 = ["integration_tests", "contract_tests"]
    extra_m2 = ["e2e_tests", "security_scan"]

    if profile == "minimal":
        m0 = list(base_gates)
        m1 = list(base_gates)
        m2 = list(base_gates)
    elif profile == "standard":
        m0 = list(base_gates)
        m1 = list(base_gates) + filter_available(extra_m1)
        m2 = list(m1) + filter_available(extra_m2)
    else:
        strict_all = filter_available(
            ["unit_tests", "integration_tests", "contract_tests", "e2e_tests", "lint", "typecheck", "security_scan"]
        )
        if not strict_all:
            strict_all = list(base_gates)
        m0 = list(strict_all)
        m1 = list(strict_all)
        m2 = list(strict_all)

    verify_json: Dict[str, Any] = {
        "default": [
            {"name": "discovery_complete", "command": "bash .claude/workflow/rpi.sh check discovery --quiet"},
            {"name": "contract_spec_complete", "command": "bash .claude/workflow/rpi.sh check contract --quiet"},
            {"name": "scope_guard_passed", "command": "bash .claude/workflow/rpi.sh check scope --quiet"},
            {"name": "spec_state_valid", "command": "bash .claude/workflow/rpi.sh spec verify --scope all --quiet"},
            {"name": "architecture_guard_passed", "command": "bash .claude/workflow/rpi.sh check architecture --quiet"},
        ],
        "M0": [],
        "M1": [],
        "M2": [],
    }
    existing = read_json_obj(config_file)
    existing_verify = existing.get("verify")
    if isinstance(existing_verify, dict):
        verify_json = existing_verify

    output = {
        "phase_gates": {"M0": m0, "M1": m1, "M2": m2},
        "commands": commands,
        "verify": verify_json,
    }

    if explain_mode:
        safe_print(f"[suggest-gates] profile={profile}", stream=sys.stderr)
        for gate in sorted(commands.keys()):
            safe_print(f"- {gate}: {commands[gate]} (source: {sources.get(gate, 'unknown')})", stream=sys.stderr)

    if write_mode:
        write_json_atomic(config_file, output)
        safe_print(f"Wrote {config_file}", stream=sys.stderr)

    safe_print(json.dumps(output, ensure_ascii=False))
    return 0


def cmd_anti_entropy(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    auto_fix = False
    strict = False
    json_output = False
    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--auto-fix":
            auto_fix = True
            i += 1
            continue
        if token == "--strict":
            strict = True
            i += 1
            continue
        if token == "--json":
            json_output = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh auto entropy [--auto-fix] [--strict] [--json]")
            safe_print("")
            safe_print("Periodic anti-entropy scan for drift and stale state.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    runtime = load_runtime(paths)
    state_file = paths.state_spec_dir / "state.json"
    discovery_file = paths.spec_l0_dir / "discovery.md"
    spec_file = paths.spec_l0_dir / "spec.md"
    tasks_file = paths.spec_l0_dir / "tasks.md"

    issues: List[Dict[str, Any]] = []

    def add_issue(severity: str, category: str, message: str, auto_fixable: bool, fixed: bool) -> None:
        issues.append(
            {
                "severity": severity,
                "category": category,
                "message": message,
                "auto_fixable": auto_fixable,
                "fixed": fixed,
            }
        )

    def run_spec_build() -> bool:
        return guardrails.build_spec_state(paths.base.project_dir, quiet=True) == 0

    def run_spec_verify() -> bool:
        result = guardrails.verify_spec_state(paths.base.project_dir, scope="all", quiet=True)
        return str_value(result.get("status", "")).lower() == "pass"

    def run_arch_check() -> bool:
        require_rules = bool_value(runtime.get("architecture_require_rules", False), False)
        result = guardrails.architecture_check(
            paths.base.project_dir,
            quiet=True,
            json_output=False,
            require_rules=require_rules,
        )
        return str_value(result.get("status", "")).lower() == "pass"

    _ = run_spec_build()
    if not run_spec_verify():
        fixed = False
        if auto_fix:
            _ = run_spec_build()
            fixed = run_spec_verify()
        add_issue("high", "spec_state", "spec state verification failed", True, fixed)

    if bool_value(runtime.get("architecture_enforce", False), False):
        if not run_arch_check():
            add_issue("high", "architecture", "architecture boundary check failed", False, False)

    if state_file.exists():
        state_mtime = file_mtime(state_file)
        latest_spec_mtime = max(file_mtime(discovery_file), file_mtime(spec_file), file_mtime(tasks_file))
        if latest_spec_mtime > state_mtime:
            fixed = False
            if auto_fix:
                fixed = run_spec_build()
            add_issue(
                "medium",
                "state_stale",
                "spec state file is older than source markdown files",
                True,
                fixed,
            )

    todo_count = 0
    todo_pattern = re.compile(r"(TODO|FIXME)")
    for root, dirs, files in os.walk(paths.base.project_dir):
        dirs[:] = [d for d in dirs if d not in {".git", ".rpi-outfile", "node_modules"}]
        for name in files:
            p = Path(root) / name
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            todo_count += len(todo_pattern.findall(text))
    if todo_count > 0:
        add_issue("low", "todo_debt", f"found {todo_count} TODO/FIXME markers in repository", False, False)

    if tasks_file.exists():
        text = tasks_file.read_text(encoding="utf-8", errors="ignore")
        ids = []
        for m in re.findall(r"task[ -]?0*([0-9]{1,4})", text, flags=re.IGNORECASE):
            num = int(m)
            if num > 0:
                ids.append(f"TASK-{num:03d}")
        dup_count = sum(1 for _, c in Counter(ids).items() if c > 1)
        if dup_count > 0:
            add_issue("high", "task_ids", "duplicate task ids detected in tasks.md", False, False)

    issue_count = len(issues)
    high_count = len([i for i in issues if i.get("severity") == "high" and not bool_value(i.get("fixed", False), False)])

    report_ts = utc_now()
    report_file = paths.base.log_dir / f"anti-entropy-{report_ts.replace(':', '_')}.json"
    report = {
        "ts": report_ts,
        "auto_fix": auto_fix,
        "issue_count": issue_count,
        "high_unfixed_count": high_count,
        "issues": issues,
    }
    write_json_atomic(report_file, report)
    append_event(
        paths,
        {
            "event": "anti_entropy",
            "issue_count": issue_count,
            "high_unfixed_count": high_count,
        },
    )

    if json_output:
        safe_print(json.dumps(report, ensure_ascii=False))
    else:
        safe_print(f"Anti-entropy report: {report_file}")
        safe_print(f"issues={issue_count} high_unfixed={high_count}")

    if strict and high_count > 0:
        return 1
    return 0


def tail_lines(path: Path, limit: int) -> str:
    if not path.exists() or limit <= 0:
        return ""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-limit:]) + ("\n" if lines else "")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_build_audit_pack(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    task_id = ""
    output_dir_raw = ""
    make_tar = False
    limit_events = 2000

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--task":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --task", stream=sys.stderr)
                return 1
            task_id = args[i + 1]
            i += 2
            continue
        if token == "--output":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --output", stream=sys.stderr)
                return 1
            output_dir_raw = args[i + 1]
            i += 2
            continue
        if token == "--tar":
            make_tar = True
            i += 1
            continue
        if token == "--limit-events":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --limit-events", stream=sys.stderr)
                return 1
            limit_events = int_value(args[i + 1], -1)
            i += 2
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe audit-pack [--task <TASK-001>] [--output <dir>] [--tar] [--limit-events <n>]")
            safe_print("")
            safe_print("Export an audit evidence pack from runtime state and logs.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    if limit_events < 1:
        safe_print(f"Invalid --limit-events value: {limit_events}", stream=sys.stderr)
        return 1

    if not task_id:
        current = read_json_obj(paths.base.current_task_file)
        task_id = str_value(current.get("task_id", ""), "")
        if not task_id:
            task_id = "SESSION"

    stamp = utc_compact_now()
    if output_dir_raw:
        out_path = Path(output_dir_raw).expanduser()
        if not out_path.is_absolute():
            out_path = (paths.base.project_dir / out_path).resolve()
    else:
        out_path = paths.audit_root_dir / f"{task_id}-{stamp}"

    for rel in ["state", "logs", "config", "spec", "meta"]:
        (out_path / rel).mkdir(parents=True, exist_ok=True)

    def safe_copy(src: Path, dst: Path) -> None:
        if not src.exists() or not src.is_file():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    safe_copy(paths.base.current_task_file, out_path / "state" / "current_task.json")
    safe_copy(paths.base.phase_file, out_path / "state" / "project_phase.json")
    safe_copy(paths.base.runtime_file, out_path / "config" / "runtime.json")
    safe_copy(paths.base.config_dir / "gates.json", out_path / "config" / "gates.json")
    safe_copy(paths.base.config_dir / "architecture.rules.json", out_path / "config" / "architecture.rules.json")
    safe_copy(paths.base.config_dir / "evals.json", out_path / "config" / "evals.json")

    safe_copy(paths.state_spec_dir / "state.json", out_path / "state" / "spec_state.json")
    safe_copy(paths.state_spec_dir / "verification.json", out_path / "state" / "spec_verification.json")
    safe_copy(paths.state_spec_dir / "links.json", out_path / "state" / "spec_links.json")

    safe_copy(paths.spec_l0_dir / "discovery.md", out_path / "spec" / "discovery.md")
    safe_copy(paths.spec_l0_dir / "spec.md", out_path / "spec" / "spec.md")
    safe_copy(paths.spec_l0_dir / "tasks.md", out_path / "spec" / "tasks.md")
    safe_copy(paths.spec_l0_dir / "milestones.md", out_path / "spec" / "milestones.md")
    safe_copy(paths.spec_l0_dir / "epic.md", out_path / "spec" / "epic.md")

    if paths.base.event_log.exists():
        (out_path / "logs" / "events.tail.jsonl").write_text(tail_lines(paths.base.event_log, limit_events), encoding="utf-8")
        safe_copy(paths.base.event_log, out_path / "logs" / "events.full.jsonl")
    if paths.base.gate_log.exists():
        (out_path / "logs" / "gate-results.tail.jsonl").write_text(tail_lines(paths.base.gate_log, limit_events), encoding="utf-8")
        safe_copy(paths.base.gate_log, out_path / "logs" / "gate-results.full.jsonl")
    safe_copy(paths.base.log_dir / "evals-results.jsonl", out_path / "logs" / "evals-results.jsonl")
    safe_copy(paths.base.log_dir / "trace-grades.jsonl", out_path / "logs" / "trace-grades.jsonl")

    branch = ""
    commit = ""
    status_short = ""
    try:
        check = subprocess.run(
            ["git", "-C", str(paths.base.project_dir), "rev-parse", "--is-inside-work-tree"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if check.returncode == 0:
            branch = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "branch", "--show-current"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout.strip()
            commit = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "rev-parse", "HEAD"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout.strip()
            status_short = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "status", "--short"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout.strip()
    except Exception:
        pass

    files_manifest: List[Dict[str, str]] = []
    for p in sorted(x for x in out_path.rglob("*") if x.is_file()):
        rel = str(p.relative_to(out_path)).replace("\\", "/")
        files_manifest.append({"path": rel, "sha256": file_sha256(p)})

    manifest = {
        "created_at": utc_now(),
        "task_id": task_id,
        "project_dir": str(paths.base.project_dir),
        "git": {"branch": branch, "commit": commit, "status_short": status_short},
        "files": files_manifest,
    }
    write_json_atomic(out_path / "meta" / "manifest.json", manifest)
    append_event(paths, {"event": "audit_pack", "task_id": task_id, "output": str(out_path)})
    safe_print(f"Audit pack built: {out_path}")

    if make_tar:
        tar_path = Path(str(out_path) + ".tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(out_path, arcname=out_path.name)
        safe_print(f"Audit pack archived: {tar_path}")
    return 0


def top_entries(counter: Counter[str], limit: int = 10) -> List[Dict[str, Any]]:
    return [{"key": key, "value": value} for key, value in counter.most_common(limit)]


def cmd_audit_report(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    task_filter = ""
    days = 30
    json_output = False
    output_dir_raw = ""

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--task":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --task", stream=sys.stderr)
                return 1
            task_filter = args[i + 1]
            i += 2
            continue
        if token == "--days":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --days", stream=sys.stderr)
                return 1
            days = int_value(args[i + 1], -1)
            i += 2
            continue
        if token == "--json":
            json_output = True
            i += 1
            continue
        if token == "--output":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --output", stream=sys.stderr)
                return 1
            output_dir_raw = args[i + 1]
            i += 2
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe audit-report [--task <TASK-ID>] [--days <n>] [--output <dir>] [--json]")
            safe_print("")
            safe_print("Generate audit analytics report from task archives and runtime logs.")
            safe_print("Output files:")
            safe_print("  .rpi-outfile/audit/reports/audit-report-<timestamp>.json")
            safe_print("  .rpi-outfile/audit/reports/audit-report-<timestamp>.md")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    if days < 1:
        safe_print(f"Invalid --days value: {days}", stream=sys.stderr)
        return 1

    if output_dir_raw:
        output_dir = Path(output_dir_raw).expanduser()
        if not output_dir.is_absolute():
            output_dir = (paths.base.project_dir / output_dir).resolve()
    else:
        output_dir = paths.audit_root_dir / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    archives: List[Dict[str, Any]] = []
    for f in sorted(paths.tasks_archive_dir.glob("*.json")):
        row = read_json_obj(f)
        if not row:
            continue
        stamp = str_value(row.get("closed_at", ""), "") or str_value(row.get("last_updated_at", ""), "")
        if stamp and not within_window(stamp, cutoff):
            continue
        if task_filter and str_value(row.get("task_id", ""), "") != task_filter:
            continue
        archives.append(row)

    tasks_total = len(archives)
    tasks_pass = len([t for t in archives if str_value(t.get("result", ""), "") == "pass"])
    tasks_fail = len([t for t in archives if str_value(t.get("result", ""), "") == "fail"])
    stale_spec_sync_tasks = len([t for t in archives if str_value(((t.get("spec_sync") or {}).get("status", "")), "") == "stale"])
    root_causes = Counter(str_value(((t.get("classification") or {}).get("root_cause", "unknown")), "unknown") for t in archives)

    durations: List[float] = []
    for t in archives:
        created = parse_iso(str_value(t.get("created_at", ""), ""))
        closed = parse_iso(str_value(t.get("closed_at", ""), ""))
        if created and closed and closed >= created:
            durations.append((closed - created).total_seconds() / 60.0)
    avg_task_minutes = (sum(durations) / len(durations)) if durations else 0

    gate_rows = [r for r in parse_jsonl(paths.base.gate_log) if within_window(str_value(r.get("ts", ""), ""), cutoff)]
    gate_failures = [r for r in gate_rows if str_value(r.get("status", ""), "") == "fail"]
    gate_counter = Counter(str_value(r.get("gate", "unknown"), "unknown") for r in gate_failures)

    event_rows = [r for r in parse_jsonl(paths.base.event_log) if within_window(str_value(r.get("ts", ""), ""), cutoff)]
    pre_tool_blocks = len([r for r in event_rows if str_value(r.get("event", ""), "") == "pre_tool_block"])
    pre_tool_warns = len([r for r in event_rows if str_value(r.get("event", ""), "") == "pre_tool_warn"])
    risk_counter = Counter(str_value(r.get("risk_rule", ""), "") for r in event_rows if str_value(r.get("risk_rule", ""), ""))
    auto_rpi_rows = [r for r in event_rows if str_value(r.get("event", ""), "") == "auto_rpi"]
    auto_rpi_total = len(auto_rpi_rows)
    auto_rpi_success = len([r for r in auto_rpi_rows if bool_value(r.get("success", False), False)])

    trace_grades: List[Dict[str, Any]] = []
    trace_path = paths.base.log_dir / "trace-grades.jsonl"
    if trace_path.exists():
        trace_rows = [r for r in parse_jsonl(trace_path) if within_window(str_value(r.get("ts", ""), ""), cutoff)]
        grade_counter = Counter(str_value(r.get("grade", ""), "") for r in trace_rows if str_value(r.get("grade", ""), ""))
        trace_grades = [{"key": k, "value": v} for k, v in sorted(grade_counter.items(), key=lambda x: x[0])]

    ts = utc_now()
    stamp = utc_compact_now()
    report_json_file = output_dir / f"audit-report-{stamp}.json"
    report_md_file = output_dir / f"audit-report-{stamp}.md"

    report = {
        "generated_at": ts,
        "scope": {"task_filter": task_filter, "days": days, "cutoff_iso": cutoff_iso},
        "task_metrics": {
            "tasks_total": tasks_total,
            "tasks_pass": tasks_pass,
            "tasks_fail": tasks_fail,
            "root_causes": dict(root_causes),
            "stale_spec_sync_tasks": stale_spec_sync_tasks,
            "avg_task_minutes": avg_task_minutes,
        },
        "gate_metrics": {
            "gate_failures_total": len(gate_failures),
            "top_failed_gates": top_entries(gate_counter, limit=10),
        },
        "event_metrics": {
            "pre_tool_blocks": pre_tool_blocks,
            "pre_tool_warns": pre_tool_warns,
            "risk_rules": top_entries(risk_counter, limit=10),
            "auto_rpi_total": auto_rpi_total,
            "auto_rpi_success": auto_rpi_success,
            "auto_rpi_success_rate": 0 if auto_rpi_total == 0 else (auto_rpi_success / auto_rpi_total),
        },
        "trace_metrics": {"trace_grades": trace_grades},
    }
    write_json_atomic(report_json_file, report)

    lines: List[str] = []
    lines.append("# Audit Report")
    lines.append("")
    lines.append(f"- generated_at: {ts}")
    lines.append(f"- task_filter: {task_filter if task_filter else 'all'}")
    lines.append(f"- window_days: {days}")
    lines.append(f"- cutoff: {cutoff_iso}")
    lines.append("")
    lines.append("## Task Metrics")
    lines.append(f"- tasks_total: {tasks_total}")
    lines.append(f"- tasks_pass: {tasks_pass}")
    lines.append(f"- tasks_fail: {tasks_fail}")
    lines.append(f"- stale_spec_sync_tasks: {stale_spec_sync_tasks}")
    lines.append(f"- avg_task_minutes: {avg_task_minutes}")
    lines.append("")
    lines.append("### Root Causes")
    for key, value in root_causes.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Gate Metrics")
    lines.append(f"- gate_failures_total: {len(gate_failures)}")
    lines.append("### Top Failed Gates")
    for row in top_entries(gate_counter, limit=10):
        lines.append(f"- {row['key']}: {row['value']}")
    lines.append("")
    lines.append("## Runtime Signals")
    lines.append(f"- pre_tool_blocks: {pre_tool_blocks}")
    lines.append(f"- pre_tool_warns: {pre_tool_warns}")
    lines.append(f"- auto_rpi_total: {auto_rpi_total}")
    lines.append(f"- auto_rpi_success: {auto_rpi_success}")
    lines.append(f"- auto_rpi_success_rate: {report['event_metrics']['auto_rpi_success_rate']}")
    lines.append("### Risk Rules")
    for row in top_entries(risk_counter, limit=10):
        lines.append(f"- {row['key']}: {row['value']}")
    lines.append("")
    lines.append("## Trace Grades")
    for row in trace_grades:
        lines.append(f"- {row['key']}: {row['value']}")
    report_md_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    append_event(paths, {"event": "audit_report", "report_json": str(report_json_file), "report_md": str(report_md_file)})

    if json_output:
        safe_print(json.dumps(report, ensure_ascii=False))
    else:
        safe_print(f"Audit report JSON: {report_json_file}")
        safe_print(f"Audit report MD:   {report_md_file}")
    return 0


def cmd_auto_rpi(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    phase = ""
    max_rounds_raw = ""
    max_minutes_raw = ""
    max_failures_raw = ""
    max_tool_events_raw = ""
    auto_fix_raw = ""
    force = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--phase":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --phase", stream=sys.stderr)
                return 1
            phase = args[i + 1]
            i += 2
            continue
        if token == "--max-rounds":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --max-rounds", stream=sys.stderr)
                return 1
            max_rounds_raw = args[i + 1]
            i += 2
            continue
        if token == "--max-minutes":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --max-minutes", stream=sys.stderr)
                return 1
            max_minutes_raw = args[i + 1]
            i += 2
            continue
        if token == "--max-failures":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --max-failures", stream=sys.stderr)
                return 1
            max_failures_raw = args[i + 1]
            i += 2
            continue
        if token == "--max-tool-events":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --max-tool-events", stream=sys.stderr)
                return 1
            max_tool_events_raw = args[i + 1]
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
        if token == "--force":
            force = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh auto run [--phase M0|M1|M2] [--max-rounds N] [--max-minutes M] [--max-failures N] [--max-tool-events N] [--auto-fix|--no-auto-fix] [--force]")
            safe_print("")
            safe_print("Run controlled autonomous RPI loop:")
            safe_print("1) spec-build + spec-verify")
            safe_print("2) contract/scope/architecture checks")
            safe_print("3) quality gate")
            safe_print("4) optional anti-entropy auto-fix and retry")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    runtime = load_runtime(paths)
    if not phase:
        phase_data = read_json_obj(paths.base.phase_file)
        phase = str_value(phase_data.get("phase", "M0"), "M0")
    phase = normalize_phase(phase, "")
    if phase not in {"M0", "M1", "M2"}:
        safe_print(f"Invalid phase: {phase}", stream=sys.stderr)
        return 1

    auto_enabled = bool_value(runtime.get("auto_rpi_enabled", False), False)
    if not auto_enabled and not force:
        safe_print("auto-rpi is disabled in runtime.json. Use --force to override.", stream=sys.stderr)
        return 1

    max_rounds = int_value(max_rounds_raw, int_value(runtime.get("auto_rpi_max_rounds", 2), 2))
    max_minutes = int_value(max_minutes_raw, int_value(runtime.get("auto_rpi_max_minutes", 30), 30))
    max_failures = int_value(max_failures_raw, int_value(runtime.get("auto_rpi_max_failures", 2), 2))
    max_tool_events = int_value(max_tool_events_raw, int_value(runtime.get("auto_rpi_max_tool_events", 200), 200))
    auto_fix = bool_value(auto_fix_raw, bool_value(runtime.get("auto_rpi_auto_fix", False), False))

    if max_rounds < 1:
        safe_print(f"Invalid max rounds: {max_rounds}", stream=sys.stderr)
        return 1
    if max_minutes < 1:
        safe_print(f"Invalid max minutes: {max_minutes}", stream=sys.stderr)
        return 1
    if max_failures < 0:
        safe_print(f"Invalid max failures: {max_failures}", stream=sys.stderr)
        return 1
    if max_tool_events < 1:
        safe_print(f"Invalid max tool events: {max_tool_events}", stream=sys.stderr)
        return 1

    loop_log = paths.base.log_dir / "auto-rpi.jsonl"
    start_epoch = int(time.time())
    start_event_count = count_lines(paths.base.event_log)

    def elapsed_minutes_now() -> int:
        return (int(time.time()) - start_epoch) // 60

    def event_delta_now() -> int:
        return count_lines(paths.base.event_log) - start_event_count

    def run_round(round_id: int) -> bool:
        ok = True
        notes: List[str] = []

        if guardrails.build_spec_state(paths.base.project_dir, quiet=True) != 0:
            ok = False
            notes.append("spec-build failed")

        verify_result = guardrails.verify_spec_state(paths.base.project_dir, scope="all", quiet=True)
        if str_value(verify_result.get("status", ""), "") != "pass":
            ok = False
            notes.append("spec-verify failed")

        contract = guardrails.check_contract_spec(paths.base.project_dir, quiet=True)
        if str_value(contract.get("status", ""), "") != "pass":
            ok = False
            notes.append("contract check failed")

        scope = guardrails.check_scope_guard(paths.base.project_dir, quiet=True)
        if str_value(scope.get("status", ""), "") != "pass":
            ok = False
            notes.append("scope guard failed")

        arch_enforce = bool_value(runtime.get("architecture_enforce", False), False)
        if arch_enforce:
            require_rules = bool_value(runtime.get("architecture_require_rules", False), False)
            arch = guardrails.architecture_check(
                paths.base.project_dir,
                quiet=True,
                json_output=False,
                require_rules=require_rules,
            )
            if str_value(arch.get("status", ""), "") != "pass":
                ok = False
                notes.append("architecture check failed")

        q_rc = task_flow.cmd_quality_gate(paths.base, [phase])
        if q_rc != 0:
            ok = False
            notes.append("quality gate failed")

        append_jsonl(
            loop_log,
            {
                "ts": utc_now(),
                "phase": phase,
                "round": round_id,
                "ok": ok,
                "notes": notes,
            },
        )
        return ok

    success = False
    failures = 0
    stop_reason = ""

    for i_round in range(1, max_rounds + 1):
        elapsed = elapsed_minutes_now()
        if elapsed >= max_minutes:
            stop_reason = f"time_budget_exceeded:{elapsed}m/{max_minutes}m"
            break
        event_delta = event_delta_now()
        if event_delta >= max_tool_events:
            stop_reason = f"tool_event_budget_exceeded:{event_delta}/{max_tool_events}"
            break

        if run_round(i_round):
            success = True
            break

        failures += 1
        if failures > max_failures:
            stop_reason = f"failure_budget_exceeded:{failures}/{max_failures}"
            break

        if auto_fix:
            _ = cmd_anti_entropy(paths, ["--auto-fix"])
        else:
            break

    append_event(
        paths,
        {
            "event": "auto_rpi",
            "phase": phase,
            "max_rounds": max_rounds,
            "max_minutes": max_minutes,
            "max_failures": max_failures,
            "max_tool_events": max_tool_events,
            "auto_fix": auto_fix,
            "success": success,
            "stop_reason": stop_reason,
        },
    )

    if success:
        agent_review_enabled = bool_value(runtime.get("agent_review_enabled", False), False)
        auto_merge_non_core = bool_value(runtime.get("a2a_auto_merge_non_core", False), False)
        if agent_review_enabled and auto_merge_non_core:
            _ = cmd_a2a_review(paths, ["--auto-merge", "--quiet"])
        safe_print(
            f"auto-rpi succeeded (phase={phase}, rounds<={max_rounds}, minutes<={max_minutes}, events<={max_tool_events})"
        )
        return 0

    if stop_reason:
        safe_print(f"auto-rpi stopped by budget constraint ({stop_reason}).", stream=sys.stderr)
    safe_print(f"auto-rpi failed (phase={phase}). See {loop_log} for details.", stream=sys.stderr)
    return 1


def cmd_a2a_review(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    base_ref = ""
    head_ref = ""
    auto_merge = False
    quiet = False
    json_output = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--base":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --base", stream=sys.stderr)
                return 1
            base_ref = args[i + 1]
            i += 2
            continue
        if token == "--head":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --head", stream=sys.stderr)
                return 1
            head_ref = args[i + 1]
            i += 2
            continue
        if token == "--auto-merge":
            auto_merge = True
            i += 1
            continue
        if token == "--quiet":
            quiet = True
            i += 1
            continue
        if token == "--json":
            json_output = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh auto review [--base <ref>] [--head <ref>] [--auto-merge] [--quiet] [--json]")
            safe_print("")
            safe_print("Agent-to-Agent review flow:")
            safe_print("1) Collect changed files")
            safe_print("2) Run deterministic checks against spec/architecture/risk")
            safe_print("3) Approve or reject")
            safe_print("4) Optional auto-merge (commit) for non-core changes if runtime allows it")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    check = subprocess.run(
        ["git", "-C", str(paths.base.project_dir), "rev-parse", "--is-inside-work-tree"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        safe_print("a2a-review requires a git repository", stream=sys.stderr)
        return 1

    runtime = load_runtime(paths)
    if not bool_value(runtime.get("agent_review_enabled", True), True):
        safe_print("agent review is disabled in runtime.json", stream=sys.stderr)
        return 1

    def collect_changed_files() -> List[str]:
        if base_ref:
            head = head_ref if head_ref else "HEAD"
            proc = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "diff", "--name-only", base_ref, head],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            text = proc.stdout or ""
            items = [x.strip() for x in text.splitlines() if x.strip()]
            return sorted(set(items))

        outputs: List[str] = []
        for cmd in [
            ["git", "-C", str(paths.base.project_dir), "diff", "--name-only"],
            ["git", "-C", str(paths.base.project_dir), "diff", "--name-only", "--cached"],
        ]:
            proc = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            outputs.extend([x.strip() for x in (proc.stdout or "").splitlines() if x.strip()])
        items = sorted(set(outputs))
        if items:
            return items

        rev = subprocess.run(
            ["git", "-C", str(paths.base.project_dir), "rev-parse", "--verify", "HEAD~1"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if rev.returncode == 0:
            proc = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "diff", "--name-only", "HEAD~1", "HEAD"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return sorted(set([x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]))
        return []

    def is_core_file(path: str) -> bool:
        p = path.replace("\\", "/")
        if re.search(r"(^|/)(README\.md|QUICKSTART\.md|CHANGELOG\.md|AGENTS\.md)$", p):
            return False
        if re.search(r"(^|/)\.rpi-outfile/", p) or re.search(r"(^|/)\.claude/", p):
            return False
        if re.search(r"(^|/)(docs|specs|design)/", p):
            return False
        if re.search(r"(^|/)(tests?|__tests__)/", p) or re.search(r"\.(test|spec)\.[^/]+$", p):
            return False
        if re.search(r"\.(md|json|ya?ml|toml|ini|cfg|txt)$", p):
            return False
        if re.search(r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|java|kt|rb|rs|php|cs|swift|scala|sh|sql)$", p):
            return True
        if re.search(r"(^|/)(src|app|apps|packages|lib|server|backend|frontend)/", p):
            return True
        return False

    changed_files = collect_changed_files()
    if not changed_files:
        safe_print("No changed files to review.", stream=sys.stderr)
        return 1

    non_core_change = not any(is_core_file(f) for f in changed_files)
    checks: List[Dict[str, Any]] = []

    def add_check(name: str, status: bool) -> None:
        checks.append(
            {
                "name": name,
                "status": "pass" if status else "fail",
                "exit_code": 0 if status else 1,
            }
        )

    spec_verify = guardrails.verify_spec_state(paths.base.project_dir, scope="all", quiet=True)
    add_check("spec_verify", str_value(spec_verify.get("status", ""), "") == "pass")
    discovery = guardrails.check_discovery(paths.base.project_dir, quiet=True)
    add_check("discovery_check", str_value(discovery.get("status", ""), "") == "pass")
    contract = guardrails.check_contract_spec(paths.base.project_dir, quiet=True)
    add_check("contract_check", str_value(contract.get("status", ""), "") == "pass")
    scope = guardrails.check_scope_guard(paths.base.project_dir, quiet=True)
    add_check("scope_check", str_value(scope.get("status", ""), "") == "pass")

    if bool_value(runtime.get("architecture_enforce", False), False):
        arch = guardrails.architecture_check(paths.base.project_dir, quiet=True, json_output=False, require_rules=False)
        add_check("architecture_check", str_value(arch.get("status", ""), "") == "pass")

    risk_decisions: List[Dict[str, Any]] = []
    risk_fail = False
    for f in changed_files:
        risk = guardrails.assess_risk(paths.base.project_dir, tool="Edit", value=f, profile_override="")
        risk_decisions.append({"file": f, "risk": risk})
        if str_value(risk.get("decision", "allow"), "allow") == "deny":
            risk_fail = True

    check_fail_count = len([c for c in checks if c.get("status") == "fail"])
    approved = check_fail_count == 0 and not risk_fail
    auto_merge_candidate = approved and non_core_change
    auto_merged = False
    merge_note = ""

    if auto_merge:
        allow_commit = bool_value(runtime.get("a2a_allow_commit", False), False)
        if not allow_commit:
            merge_note = "runtime.a2a_allow_commit=false"
        elif not auto_merge_candidate:
            merge_note = "not_non_core_or_not_approved"
        else:
            subprocess.run(["git", "-C", str(paths.base.project_dir), "add", "-A"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            staged = subprocess.run(
                ["git", "-C", str(paths.base.project_dir), "diff", "--cached", "--quiet"],
                check=False,
            )
            if staged.returncode == 0:
                merge_note = "no_staged_changes"
            else:
                commit = subprocess.run(
                    ["git", "-C", str(paths.base.project_dir), "commit", "-m", "chore(a2a): auto-merge non-core change"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if commit.returncode == 0:
                    auto_merged = True
                    merge_note = "committed"
                else:
                    merge_note = "git_commit_failed"

    report = {
        "reviewed_at": utc_now(),
        "changed_files": changed_files,
        "non_core_change": non_core_change,
        "checks": checks,
        "risk_decisions": risk_decisions,
        "approved": approved,
        "auto_merge_candidate": auto_merge_candidate,
        "auto_merge_requested": auto_merge,
        "auto_merged": auto_merged,
        "merge_note": merge_note,
    }
    report_file = paths.state_agent_review_dir / "latest.json"
    write_json_atomic(report_file, report)
    append_event(
        paths,
        {
            "event": "a2a_review",
            "approved": approved,
            "non_core_change": non_core_change,
            "auto_merge_candidate": auto_merge_candidate,
            "auto_merged": auto_merged,
            "merge_note": merge_note,
        },
    )

    if json_output:
        safe_print(json.dumps(report, ensure_ascii=False))
    elif not quiet:
        safe_print(f"A2A review report: {report_file}")
        subset = {
            "approved": approved,
            "non_core_change": non_core_change,
            "auto_merge_candidate": auto_merge_candidate,
            "auto_merged": auto_merged,
            "merge_note": merge_note,
            "changed_files": changed_files,
            "checks": checks,
        }
        safe_print(json.dumps(subset, ensure_ascii=False, indent=2))

    return 0 if approved else 1


def cmd_agent_memory_update(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    task_id = ""
    result = ""
    root_cause = ""
    note = ""
    archive_file = ""
    force = False
    quiet = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--task":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --task", stream=sys.stderr)
                return 1
            task_id = args[i + 1]
            i += 2
            continue
        if token == "--result":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --result", stream=sys.stderr)
                return 1
            result = args[i + 1]
            i += 2
            continue
        if token == "--root-cause":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --root-cause", stream=sys.stderr)
                return 1
            root_cause = args[i + 1]
            i += 2
            continue
        if token == "--note":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --note", stream=sys.stderr)
                return 1
            note = args[i + 1]
            i += 2
            continue
        if token == "--archive":
            if i + 1 >= len(args):
                safe_print("Unknown argument: --archive", stream=sys.stderr)
                return 1
            archive_file = args[i + 1]
            i += 2
            continue
        if token == "--force":
            force = True
            i += 1
            continue
        if token == "--quiet":
            quiet = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh auto memory [options]")
            safe_print("")
            safe_print("Options:")
            safe_print("  --task <TASK-ID>          Task id to backfill from archive (optional)")
            safe_print("  --result <pass|fail>      Task result (optional; read from archive if absent)")
            safe_print("  --root-cause <value>      Root cause value (optional; read from archive if absent)")
            safe_print("  --note \"<text>\"           Closure note (optional; read from archive if absent)")
            safe_print("  --archive <file>          Explicit archive file path")
            safe_print("  --force                   Write lesson even when result is pass")
            safe_print("  --quiet                   Suppress non-error output")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    def pick_archive_file() -> str:
        if archive_file and Path(archive_file).is_file():
            return archive_file
        if task_id:
            candidates = sorted(paths.tasks_archive_dir.glob(f"{task_id}-*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            if candidates:
                return str(candidates[0])
        candidates = sorted(paths.tasks_archive_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        return str(candidates[0]) if candidates else ""

    selected_archive = pick_archive_file()
    archive_json: Dict[str, Any] = {}
    if selected_archive and Path(selected_archive).is_file():
        archive_json = read_json_obj(Path(selected_archive))
        if not task_id:
            task_id = str_value(archive_json.get("task_id", ""), "")
        if not result:
            result = str_value(archive_json.get("result", ""), "")
        if not root_cause:
            classification = archive_json.get("classification", {})
            if isinstance(classification, dict):
                root_cause = str_value(classification.get("root_cause", ""), "")
        if not note:
            classification = archive_json.get("classification", {})
            if isinstance(classification, dict):
                note = str_value(classification.get("note", ""), "")

    if not task_id:
        current = read_json_obj(paths.base.current_task_file)
        task_id = str_value(current.get("task_id", ""), "")
    if not result:
        result = "fail"
    if not root_cause:
        root_cause = "unknown"

    if result != "fail" and not force:
        if not quiet:
            safe_print(f"agent memory skipped: result={result} (use --force to override)")
        return 0

    created_at = str_value(archive_json.get("created_at", ""), "")
    gate_rows = parse_jsonl(paths.base.gate_log)
    event_rows = parse_jsonl(paths.base.event_log)

    gate_evidence: List[str] = []
    for row in gate_rows:
        if str_value(row.get("status", ""), "") != "fail":
            continue
        ts = str_value(row.get("ts", ""), "")
        if created_at and ts and ts < created_at:
            continue
        gate = str_value(row.get("gate", ""), "")
        if gate and gate not in gate_evidence:
            gate_evidence.append(gate)
        if len(gate_evidence) >= 5:
            break

    block_evidence: List[str] = []
    for row in event_rows:
        if str_value(row.get("event", ""), "") != "pre_tool_block":
            continue
        ts = str_value(row.get("ts", ""), "")
        if created_at and ts and ts < created_at:
            continue
        reason = str_value(row.get("reason", ""), "")
        if reason and reason not in block_evidence:
            block_evidence.append(reason)
        if len(block_evidence) >= 5:
            break

    if gate_evidence:
        primary_evidence = gate_evidence[0]
    elif block_evidence:
        primary_evidence = block_evidence[0]
    else:
        primary_evidence = "no_direct_signal"

    signature_src = f"{task_id}|{result}|{root_cause}|{note}|{primary_evidence}"
    signature = hashlib.sha256(signature_src.encode("utf-8")).hexdigest()

    if paths.agents_file.exists():
        snapshot_before_mutation(paths, "agent_memory_update", [paths.agents_file], actor="agent-memory")
        agents_text = paths.agents_file.read_text(encoding="utf-8", errors="ignore")
        if "## Learned Guards" not in agents_text:
            with paths.agents_file.open("a", encoding="utf-8") as handle:
                handle.write("\n## Learned Guards\n")
    else:
        paths.agents_file.write_text(
            "# AGENTS.md\n\n"
            "## Global Policy\n"
            "- All code changes must be traceable to spec refs.\n"
            "- Prefer deterministic checks before autonomous retries.\n"
            "- Capture every recurring failure as a durable guard.\n\n"
            "## Learned Guards\n",
            encoding="utf-8",
        )

    agents_text = paths.agents_file.read_text(encoding="utf-8", errors="ignore")
    if f"fingerprint: {signature}" in agents_text:
        if not quiet:
            safe_print(f"agent memory already exists: {signature}")
        return 0

    if root_cause == "spec_missing":
        prevention_lines = [
            "Run /rpi-check discovery, /rpi-check contract, /rpi-spec verify before coding.",
            "Keep M0 Must <= 3 and update Out-of-Scope explicitly.",
        ]
    elif root_cause == "execution_deviation":
        prevention_lines = [
            "Enforce Red test evidence before production edits.",
            "Run /rpi-gates run and inspect failing gate output before close.",
        ]
    elif root_cause == "both":
        prevention_lines = [
            "Fix spec completeness first, then rerun quality gates.",
            "Require traceable spec_refs for every code mutation.",
        ]
    else:
        prevention_lines = [
            "Run /rpi-check doctor and /rpi-observe evals after unexpected failures.",
            "Add a targeted regression check for the observed failure mode.",
        ]

    lesson_id = f"LESSON-{utc_compact_now()}"
    ts = utc_now()
    with paths.agents_file.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(f"### {lesson_id}\n")
        handle.write(f"- fingerprint: {signature}\n")
        handle.write(f"- created_at: {ts}\n")
        handle.write(f"- task_id: {task_id or 'unknown'}\n")
        handle.write(f"- result: {result}\n")
        handle.write(f"- root_cause: {root_cause}\n")
        handle.write(f"- note: {note if note else 'N/A'}\n")
        handle.write(f"- primary_evidence: {primary_evidence}\n")
        handle.write("- prevention:\n")
        for line in prevention_lines:
            handle.write(f"  - {line}\n")
        handle.write("- observed_gate_failures:\n")
        if gate_evidence:
            for line in gate_evidence:
                handle.write(f"  - {line}\n")
        else:
            handle.write("  - none\n")
        handle.write("- observed_blocks:\n")
        if block_evidence:
            for line in block_evidence:
                handle.write(f"  - {line}\n")
        else:
            handle.write("  - none\n")

    state_row = {
        "ts": ts,
        "lesson_id": lesson_id,
        "task_id": task_id,
        "result": result,
        "root_cause": root_cause,
        "note": note,
        "fingerprint": signature,
        "gate_evidence": gate_evidence,
        "block_evidence": block_evidence,
    }
    write_json_atomic(paths.state_agent_memory_dir / "latest.json", state_row)
    append_event(paths, {"event": "agent_memory_update", "task_id": task_id, "root_cause": root_cause, "fingerprint": signature})

    if not quiet:
        safe_print(f"Agent memory updated: {paths.agents_file}")
        safe_print(f"Lesson: {lesson_id}")
    return 0


def cmd_abort_task(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    reason = " ".join(argv).strip()
    if not reason:
        safe_print('Usage: bash .claude/workflow/rpi.sh task abort "<reason>"', stream=sys.stderr)
        return 1

    current = read_json_obj(paths.base.current_task_file)
    task_id = str_value(current.get("task_id", ""), "")
    status = str_value(current.get("status", "idle"), "idle")
    if not task_id or status == "idle":
        safe_print("No active task to abort.")
        return 0

    safe_print(f"Aborting task: {task_id}")
    safe_print(f"Reason: {reason}")
    contract_file = task_flow.write_portable_contract(
        paths.base,
        current,
        transition="aborted",
        reason=reason,
    )
    append_event(
        paths,
        {
            "event": "task_aborted",
            "task_id": task_id,
            "reason": reason,
            "portable_contract": str(contract_file),
        },
    )
    phase = normalize_phase(str_value(read_json_obj(paths.base.phase_file).get("phase", "M0"), "M0"), "M0")
    task_flow.write_idle_task(paths.base, phase)
    safe_print("")
    safe_print("Task aborted successfully.")
    safe_print("Next steps:")
    safe_print("  - Review spec_refs and context before restarting")
    safe_print("  - Run /rpi-task start with correct parameters")
    safe_print("  - Or run /rpi-check doctor to check project state")
    return 0


def cmd_pause_task(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    reason = " ".join(argv).strip()
    if not reason:
        safe_print('Usage: bash .claude/workflow/rpi.sh task pause "<reason>"', stream=sys.stderr)
        return 1

    current = read_json_obj(paths.base.current_task_file)
    task_id = str_value(current.get("task_id", ""), "")
    status = str_value(current.get("status", "idle"), "idle")
    phase = str_value(current.get("phase", ""), "") or normalize_phase(str_value(read_json_obj(paths.base.phase_file).get("phase", "M0"), "M0"), "M0")
    if not task_id or status != "in_progress":
        safe_print("No active in-progress task to pause.")
        return 0

    stack = read_json(paths.base.task_stack_file, [])
    if not isinstance(stack, list):
        stack = []
    entry_id = f"{int(time.time())}-{os.getpid()}-{task_id}"
    paused_at = utc_now()
    spec_refs_raw = current.get("spec_refs", [])
    context_refs_raw = current.get("context_refs", [])
    spec_refs_compact = task_flow.compact_ref_list(spec_refs_raw if isinstance(spec_refs_raw, list) else [], max_items=3)
    context_refs_compact = task_flow.minimal_context_refs(
        spec_refs_compact,
        context_refs_raw if isinstance(context_refs_raw, list) else [],
        max_items=3,
    )
    compact_task = copy.deepcopy(current)
    compact_task["spec_refs"] = spec_refs_compact
    compact_task["context_refs"] = context_refs_compact
    compact_task["last_updated_at"] = paused_at
    stack.append({"entry_id": entry_id, "paused_at": paused_at, "reason": reason, "task": compact_task})
    write_json_atomic(paths.base.task_stack_file, stack)

    stop_state = paths.base.state_dir / "stop_loop_state.json"
    if stop_state.exists():
        stop_state.unlink()
    capsule_file = task_flow.write_task_capsule(paths.base, compact_task, transition="paused", reason=reason)
    contract_file = task_flow.write_portable_contract(paths.base, compact_task, transition="paused", reason=reason)
    task_flow.write_idle_task(paths.base, phase)

    append_event(
        paths,
        {
            "event": "task_paused",
            "task_id": task_id,
            "reason": reason,
            "entry_id": entry_id,
            "task_capsule": str(capsule_file),
            "portable_contract": str(contract_file),
        },
    )
    safe_print(f"Paused task: {task_id}")
    safe_print(f"Reason: {reason}")
    safe_print(f"Resume with: /rpi-task resume {task_id}")
    return 0


def cmd_resume_task(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    target_task_raw = argv[0] if argv else ""
    target_task = task_flow.normalize_task_id(target_task_raw) if target_task_raw else ""

    current = read_json_obj(paths.base.current_task_file)
    current_task_id = str_value(current.get("task_id", ""), "")
    current_status = str_value(current.get("status", "idle"), "idle")
    if current_task_id and current_status == "in_progress":
        safe_print(f"Resume blocked: active task already in progress ({current_task_id}).", stream=sys.stderr)
        safe_print("Close/pause current task first.", stream=sys.stderr)
        return 1

    if not paths.base.task_stack_file.exists():
        safe_print("No paused task stack found.")
        return 1

    stack = read_json(paths.base.task_stack_file, [])
    if not isinstance(stack, list) or not stack:
        safe_print("No paused tasks to resume.")
        return 1

    selected: Optional[Dict[str, Any]] = None
    if target_task:
        for row in reversed(stack):
            if not isinstance(row, dict):
                continue
            task_obj = row.get("task", {})
            if isinstance(task_obj, dict) and str_value(task_obj.get("task_id", ""), "") == target_task:
                selected = row
                break
    else:
        row = stack[-1]
        if isinstance(row, dict):
            selected = row

    if not selected:
        if target_task:
            safe_print(f"No paused entry found for task: {target_task}", stream=sys.stderr)
        else:
            safe_print("No paused task entry found.", stream=sys.stderr)
        return 1

    entry_id = str_value(selected.get("entry_id", ""), "")
    task_json = selected.get("task", {})
    task_id = str_value((task_json or {}).get("task_id", ""), "")
    paused_at = str_value(selected.get("paused_at", ""), "")
    if not entry_id or not isinstance(task_json, dict) or not task_id:
        safe_print("Invalid paused task entry; cannot resume safely.", stream=sys.stderr)
        return 1

    filtered = [row for row in stack if not (isinstance(row, dict) and str_value(row.get("entry_id", ""), "") == entry_id)]
    write_json_atomic(paths.base.task_stack_file, filtered)

    resumed_at = utc_now()
    resumed = copy.deepcopy(task_json)
    resumed["status"] = "in_progress"
    resumed["enforce_stop_gate"] = True
    resumed["last_updated_at"] = resumed_at
    spec_refs_raw = resumed.get("spec_refs", [])
    context_refs_raw = resumed.get("context_refs", [])
    spec_refs_compact = task_flow.compact_ref_list(spec_refs_raw if isinstance(spec_refs_raw, list) else [], max_items=3)
    context_refs_compact = task_flow.minimal_context_refs(
        spec_refs_compact,
        context_refs_raw if isinstance(context_refs_raw, list) else [],
        max_items=3,
    )
    resumed["spec_refs"] = spec_refs_compact
    resumed["context_refs"] = context_refs_compact
    guardrails_obj = resumed.get("guardrails")
    if not isinstance(guardrails_obj, dict):
        guardrails_obj = {}
    precode = guardrails_obj.get("precode")
    if not isinstance(precode, dict):
        precode = {}
    precode.setdefault("status", "unknown")
    precode.setdefault("signature", "")
    precode.setdefault("verified_at", "")
    precode.setdefault("note", "")
    guardrails_obj["precode"] = precode
    resumed["guardrails"] = guardrails_obj
    write_json_atomic(paths.base.current_task_file, resumed)
    contract_file = task_flow.write_portable_contract(paths.base, resumed, transition="resumed")

    stop_state = paths.base.state_dir / "stop_loop_state.json"
    if stop_state.exists():
        stop_state.unlink()

    append_event(
        paths,
        {
            "event": "task_resumed",
            "task_id": task_id,
            "paused_at": paused_at,
            "entry_id": entry_id,
            "portable_contract": str(contract_file),
        },
    )
    safe_print(f"Resumed task: {task_id}")
    if paused_at:
        safe_print(f"Paused at: {paused_at}")
    return 0


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def iter_text_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        yield target
        return
    if target.is_dir():
        for p in target.rglob("*"):
            if p.is_file():
                yield p


def cmd_query_logs(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    task_filter = ""
    event_filter = ""
    phase_filter = ""
    limit = 20
    output_format = "text"

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--task":
            if i + 1 >= len(args):
                safe_print("Unknown option: --task", stream=sys.stderr)
                return 1
            task_filter = args[i + 1]
            i += 2
            continue
        if token == "--event":
            if i + 1 >= len(args):
                safe_print("Unknown option: --event", stream=sys.stderr)
                return 1
            event_filter = args[i + 1]
            i += 2
            continue
        if token == "--phase":
            if i + 1 >= len(args):
                safe_print("Unknown option: --phase", stream=sys.stderr)
                return 1
            phase_filter = args[i + 1]
            i += 2
            continue
        if token == "--limit":
            if i + 1 >= len(args):
                safe_print("Unknown option: --limit", stream=sys.stderr)
                return 1
            limit = int_value(args[i + 1], -1)
            i += 2
            continue
        if token == "--format":
            if i + 1 >= len(args):
                safe_print("Unknown option: --format", stream=sys.stderr)
                return 1
            output_format = args[i + 1]
            i += 2
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe logs [options]")
            safe_print("")
            safe_print("Options:")
            safe_print("  --task <task_id>       Filter by task ID")
            safe_print("  --event <event_type>   Filter by event type")
            safe_print("  --phase <M0|M1|M2>     Filter by phase")
            safe_print("  --limit <n>            Limit output to n entries (default: 20)")
            safe_print("  --format <json|text>   Output format (default: text)")
            return 0
        safe_print(f"Unknown option: {token}", stream=sys.stderr)
        return 1

    if limit < 1:
        safe_print(f"Invalid --limit value: {limit}", stream=sys.stderr)
        return 1
    if output_format not in {"json", "text"}:
        safe_print(f"Invalid --format value: {output_format}", stream=sys.stderr)
        return 1

    if not paths.base.event_log.exists():
        safe_print(f"No event log found at: {paths.base.event_log}")
        return 0

    rows = parse_jsonl(paths.base.event_log)
    rows.reverse()
    matched = 0
    for row in rows:
        if task_filter and str_value(row.get("task_id", ""), "") != task_filter:
            continue
        if event_filter and str_value(row.get("event", ""), "") != event_filter:
            continue
        if phase_filter and str_value(row.get("phase", ""), "") != phase_filter:
            continue
        if output_format == "json":
            safe_print(json.dumps(row, ensure_ascii=False))
        else:
            text = f"[{str_value(row.get('ts', ''), '')}] {str_value(row.get('event', ''), '')}"
            if str_value(row.get("task_id", ""), ""):
                text += f" | task={row['task_id']}"
            if str_value(row.get("phase", ""), ""):
                text += f" | phase={row['phase']}"
            if str_value(row.get("status", ""), ""):
                text += f" | status={row['status']}"
            safe_print(text)
        matched += 1
        if matched >= limit:
            break

    if matched == 0:
        safe_print("No matching events found.")
    return 0


def cmd_recover(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    action = argv[0] if argv else "list"

    if action in {"--help", "-h", "help"}:
        safe_print("Usage:")
        safe_print("  bash .claude/workflow/rpi.sh observe recover list [--target <rel-path>] [--limit <n>] [--json]")
        safe_print("  bash .claude/workflow/rpi.sh observe recover restore <rel-path> [--snapshot <snapshot-ref>] [--reason <text>] [--dry-run]")
        return 0

    if action == "list":
        target = ""
        limit = 30
        json_output = False
        i = 1
        args = list(argv)
        while i < len(args):
            token = args[i]
            if token == "--target":
                if i + 1 >= len(args):
                    safe_print("Unknown option: --target", stream=sys.stderr)
                    return 1
                target = args[i + 1]
                i += 2
                continue
            if token == "--limit":
                if i + 1 >= len(args):
                    safe_print("Unknown option: --limit", stream=sys.stderr)
                    return 1
                limit = int_value(args[i + 1], -1)
                i += 2
                continue
            if token == "--json":
                json_output = True
                i += 1
                continue
            safe_print(f"Unknown option: {token}", stream=sys.stderr)
            return 1

        if limit < 1:
            safe_print(f"Invalid --limit value: {limit}", stream=sys.stderr)
            return 1

        rows = artifact_recovery.list_snapshot_rows(paths.base.project_dir, target=target, limit=limit)
        if json_output:
            safe_print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            safe_print("No recovery snapshots found.")
            return 0
        safe_print(f"Recovery snapshots ({len(rows)}):")
        for row in rows:
            safe_print(
                f"- [{str_value(row.get('ts', ''), '')}] target={str_value(row.get('target', ''), '')} "
                f"id={str_value(row.get('id', ''), '')} reason={str_value(row.get('reason', ''), '')}"
            )
            safe_print(f"  snapshot={str_value(row.get('snapshot', ''), '')}")
        return 0

    if action == "restore":
        if len(argv) < 2:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe recover restore <rel-path> [--snapshot <snapshot-ref>] [--reason <text>] [--dry-run]", stream=sys.stderr)
            return 1
        target = argv[1]
        snapshot_ref = ""
        reason = "manual_restore"
        dry_run = False
        i = 2
        args = list(argv)
        while i < len(args):
            token = args[i]
            if token == "--snapshot":
                if i + 1 >= len(args):
                    safe_print("Unknown option: --snapshot", stream=sys.stderr)
                    return 1
                snapshot_ref = args[i + 1]
                i += 2
                continue
            if token == "--reason":
                if i + 1 >= len(args):
                    safe_print("Unknown option: --reason", stream=sys.stderr)
                    return 1
                reason = args[i + 1]
                i += 2
                continue
            if token == "--dry-run":
                dry_run = True
                i += 1
                continue
            safe_print(f"Unknown option: {token}", stream=sys.stderr)
            return 1

        row = artifact_recovery.find_snapshot_row(paths.base.project_dir, target=target, snapshot_ref=snapshot_ref)
        if not row:
            safe_print(f"No snapshot found for target: {target}", stream=sys.stderr)
            return 1
        if dry_run:
            safe_print(json.dumps(row, ensure_ascii=False, indent=2))
            return 0

        result = artifact_recovery.restore_snapshot(
            project_dir=paths.base.project_dir,
            target=target,
            snapshot_ref=snapshot_ref,
            reason=reason,
            actor="recover",
        )
        append_event(
            paths,
            {
                "event": "artifact_restore",
                "target": str_value(result.get("target", ""), ""),
                "snapshot": str_value(result.get("snapshot", ""), ""),
                "reason": reason,
            },
        )
        safe_print("Artifact restored:")
        safe_print(f"- target: {str_value(result.get('target', ''), '')}")
        safe_print(f"- snapshot: {str_value(result.get('snapshot', ''), '')}")
        if str_value(result.get("pre_restore_snapshot", ""), ""):
            safe_print(f"- pre_restore_snapshot: {str_value(result.get('pre_restore_snapshot', ''), '')}")
        return 0

    safe_print(f"Unknown recover action: {action}", stream=sys.stderr)
    return 1


def cmd_trace_grade(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    quiet = False
    for token in argv:
        if token == "--quiet":
            quiet = True
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh observe trace [--quiet]")
            safe_print("")
            safe_print("Grade execution trace quality from events and gate logs.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    if not paths.base.event_log.exists() or not paths.base.gate_log.exists():
        safe_print("Missing logs for grading", stream=sys.stderr)
        return 1

    event_rows = parse_jsonl(paths.base.event_log)
    gate_rows = parse_jsonl(paths.base.gate_log)

    pre_blocks = len([r for r in event_rows if str_value(r.get("event", ""), "") == "pre_tool_block"])
    stop_blocks = len([r for r in event_rows if str_value(r.get("event", ""), "") == "stop_block"])
    post_tools = len([r for r in event_rows if str_value(r.get("event", ""), "") == "post_tool_use"])
    gate_failures = len([r for r in gate_rows if str_value(r.get("status", ""), "") == "fail"])
    gate_total = len(gate_rows)
    quality_events = len([r for r in event_rows if str_value(r.get("event", ""), "") == "quality_gate"])

    score = 100
    score -= pre_blocks * 2
    score -= stop_blocks * 3
    score -= gate_failures * 5
    if post_tools == 0:
        score -= 10
    if quality_events == 0:
        score -= 10
    if score < 0:
        score = 0

    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    else:
        grade = "D"

    result = {
        "ts": utc_now(),
        "grade": grade,
        "score": score,
        "metrics": {
            "pre_tool_blocks": pre_blocks,
            "stop_blocks": stop_blocks,
            "post_tool_events": post_tools,
            "gate_failures": gate_failures,
            "gate_total": gate_total,
            "quality_events": quality_events,
        },
    }
    append_jsonl(paths.base.log_dir / "trace-grades.jsonl", result)
    append_event(paths, {"event": "trace_grade", "grade": grade, "score": score})

    if not quiet:
        safe_print(f"Trace grade: {grade} (score={score})")
        safe_print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_check_entry_integrity(paths: Paths, argv: Sequence[str]) -> int:
    quiet = False
    for token in argv:
        if token == "--quiet":
            quiet = True
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh check entry [--quiet]")
            safe_print("")
            safe_print("Checks whether micro-kernel entry paths referenced in docs/config/commands exist and are executable.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    legacy_scripts_dir = paths.base.project_dir / ".claude" / "workflow" / "scripts"
    legacy_scripts_dir_exists = legacy_scripts_dir.exists()

    scan_targets = [
        paths.base.project_dir / "README.md",
        paths.base.project_dir / ".claude" / "settings.json",
        paths.base.project_dir / ".claude" / "commands",
        paths.base.project_dir / ".claude" / "workflow" / "config" / "gates.json",
    ]
    pattern = re.compile(r"(\.claude/hooks/[A-Za-z0-9_.-]+\.sh|\.claude/workflow/rpi\.sh)")
    refs: List[str] = []
    for target in scan_targets:
        for f in iter_text_files(target):
            refs.extend(pattern.findall(read_text_safe(f)))
    refs = sorted(set([x[0] if isinstance(x, tuple) else x for x in refs]))

    missing_paths: List[str] = []
    not_executable: List[str] = []
    for rel in refs:
        p = paths.base.project_dir / rel
        if not p.is_file():
            missing_paths.append(rel)
            continue
        if not os.access(p, os.X_OK):
            not_executable.append(rel)

    claude_md_violations: List[str] = []
    for f in paths.base.project_dir.rglob("*"):
        if not f.is_file():
            continue
        name = f.name.lower()
        if name != "claude.md":
            continue
        rel = str(f.relative_to(paths.base.project_dir)).replace("\\", "/")
        if rel != "CLAUDE.md":
            claude_md_violations.append(rel)
    if not (paths.base.project_dir / "CLAUDE.md").is_file():
        claude_md_violations.append("missing_root_CLAUDE.md")

    legacy_ref_scan_targets = [
        paths.base.project_dir / "README.md",
        paths.base.project_dir / "QUICKSTART.md",
        paths.base.project_dir / "prd.md",
        paths.base.project_dir / ".claude" / "settings.json",
        paths.base.project_dir / ".claude" / "commands",
        paths.base.project_dir / ".claude" / "skills",
        paths.base.project_dir / ".claude" / "workflow" / "config",
    ]
    legacy_ref_files: List[str] = []
    legacy_ref_ignore = {
        ".claude/workflow/config/evals.json",
    }
    legacy_ref_rx = re.compile(r"\.claude/workflow/scripts/")
    for target in legacy_ref_scan_targets:
        for f in iter_text_files(target):
            rel = str(f.relative_to(paths.base.project_dir)).replace("\\", "/")
            if rel in legacy_ref_ignore:
                continue
            if legacy_ref_rx.search(read_text_safe(f)):
                legacy_ref_files.append(rel)
    legacy_ref_files = sorted(set(legacy_ref_files))

    if not missing_paths and not claude_md_violations and not legacy_scripts_dir_exists and not legacy_ref_files:
        if not quiet:
            safe_print("entry integrity check passed")
            if not_executable:
                safe_print("note: some scripts are not executable, but non-blocking because runtime uses 'bash <script>'.", stream=sys.stderr)
        return 0

    if not quiet:
        safe_print("entry integrity check failed:", stream=sys.stderr)
        if missing_paths:
            safe_print("- missing script files:", stream=sys.stderr)
            for p in missing_paths:
                safe_print(f"  - {p}", stream=sys.stderr)
        if not_executable:
            safe_print("- not executable (non-blocking in this framework):", stream=sys.stderr)
            for p in not_executable:
                safe_print(f"  - {p}", stream=sys.stderr)
        if claude_md_violations:
            safe_print("- CLAUDE.md single-entry violation:", stream=sys.stderr)
            for p in claude_md_violations:
                safe_print(f"  - {p}", stream=sys.stderr)
            safe_print("  expected: only ./CLAUDE.md", stream=sys.stderr)
        if legacy_scripts_dir_exists:
            safe_print("- legacy script runtime directory still exists (must be removed):", stream=sys.stderr)
            safe_print("  - .claude/workflow/scripts", stream=sys.stderr)
        if legacy_ref_files:
            safe_print("- legacy script path references detected:", stream=sys.stderr)
            for rel in legacy_ref_files[:20]:
                safe_print(f"  - {rel}", stream=sys.stderr)
            if len(legacy_ref_files) > 20:
                safe_print(f"  - ... and {len(legacy_ref_files) - 20} more", stream=sys.stderr)
    return 1


def cmd_check_theory(paths: Paths, argv: Sequence[str]) -> int:
    quiet = False
    for token in argv:
        if token == "--quiet":
            quiet = True
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh check theory [--quiet]")
            safe_print("")
            safe_print("Checks whether the project still aligns with the Vibe-Spec + RPI theory baseline.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    failures: List[str] = []
    project = paths.base.project_dir

    readme = project / "README.md"
    if not readme.is_file():
        failures.append("missing README.md")
    elif not re.search(r"Vibe-Spec.*RPI|Vibe:Spec", read_text_safe(readme), flags=re.IGNORECASE):
        failures.append("README.md missing explicit Vibe-Spec + RPI theory baseline")

    claude_md = project / "CLAUDE.md"
    if not claude_md.is_file():
        failures.append("missing root CLAUDE.md")
    elif not re.search(r"Requirement.*Plan.*Implement", read_text_safe(claude_md), flags=re.IGNORECASE | re.DOTALL):
        failures.append("CLAUDE.md missing RPI fixed flow definition")

    settings_file = project / ".claude" / "settings.json"
    if not settings_file.is_file():
        failures.append("missing .claude/settings.json")
    else:
        settings = read_json_obj(settings_file)
        hook_cmds: List[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                cmd = value.get("command")
                if isinstance(cmd, str):
                    hook_cmds.append(cmd)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        hooks = settings.get("hooks", {})
        walk(hooks)
        if not hook_cmds:
            failures.append("settings.json has no hook commands")
        else:
            prefix = re.compile(r"^bash\s+\.claude/workflow/rpi\.sh\s+hook-")
            bad = [c for c in hook_cmds if not prefix.search(c)]
            if bad:
                failures.append(f"hook command not routed through micro-kernel: {bad[0]}")

    def dir_contains_pattern(dir_path: Path, pattern: str) -> bool:
        rx = re.compile(pattern)
        for f in iter_text_files(dir_path):
            if rx.search(read_text_safe(f)):
                return True
        return False

    if dir_contains_pattern(project / ".claude" / "commands", r"\.claude/workflow/scripts/") or re.search(
        r"\.claude/workflow/scripts/",
        read_text_safe(project / "README.md"),
    ):
        failures.append("user-facing docs still reference .claude/workflow/scripts/* directly")

    if dir_contains_pattern(project / ".claude" / "skills", r"\.claude/workflow/scripts/"):
        failures.append("skills still reference .claude/workflow/scripts/* directly")

    required_rules = [
        ".claude/rules/00-foundation.md",
        ".claude/rules/01-spec-layering.md",
        ".claude/rules/02-rpi-traceability.md",
        ".claude/rules/03-tdd-quality-gates.md",
        ".claude/rules/04-context-pack-injection.md",
        ".claude/rules/05-discovery-first.md",
    ]
    for rel in required_rules:
        if not (project / rel).is_file():
            failures.append(f"missing rule file: {rel}")

    required_commands = [
        ".claude/commands/rpi-init.md",
        ".claude/commands/rpi-task.md",
        ".claude/commands/rpi-check.md",
        ".claude/commands/rpi-spec.md",
        ".claude/commands/rpi-gates.md",
        ".claude/commands/rpi-mode.md",
        ".claude/commands/rpi-observe.md",
        ".claude/commands/rpi-auto.md",
    ]
    for rel in required_commands:
        if not (project / rel).is_file():
            failures.append(f"missing command file: {rel}")

    commands_dir = project / ".claude" / "commands"
    expected_names = {Path(rel).name for rel in required_commands}
    actual_names = {p.name for p in commands_dir.glob("rpi-*.md")} if commands_dir.is_dir() else set()
    extra_names = sorted(actual_names - expected_names)
    if extra_names:
        failures.append(f"legacy command docs still present: {', '.join(extra_names)}")

    check_cmd = project / ".claude" / "commands" / "rpi-check.md"
    if check_cmd.is_file():
        check_text = read_text_safe(check_cmd)
        if "theory" not in check_text:
            failures.append("rpi-check command doc missing theory action")

    gates_cmd = project / ".claude" / "commands" / "rpi-gates.md"
    if gates_cmd.is_file():
        gates_text = read_text_safe(gates_cmd)
        if "preview" not in gates_text:
            failures.append("rpi-gates command doc missing preview action")

    ups = project / ".claude" / "workflow" / "engine" / "user_prompt_submit_core.py"
    if ups.is_file():
        ups_text = read_text_safe(ups)
        if "Vibe:Spec" not in ups_text:
            failures.append("user_prompt_submit core missing Vibe:Spec injection")
        if "Follow RPI: Requirement -> Plan -> Implement" not in ups_text:
            failures.append("user_prompt_submit core missing explicit RPI flow injection")
        if "auto_inject_linkage_context" not in ups_text:
            failures.append("user_prompt_submit core missing auto_inject_linkage_context handling")
    else:
        failures.append("missing .claude/workflow/engine/user_prompt_submit_core.py")

    ptu = project / ".claude" / "workflow" / "engine" / "pre_tool_use_core.py"
    if ptu.is_file():
        ptu_text = read_text_safe(ptu)
        if "require_linkage_spec" not in ptu_text:
            failures.append("pre_tool_use core missing require_linkage_spec enforcement")
        if "linkage_strict_mode" not in ptu_text:
            failures.append("pre_tool_use core missing linkage_strict_mode enforcement")
    else:
        failures.append("missing .claude/workflow/engine/pre_tool_use_core.py")

    metadata_pattern = re.compile(r"最后更新|维护者|Maintainer|Last Updated|Updated on")
    if any(metadata_pattern.search(read_text_safe(f)) for f in iter_text_files(project / ".rpi-blueprint" / "specs")) or any(
        metadata_pattern.search(read_text_safe(f)) for f in iter_text_files(project / ".claude" / "skills")
    ):
        failures.append("template/skill files still contain maintainer or last-updated metadata")

    smart_quote_pattern = re.compile(r"[“”‘’]")
    if any(smart_quote_pattern.search(read_text_safe(f)) for f in iter_text_files(project / ".claude" / "commands")):
        failures.append("command docs contain smart quotes; replace with ASCII quotes for copy-paste safety")

    gates_file = project / ".claude" / "workflow" / "config" / "gates.json"
    if gates_file.is_file():
        gates = read_json_obj(gates_file)
        verify_default = ((gates.get("verify") or {}).get("default") or [])
        if not isinstance(verify_default, list):
            verify_default = []

        def has_entry(name: str, cmd_pattern: str) -> bool:
            for item in verify_default:
                if not isinstance(item, dict):
                    continue
                if str_value(item.get("name", ""), "") != name:
                    continue
                command = str_value(item.get("command", ""), "")
                if re.search(cmd_pattern, command):
                    return True
            return False

        if not has_entry("discovery_complete", r"^bash \.claude/workflow/rpi\.sh check discovery( |$)"):
            failures.append("gates.verify.default missing discovery_complete routed to rpi.sh check discovery")
        if not has_entry("contract_spec_complete", r"^bash \.claude/workflow/rpi\.sh check contract( |$)"):
            failures.append("gates.verify.default missing contract_spec_complete routed to rpi.sh check contract")
        if not has_entry("scope_guard_passed", r"^bash \.claude/workflow/rpi\.sh check scope( |$)"):
            failures.append("gates.verify.default missing scope_guard_passed routed to rpi.sh check scope")

    if not failures:
        if not quiet:
            safe_print("theory drift check passed (Vibe-Spec + RPI baseline intact)")
        return 0

    if not quiet:
        safe_print("theory drift check failed:", stream=sys.stderr)
        for item in failures:
            safe_print(f"- {item}", stream=sys.stderr)
    return 1


def cmd_check_skeleton(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    quiet = False
    for token in argv:
        if token == "--quiet":
            quiet = True
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh check skeleton [--quiet]")
            safe_print("")
            safe_print("Check global multi-module skeleton completeness.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    l0 = paths.spec_l0_dir
    module_linkage = l0 / "module-linkage.md"
    ux_spec = l0 / "ux-spec.md"
    ux_flow = l0 / "ux-flow.md"
    reference_module = l0 / "reference-module.md"
    runtime = load_runtime(paths)

    issues: List[str] = []
    if not module_linkage.is_file():
        issues.append("missing: .rpi-outfile/specs/l0/module-linkage.md")
    else:
        text = read_text_safe(module_linkage)
        for key in ["模块联动关系", "数据流向", "技术实现标准"]:
            if key not in text:
                issues.append(f"module-linkage.md missing section: {key}")

    need_ux_flow = bool_value(runtime.get("require_ux_spec", False), False) or ux_spec.is_file()
    if need_ux_flow and not ux_flow.is_file():
        issues.append("missing: .rpi-outfile/specs/l0/ux-flow.md (frontend skeleton)")

    if bool_value(runtime.get("require_reference_module", False), False) and not reference_module.is_file():
        issues.append("missing: .rpi-outfile/specs/l0/reference-module.md (required by runtime)")

    if issues:
        if not quiet:
            safe_print("skeleton check failed:")
            for item in issues:
                safe_print(f"- {item}")
        return 1
    if not quiet:
        safe_print("skeleton check passed")
    return 0


def cmd_skeleton_init(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    frontend_mode = "auto"
    force = False

    i = 0
    args = list(argv)
    while i < len(args):
        token = args[i]
        if token == "--frontend":
            frontend_mode = "on"
            i += 1
            continue
        if token == "--no-frontend":
            frontend_mode = "off"
            i += 1
            continue
        if token == "--force":
            force = True
            i += 1
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh check skeleton-init [--frontend|--no-frontend] [--force]")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    tpl_dir = paths.base.project_dir / ".rpi-blueprint" / "specs" / "l0"
    l0 = paths.spec_l0_dir
    l0.mkdir(parents=True, exist_ok=True)
    created: List[str] = []
    skipped: List[str] = []
    missing_tpl: List[str] = []

    def copy_from_template(src_rel: str, dst_rel: str) -> None:
        src = tpl_dir / src_rel
        dst = l0 / dst_rel
        if not src.is_file():
            missing_tpl.append(f".rpi-blueprint/specs/l0/{src_rel}")
            return
        if not force and dst.is_file():
            skipped.append(f".rpi-outfile/specs/l0/{dst_rel}")
            return
        if dst.is_file():
            snapshot_before_mutation(paths, f"skeleton_init:{dst_rel}", [dst], actor="skeleton-init")
        dst.write_bytes(src.read_bytes())
        created.append(f".rpi-outfile/specs/l0/{dst_rel}")

    copy_from_template("module-linkage.template.md", "module-linkage.md")
    copy_from_template("reference-module.template.md", "reference-module.md")

    runtime = load_runtime(paths)
    need_frontend = False
    if frontend_mode == "on":
        need_frontend = True
    elif frontend_mode == "auto":
        need_frontend = bool_value(runtime.get("require_ux_spec", False), False) or (l0 / "ux-spec.md").is_file()

    if need_frontend:
        ux_flow = l0 / "ux-flow.md"
        if force or not ux_flow.is_file():
            if ux_flow.is_file():
                snapshot_before_mutation(paths, "skeleton_init:ux-flow.md", [ux_flow], actor="skeleton-init")
            ux_flow.write_text(
                "# 全局 UX 业务流转规范\n\n"
                "## 核心业务流\n"
                "1. 用户进入主流程页面\n"
                "2. 完成关键表单输入并提交\n"
                "3. 查看结果反馈并可回退\n\n"
                "## 页面流转约束\n"
                "- 禁止同级表单块（优先 Modal/Drawer）\n"
                "- 删除动作必须二次确认\n"
                "- 提交按钮必须提供 loading 状态\n\n"
                "## 跨模块流转\n"
                "- 在 module-linkage.md 中定义触发关系\n"
                "- 本文件描述页面级交互路径与异常回退\n",
                encoding="utf-8",
            )
            created.append(".rpi-outfile/specs/l0/ux-flow.md")
        else:
            skipped.append(".rpi-outfile/specs/l0/ux-flow.md")

    append_event(
        paths,
        {
            "event": "skeleton_init",
            "created": created,
            "skipped": skipped,
            "missing_templates": missing_tpl,
        },
    )
    safe_print("skeleton init finished")
    if created:
        safe_print("created:")
        for item in created:
            safe_print(f"- {item}")
    if skipped:
        safe_print("skipped (already exists):")
        for item in skipped:
            safe_print(f"- {item}")
    if missing_tpl:
        safe_print("missing templates:")
        for item in missing_tpl:
            safe_print(f"- {item}")
        return 1
    return 0


def cmd_evaluate_requirement(paths: Paths, argv: Sequence[str]) -> int:
    _ = paths
    if not argv:
        safe_print('Usage: bash .claude/workflow/rpi.sh init setup "<idea>" [platform]', stream=sys.stderr)
        return 1

    text = " ".join(argv)
    text_trimmed = text.strip()
    text_len = len(text_trimmed)
    status = "accepted"
    reasons: List[str] = []

    if text_len < 8:
        status = "clarify"
        reasons.append("Requirement is too short to infer target users and MVP scope.")

    if re.search(r"^\s*(优化一下|做一下|完善一下|弄一下|看着办|随便做)\s*$", text_trimmed, flags=re.IGNORECASE):
        status = "rejected"
        reasons.append("Requirement is too vague and has no executable outcome.")

    if re.search(r"(绕过|绕过权限|后门|窃取|提权|删除全部|破坏|刷量)", text_trimmed, flags=re.IGNORECASE):
        status = "rejected"
        reasons.append("Requirement appears unsafe or policy-risky.")

    punct_count = len(re.findall(r"[,，、;；/|]", text_trimmed))
    conj_count = len(re.findall(r"(并且|同时|另外|以及|再加上|此外)", text_trimmed))
    sep_count = punct_count + conj_count
    if sep_count > 20:
        if status == "accepted":
            status = "clarify"
        reasons.append("Requirement may contain too many bundled features; split to MVP first.")

    result = {"status": status, "requirement": text_trimmed, "reasons": reasons}
    safe_print(json.dumps(result, ensure_ascii=False))
    return 0


def detect_project_surface(idea: str, platform: str, project_type: str = "") -> Tuple[bool, bool]:
    """Return (is_frontend, is_headless_cli_like)."""
    platform_text = (platform or "").strip()
    project_type_text = (project_type or "").strip()
    text = " ".join([idea or "", platform_text, project_type_text]).strip()

    if platform_text:
        if re.search(
            r"(front[\s-]?end|frontend|前端|web|h5|页面|界面|ui|ux|客户端|小程序|android|ios|flutter|react\s*native|electron|浏览器|桌面端|移动端|app|应用)",
            platform_text,
            flags=re.IGNORECASE,
        ):
            return True, False
        if re.search(
            r"(cli|terminal|cmd|command|命令行|终端|脚本|headless|无界面|daemon|worker|batch|api|service|backend|后端|服务端)",
            platform_text,
            flags=re.IGNORECASE,
        ):
            return False, True

    frontend_pattern = re.compile(
        r"(front[\s-]?end|frontend|前端|web|h5|页面|界面|ui|ux|客户端|小程序|android|ios|flutter|react\s*native|electron|浏览器|桌面端|移动端|app|应用)",
        flags=re.IGNORECASE,
    )
    headless_pattern = re.compile(
        r"(cli|terminal|cmd|command|命令行|终端|脚本|headless|无界面|daemon|worker|batch|api|service|backend|后端|服务端)",
        flags=re.IGNORECASE,
    )

    if "前端" in project_type_text:
        return True, False
    if re.search(r"(终端|命令行|CLI|无界面|后端)", project_type_text, flags=re.IGNORECASE):
        return False, True

    has_frontend_signal = bool(frontend_pattern.search(text))
    has_headless_signal = bool(headless_pattern.search(text))

    if has_frontend_signal:
        return True, False
    if has_headless_signal:
        return False, True

    if re.search(r"(应用|app|系统|平台|门户)", idea, flags=re.IGNORECASE):
        return True, False

    return False, False


MVP_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def infer_business_profile(idea: str) -> Dict[str, Any]:
    text = (idea or "").strip()
    if re.search(r"(网课|课程|学习|视频|音频|vip|会员|激活码|直播|课时|章节)", text, flags=re.IGNORECASE):
        return {
            "domain": "在线课程",
            "actor": "学员",
            "core_object": "课程学习记录",
            "main_flow": "学员登录后浏览课程并完成短视频/音频播放，系统记录学习轨迹",
            "exception_flow": "激活码无效、权益不足或播放异常时拒绝并返回可解释反馈",
            "governance_flow": "VIP 权益状态变更后访问权限即时生效并写入审计记录",
            "extension_flow": "学习推荐、社区互动与活动运营",
            "term_example": "学习轨迹",
            "term_definition": "用于回放学习进度、行为与权益校验结果的可追溯记录",
            "tech_stack": "Next.js + Node.js + SQLite（M0）+ PostgreSQL 迁移跑道（M1/M2）",
            "segments": {
                "S0": "MVP运营段：课程播放、订阅收藏、学习历史、VIP激活码权益闭环，可正常运营",
                "S1": "成长期段：播放体验优化、学习进度增强、搜索推荐等迭代优化或新增1条业务",
                "S2": "成熟期段：高并发播放稳定性、性能容量治理、更多场景覆盖与部分生态完善",
                "S3": "生态持续进化段：社区化/商业化扩展、壁垒能力建设、新业务线探索",
            },
            "abc": {
                "A": "A = 选择 S0（先把核心需求业务线做成可运营产品）",
                "B": "B = 选择 S0 + S1（在可运营基础上进入成长迭代）",
                "C": "C = 选择 S0 + S1 + S2（进入成熟规模化，S3进入路线图）",
            },
            "phase_strategy": {
                "M0": "围绕已选业务段交付可运营闭环，不做演示版半成品",
                "M1": "成长期迭代：优化主链路体验，或新增1条受控业务方向",
                "M2": "成熟期扩展：规模化能力、性能与稳定性、生态能力试点",
            },
            "must_map": {
                "A": ["L1", "L2", "L3"],
                "B": ["L1", "L2", "L3"],
                "C": ["L1", "L2", "L3"],
            },
            "wont_map": {
                "A": ["L4", "第三方支付", "社区互动"],
                "B": ["L4", "跨区域容灾", "复杂商业化能力"],
                "C": ["S3 全量生态化", "跨平台多端统一运营", "实验性新业务线"],
            },
        }
    if "用户" in text:
        return {
            "domain": "用户管理",
            "actor": "业务管理员",
            "core_object": "用户档案",
            "main_flow": "管理员登录后创建与维护用户档案，并分配基础角色",
            "exception_flow": "鉴权失败或账号冲突时拒绝请求并返回可解释原因",
            "governance_flow": "角色变更后权限即时生效并写入审计轨迹",
            "extension_flow": "用户统计报表与运营分析",
            "term_example": "用户档案",
            "term_definition": "用于统一管理身份、属性与权限绑定的业务实体",
            "tech_stack": "Web + REST API + 关系型存储",
            "segments": {
                "S0": "MVP运营段：用户创建维护、鉴权与基础权限可运营",
                "S1": "成长期段：体验优化、搜索筛选、批量处理等迭代",
                "S2": "成熟期段：规模化与稳定性治理、更多场景支持",
                "S3": "生态持续进化段：运营平台化、生态集成与扩展业务线",
            },
            "abc": {
                "A": "A = S0",
                "B": "B = S0 + S1",
                "C": "C = S0 + S1 + S2（S3 路线图）",
            },
            "phase_strategy": {
                "M0": "核心业务可运营闭环",
                "M1": "优化主链路并受控新增能力",
                "M2": "规模化与稳定性增强，生态能力试点",
            },
        }
    if "订单" in text:
        return {
            "domain": "订单管理",
            "actor": "运营专员",
            "core_object": "订单记录",
            "main_flow": "运营专员创建并更新订单记录，完成状态流转",
            "exception_flow": "库存不足或状态冲突时拒绝并回传原因",
            "governance_flow": "关键状态变更同步触发审计与权限校验",
            "extension_flow": "订单报表与渠道分析",
            "term_example": "订单记录",
            "term_definition": "用于追踪交易生命周期与履约状态的核心实体",
            "tech_stack": "Web + REST API + 消息通知",
            "segments": {
                "S0": "MVP运营段：下单、状态流转、异常回执可运营",
                "S1": "成长期段：履约优化、风控增强、查询体验迭代",
                "S2": "成熟期段：高并发交易稳定性与性能治理",
                "S3": "生态持续进化段：供应链协同与商业化扩展",
            },
            "abc": {
                "A": "A = S0",
                "B": "B = S0 + S1",
                "C": "C = S0 + S1 + S2（S3 路线图）",
            },
            "phase_strategy": {
                "M0": "核心交易闭环可运营",
                "M1": "履约与体验优化，受控新增能力",
                "M2": "规模化稳定性与生态扩展试点",
            },
        }
    return {
        "domain": "核心业务",
        "actor": "业务操作员",
        "core_object": "核心记录",
        "main_flow": "业务操作员登录后创建并维护核心记录",
        "exception_flow": "参数非法或冲突时拒绝并返回可解释原因",
        "governance_flow": "状态变更需要权限校验并保留审计轨迹",
        "extension_flow": "统计报表与外部集成",
        "term_example": "核心记录",
        "term_definition": "承载主流程输入、状态与结果的最小可追踪业务实体",
        "tech_stack": "Web + API + 持久化存储",
        "segments": {
            "S0": "MVP运营段：核心需求业务线关键功能可用且可运营",
            "S1": "成长期段：迭代优化与受控新增业务方向",
            "S2": "成熟期段：规模化功能、性能稳定性与更多场景覆盖",
            "S3": "生态持续进化段：壁垒能力、新方向探索与新业务线",
        },
        "abc": {
            "A": "A = S0",
            "B": "B = S0 + S1",
            "C": "C = S0 + S1 + S2（S3 路线图）",
        },
        "phase_strategy": {
            "M0": "核心业务闭环可运营",
            "M1": "成长迭代优化或新增受控业务",
            "M2": "成熟规模化与生态能力试点",
        },
    }


def build_mvp_placeholder_replacements(
    profile: Dict[str, Any],
    cov_a: int,
    cov_b: int,
    cov_c: int,
    is_frontend: bool,
    is_headless_cli: bool,
) -> Dict[str, str]:
    actor = profile.get("actor", "业务操作员")
    core_object = profile.get("core_object", "核心记录")
    main_flow = profile.get("main_flow", "完成主链路业务动作")
    exception_flow = profile.get("exception_flow", "异常场景可解释拒绝")
    governance_flow = profile.get("governance_flow", "权限与审计治理生效")
    extension_flow = profile.get("extension_flow", "扩展运营能力")
    term_example = profile.get("term_example", core_object)
    term_definition = profile.get("term_definition", "关键业务实体")
    tech_stack = profile.get("tech_stack", "Web + API + 存储")

    return {
        "角色、对象、触发条件": f"{actor}、{core_object}、登录后触发业务动作",
        "可进入业务流程": f"{actor}可进入{profile.get('domain', '业务')}主流程",
        "请求、规则、上下文": f"{core_object}请求、鉴权规则、会话上下文",
        "决策结果": "允许执行 / 拒绝执行（含原因）",
        "决策结果、执行参数": f"决策结果、{core_object}字段参数",
        "业务结果已落地": f"{core_object}成功落库并可回查",
        "执行结果、审计信息": "执行结果、操作者、时间戳、变更摘要",
        "可复用输出/反馈/追溯": "统一回执、可检索日志、可回放审计链路",
        "主链路：高频核心业务流程": f"{main_flow}",
        "关键异常链路：失败/拒绝/回滚流程": f"{exception_flow}",
        "治理链路：权限/状态/一致性变更": f"{governance_flow}",
        "扩展链路：运营/报表/外部集成": f"{extension_flow}",
        "术语": f"{term_example}",
        "统一定义（避免同词多义）": f"{term_definition}",
        "核心业务上下文": f"{profile.get('domain', '业务')}主流程上下文",
        "支撑协作上下文": f"{core_object}管理与查询上下文",
        "治理/审计上下文（方向 C 建议）": "审计追踪与权限治理上下文",
        "业务规则，任何实现都不能违反": "未鉴权请求不得创建/修改核心记录",
        f"P0 覆盖率 >= {cov_a}%，至少 1 条主链路 + 1 条关键异常链路": f"P0 覆盖率 >= {cov_a}%，主链路 L1 + 异常链路 L2",
        "至少 1 个 Core 上下文（示例：C1 [Core]）": "C1 [Core]",
        "可选提升 1 项非核心能力；需同步降权 1 项并给出理由": "可提升 1 项非核心能力并同步降权 1 项，记录取舍理由",
        "选定链路 IDs（示例：L1,L2）": "L1, L2",
        "未入选链路 + 非核心扩展能力": "L3, L4, 低优先级扩展能力",
        "最小可验证实现方案": tech_stack,
        f"P0 覆盖率 >= {cov_b}%，主路径链路可用且可复测": f"P0 覆盖率 >= {cov_b}%，链路 L1/L2/L3 可复测",
        "Core + 至少 1 个 Supporting（示例：C1,C2）": "C1 [Core] + C2 [Supporting]",
        "选定链路 IDs（示例：L1,L2,L3）": "L1, L2, L3",
        "运营深水区、重型扩展、低频场景": "L4 与低频扩展场景",
        "成熟稳定的主流方案": tech_stack,
        f"P0 覆盖率 = {cov_c}%，并补齐运营治理链路": f"P0 覆盖率 = {cov_c}%，补齐治理链路 G1",
        "所有 P0 上下文 + 治理上下文（示例：C1,C2,C3[Governance]）": "C1 + C2 + C3 [Governance]",
        "允许调权但不降低治理能力要求": "允许调权，但审计与治理能力必须保留",
        "方向 B 链路 + 运营治理链路 IDs（监控/审计/恢复）": "L1, L2, L3, G1",
        "超大规模优化、复杂中间件引入": "跨区域容灾与复杂中间件二期引入",
        "方向 B 技术栈 + 运维治理工具链": f"{tech_stack} + 日志指标告警链路",
        "非核心功能提升原因（用户价值/业务时机/风险收益）": "首轮试点用户需要快速看到统计价值",
        "被降权项及影响说明": "将治理细分能力下放至 M1，M0 保留基础校验",
        "例如 80%": "85%",
        "Lx：用户进入关键页面并完成已选主链路操作（S1→S4）": f"L1：{main_flow}",
        "Ly：系统处理并在界面返回成功/失败/空态/无权限等可理解反馈": f"L2：{exception_flow}",
        "Lz：结果可回显并可复测，且仅覆盖已选 Must 链路范围": "L3：核心结果可回显、可回查、可复测",
        "Lx：已选主链路页面可独立完成全流程（非静态占位）": "L1：主链路页面真实可操作并可端到端完成",
        "Ly：已选异常链路具备完整反馈（加载/成功/失败/空态/无权限）": "L2：异常链路含加载/成功/失败/无权限完整反馈",
        "Lz：已选链路结果可回显并可复测，刷新或重进后状态一致": "L3：结果状态刷新后保持一致并可复测",
        "未入选链路（按链路 ID 列出，如 L3/L4）": "L4",
        "从设想反推的非核心扩展能力 #1": "高级统计看板",
        "从设想反推的非核心扩展能力 #2": "跨系统自动同步",
    }


def profile_segment_scope(profile: Dict[str, Any], direction: str) -> str:
    segments = profile.get("segments", {})
    if not isinstance(segments, dict):
        segments = {}
    s0 = str_value(segments.get("S0", "S0"))
    s1 = str_value(segments.get("S1", "S1"))
    s2 = str_value(segments.get("S2", "S2"))
    s3 = str_value(segments.get("S3", "S3"))
    if direction == "A":
        return f"S0（{s0}）"
    if direction == "B":
        return f"S0 + S1（{s0}；{s1}）"
    return f"S0 + S1 + S2（{s0}；{s1}；{s2}；S3 进入路线图：{s3}）"


def profile_phase_strategy(profile: Dict[str, Any], phase: str) -> str:
    strategies = profile.get("phase_strategy", {})
    if not isinstance(strategies, dict):
        strategies = {}
    default_map = {
        "M0": "核心业务闭环可运营",
        "M1": "成长迭代优化或新增受控业务",
        "M2": "成熟规模化与生态能力试点",
    }
    return str_value(strategies.get(phase, default_map.get(phase, "")))


def profile_must_wont_map(profile: Dict[str, Any], direction: str) -> Tuple[List[str], List[str]]:
    must_map = profile.get("must_map", {})
    wont_map = profile.get("wont_map", {})
    must_default: Dict[str, List[str]] = {"A": ["L1", "L2"], "B": ["L1", "L2", "L3"], "C": ["L1", "L2", "L3"]}
    wont_default: Dict[str, List[str]] = {
        "A": ["L3", "L4", "非核心扩展"],
        "B": ["L4", "重型运营能力", "低优先级扩展"],
        "C": ["超大规模优化", "实验性扩展", "跨域集成二期"],
    }
    if not isinstance(must_map, dict):
        must_map = {}
    if not isinstance(wont_map, dict):
        wont_map = {}
    must = must_map.get(direction, must_default.get(direction, ["L1", "L2"]))
    wont = wont_map.get(direction, wont_default.get(direction, ["L4", "非核心扩展", "低优先级能力"]))
    if not isinstance(must, list):
        must = must_default.get(direction, ["L1", "L2"])
    if not isinstance(wont, list):
        wont = wont_default.get(direction, ["L4", "非核心扩展", "低优先级能力"])
    return [str(x).strip() for x in must if str(x).strip()], [str(x).strip() for x in wont if str(x).strip()]


def materialize_mvp_lines(lines: Sequence[str], replacements: Dict[str, str]) -> List[str]:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return replacements.get(key, "见 Discovery 已确认")

    out: List[str] = []
    for raw in lines:
        line = MVP_PLACEHOLDER_RE.sub(repl, raw)
        out.append(line)
    if out:
        out[0] = out[0].replace("待确认", "可执行版")
    normalized: List[str] = []
    for line in out:
        if "AI 在执行 /rpi-init 时需填充具体内容" in line:
            normalized.append("> 以下方向已由框架自动推导，可直接用于范围评审与任务拆解。")
            continue
        normalized.append(line)
    return normalized


def clamp_percent(raw: Any, default: int) -> int:
    val = int_value(raw, default)
    if val < 0:
        return 0
    if val > 100:
        return 100
    return val


def mvp_coverage_policy(runtime: Dict[str, Any]) -> Tuple[int, int, int, int]:
    a = clamp_percent(runtime.get("mvp_coverage_threshold_a", 40), 40)
    b = clamp_percent(runtime.get("mvp_coverage_threshold_b", 80), 80)
    c = clamp_percent(runtime.get("mvp_coverage_threshold_c", 100), 100)
    low_conf = clamp_percent(runtime.get("mvp_low_confidence_ratio_max", 30), 30)
    if b < a:
        b = a
    if c < b:
        c = b
    return a, b, c, low_conf


def markdown_lines(path: Path) -> List[str]:
    return read_text_safe(path).splitlines()


def write_markdown_lines(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def parse_prefixed_value(line: str, labels: Sequence[str]) -> str:
    text = line.strip()
    for label in labels:
        for mark in (":", "："):
            prefix = f"- {label}{mark}"
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
    return ""


def parse_percent_from_text(text: str) -> Optional[int]:
    m = re.search(r"(-?[0-9]{1,3})\s*%", str(text or ""))
    if not m:
        return None
    try:
        value = int(m.group(1))
    except ValueError:
        return None
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def parse_discovery_list(raw: str) -> List[str]:
    return [x for x in spec_state_tool.parse_list(str(raw or "")) if str(x).strip()]


def detect_direction_choice(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    m = re.search(r"(?:方向|Direction)\s*[:：]?\s*([ABC])", text, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"(^|[^A-Za-z])([ABC])([^A-Za-z]|$)", text, flags=re.IGNORECASE)
    if m:
        return m.group(2).upper()
    return ""


def markdown_materialized(path: Path) -> bool:
    if not path.is_file():
        return False
    text = read_text_safe(path)
    if not text.strip():
        return False
    if "{{" in text:
        return False
    meaningful = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        meaningful += 1
    return meaningful >= 8


def phase_artifact_status(paths: Paths) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    phase_dir = paths.base.spec_dir / "phases"
    for phase in ("M0", "M1", "M2"):
        file = phase_dir / f"{phase.lower()}.md"
        exists = file.is_file()
        text = read_text_safe(file) if exists else ""
        placeholders = len(re.findall(r"\{\{[^}]+\}\}", text))
        line_count = len([x for x in text.splitlines() if x.strip()])
        out[phase] = {
            "file": f".rpi-outfile/specs/phases/{phase.lower()}.md",
            "exists": exists,
            "line_count": line_count,
            "placeholder_count": placeholders,
            "materialized": markdown_materialized(file),
        }
    return out


def render_phase_doc(
    phase: str,
    idea: str,
    platform: str,
    direction: str,
    must_ids: Sequence[str],
    wont_ids: Sequence[str],
    coverage_target: str,
    weighted_target: str,
    is_frontend: bool,
) -> str:
    must_text = ", ".join(must_ids) if must_ids else "L1, L2"
    wont_text = ", ".join(wont_ids) if wont_ids else "L4, 非核心扩展, 低优先级能力"
    front_flag = "是" if is_frontend else "否"
    profile = infer_business_profile(idea)
    segment_scope = profile_segment_scope(profile, direction)
    if phase == "M1":
        lines = [
            "# M1 — 成长期迭代与稳定扩展（自动实化）",
            "",
            "## 项目上下文",
            f"- 项目设想：{idea}",
            f"- 运行形态：{platform}",
            f"- 前端项目：{front_flag}",
            f"- ABC 业务段选择：{direction}（{segment_scope}）",
            f"- M1 阶段策略：{profile_phase_strategy(profile, 'M1')}",
            f"- M0 Must 链路：{must_text}",
            f"- M0 Won't 链路：{wont_text}",
            "",
            "## M1 阶段目标",
            "- 在 M0 可运营基线之上提升已选核心链路体验、稳定性与可回归能力。",
            "- 允许新增 1 条受控业务方向（仅限已选业务段内），并同步更新 Must/Won't 与加权覆盖率。",
            "- 补齐契约细节、异常路径与跨模块协作验证，保证持续交付质量。",
            "",
            "## M1 入场检查（自动回填）",
            "- [ ] M0 已选 Must 链路在当前分支可复现",
            f"- [ ] 覆盖率目标与方向一致（当前：{coverage_target}）",
            f"- [ ] 加权覆盖率目标已记录（当前：{weighted_target}）",
            "- [ ] 若发生调权，已记录“提升项+降权项+理由+影响”",
            "- [ ] spec/tasks/milestones 与 discovery 结论一致",
            "- [ ] 集成与异常测试入口已准备",
            "",
            "## M1 任务建议",
            f"- 稳定性任务：针对 {must_text} 建立回归清单、失败重试与可观测断点",
            "- 成长任务：新增 1 条受控业务方向并验证不会破坏 M0 可运营闭环",
            "- 契约任务：补全输入/输出/错误语义，减少实现歧义",
            "- 集成任务：补齐跨模块或跨层调用链的最小可验证闭环",
            "- 质量任务：lint/typecheck/unit/integration 固化为可重复门禁",
            "",
            "## M1 出场门禁（建议）",
            "- spec_state_valid_m1",
            "- scope_guard_passed_m1",
            "- contract_spec_complete",
            "- integration tests（由 gates phase_gates.M1 控制）",
        ]
        if is_frontend:
            lines.append("- ux_compliance_passed_m1")
        return "\n".join(lines).rstrip("\n") + "\n"

    lines = [
        "# M2 — 成熟期规模化与治理完善（自动实化）",
        "",
        "## 项目上下文",
        f"- 项目设想：{idea}",
        f"- 运行形态：{platform}",
        f"- 前端项目：{front_flag}",
        f"- ABC 业务段选择：{direction}（{segment_scope}）",
        f"- M2 阶段策略：{profile_phase_strategy(profile, 'M2')}",
        f"- 核心链路：{must_text}",
        "",
        "## M2 阶段目标",
        "- 在已选业务段上补齐规模化能力、性能稳定性与治理链路。",
        "- 固化可观测、可回滚、可审计能力，支持稳定运营和持续迭代。",
        "- 可开展小范围生态试点，但不稀释核心业务质量目标。",
        "",
        "## M2 入场检查（自动回填）",
        "- [ ] M1 质量门禁通过，且核心链路可稳定复现",
        "- [ ] M0/M1 目标与当前成熟期范围保持一致",
        "- [ ] 关键路径已配置日志/指标/错误可观测信号",
        "- [ ] 回滚策略与恢复步骤已文档化并可演练",
        "- [ ] 安全扫描与依赖风险检查具备执行入口",
        "",
        "## M2 任务建议",
        "- 规模化任务：容量压测、性能瓶颈定位、关键路径优化",
        "- 发布任务：发布脚本、环境变量、灰度/回滚流程确认",
        "- 可观测任务：关键指标、告警阈值、错误分级统一",
        "- 审计任务：生成可追溯证据包（门禁结果、关键日志、修复记录）",
        "- 生态试点任务：仅在不影响核心链路前提下验证 1 条增量能力",
        "",
        "## M2 出场门禁（建议）",
        "- spec_state_valid_m2",
        "- architecture_guard_passed_m2",
        "- entry_integrity_passed_m2",
        "- e2e/security gates（由 gates phase_gates.M2 控制）",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def materialize_phase_specs(
    paths: Paths,
    idea: str,
    platform: str,
    direction: str,
    must_ids: Sequence[str],
    wont_ids: Sequence[str],
    coverage_target: str,
    weighted_target: str,
    is_frontend: bool,
) -> None:
    phase_dir = paths.base.spec_dir / "phases"
    phase_dir.mkdir(parents=True, exist_ok=True)
    m1_file = phase_dir / "m1.md"
    m2_file = phase_dir / "m2.md"

    m1_doc = render_phase_doc(
        "M1",
        idea,
        platform,
        direction,
        must_ids,
        wont_ids,
        coverage_target,
        weighted_target,
        is_frontend,
    )
    m2_doc = render_phase_doc(
        "M2",
        idea,
        platform,
        direction,
        must_ids,
        wont_ids,
        coverage_target,
        weighted_target,
        is_frontend,
    )
    m1_file.write_text(m1_doc, encoding="utf-8")
    m2_file.write_text(m2_doc, encoding="utf-8")


def short_task_title(raw: str, fallback: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return fallback
    text = re.sub(r"^[Ll][0-9]+\s*[:：]\s*", "", text).strip()
    text = re.split(r"[，,。；;：:（）()]", text, maxsplit=1)[0].strip()
    if not text:
        return fallback
    return text[:24]


def materialize_l0_docs(
    paths: Paths,
    idea: str,
    platform: str,
    profile: Dict[str, Any],
    direction: str,
    must_ids: Sequence[str],
    wont_ids: Sequence[str],
    coverage_target: str,
    weighted_target: str,
) -> None:
    desc_map = collect_link_descriptions(paths)
    for lid, desc in build_profile_link_descriptions(profile).items():
        if lid not in desc_map:
            desc_map[lid] = desc

    def item_desc(item: str) -> str:
        token = str(item).strip()
        if not token:
            return ""
        if re.fullmatch(r"L[1-9][0-9]*", token):
            return desc_map.get(token, token)
        return token

    must_desc = [item_desc(x) for x in must_ids if item_desc(x)]
    wont_desc = [item_desc(x) for x in wont_ids if item_desc(x)]

    domain = str_value(profile.get("domain", "核心业务"))
    actor = str_value(profile.get("actor", "业务操作员"))
    core_object = str_value(profile.get("core_object", "核心记录"))
    main_flow = str_value(profile.get("main_flow", "完成核心业务流程"))
    exception_flow = str_value(profile.get("exception_flow", "异常场景返回可解释反馈"))
    governance_flow = str_value(profile.get("governance_flow", "治理链路保持可追溯"))
    extension_flow = str_value(profile.get("extension_flow", "扩展链路按阶段引入"))
    segment_scope = profile_segment_scope(profile, direction)

    if domain == "在线课程":
        entities = [
            ("user", "学员账号"),
            ("course", "课程基础信息"),
            ("media_asset", "短视频/音频资源"),
            ("favorite_subscription", "订阅收藏关系"),
            ("learning_history", "学习历史轨迹"),
            ("vip_activation_code", "VIP 激活码"),
            ("vip_entitlement", "VIP 权益状态"),
        ]
        key_fields = [
            "`course.id`：课程唯一标识",
            "`media_asset.type`：视频/音频类型",
            "`learning_history.progress`：学习进度",
            "`favorite_subscription.state`：订阅/收藏状态",
            "`vip_activation_code.code`：激活码",
            "`vip_entitlement.expire_at`：权益失效时间",
        ]
        input_contract = [
            "`POST /api/auth/login`：账号凭证必填",
            "`GET /api/courses`：课程列表分页查询",
            "`POST /api/courses/{id}/favorite`：课程收藏/订阅",
            "`POST /api/learning/history`：上报学习历史",
            "`POST /api/vip/activate`：激活码激活",
        ]
        errors = ["UNAUTHORIZED", "VIP_REQUIRED", "ACTIVATION_CODE_INVALID", "MEDIA_UNAVAILABLE"]
        m1_scope = "播放体验优化、学习进度增强、搜索推荐（受控新增）"
        m2_scope = "并发播放稳定性、性能容量治理、监控告警与审计闭环"
    elif domain == "用户管理":
        entities = [
            ("user", "用户档案"),
            ("role_binding", "角色绑定关系"),
            ("audit_log", "审计日志"),
        ]
        key_fields = [
            "`user.id`：用户唯一标识",
            "`user.status`：用户状态",
            "`role_binding.role`：角色标识",
        ]
        input_contract = [
            "`POST /api/users`：创建用户档案",
            "`PATCH /api/users/{id}`：更新用户档案",
            "`POST /api/users/{id}/roles`：绑定角色",
        ]
        errors = ["UNAUTHORIZED", "VALIDATION_ERROR", "CONFLICT"]
        m1_scope = "检索筛选增强、批量处理、角色治理补强"
        m2_scope = "规模化访问稳定性、审计治理与发布保障"
    elif domain == "订单管理":
        entities = [
            ("order", "订单记录"),
            ("order_item", "订单明细"),
            ("order_status_log", "订单状态变更轨迹"),
        ]
        key_fields = [
            "`order.id`：订单唯一标识",
            "`order.status`：订单状态",
            "`order_item.sku_id`：商品标识",
        ]
        input_contract = [
            "`POST /api/orders`：创建订单",
            "`PATCH /api/orders/{id}/status`：推进状态流转",
            "`GET /api/orders`：按条件检索订单",
        ]
        errors = ["UNAUTHORIZED", "STOCK_INSUFFICIENT", "STATUS_CONFLICT"]
        m1_scope = "履约流程优化、风控规则增强、查询体验优化"
        m2_scope = "高并发交易稳定性、容量治理、监控告警与审计闭环"
    else:
        entities = [
            ("user", "系统用户"),
            ("core_record", f"{core_object}"),
            ("audit_log", "审计记录"),
        ]
        key_fields = [
            "`core_record.id`：唯一标识",
            "`core_record.status`：生命周期状态",
            "`audit_log.trace_id`：追踪标识",
        ]
        input_contract = [
            "`POST /api/core-records`：创建核心记录",
            "`PATCH /api/core-records/{id}`：更新核心记录",
            "`GET /api/core-records`：查询核心记录",
        ]
        errors = ["UNAUTHORIZED", "VALIDATION_ERROR", "CONFLICT"]
        m1_scope = "主链路体验优化与受控新增能力"
        m2_scope = "规模化稳定性、性能治理与审计闭环"

    epic_lines: List[str] = [
        "# L0 Epic",
        "",
        "## Facts / Assumptions / Open Questions",
        "",
        "### Facts",
        f"1. {domain}业务价值取决于已选 Must 链路是否可稳定执行并可回归。",
        f"2. 当前核心链路需要围绕“{main_flow}”形成可运营闭环。",
        "",
        "### Assumptions",
        f"1. M0 聚焦 {profile_phase_strategy(profile, 'M0')}，不扩张到 Won't 范围。",
        f"2. 关键异常将按“{exception_flow}”提供可解释反馈。",
        "",
        "### Open Questions",
        f"1. M1 是否优先推进：{profile_phase_strategy(profile, 'M1')}？",
        "",
        f"- 目标用户：{actor}",
        f"- 核心问题：{main_flow} 与 {governance_flow} 缺少统一工程闭环。",
        f"- 核心价值：在 {segment_scope} 内交付可运营、可追溯、可迭代的{domain}能力。",
        "- 成功指标（2-4 项）：",
        f"  - {coverage_target} 且已选 Must 链路可复测",
        "  - 关键异常链路具备可解释反馈",
        f"  - 加权覆盖率目标达到 {weighted_target}",
        "- Must：",
    ]
    for row in must_desc:
        epic_lines.append(f"  - {row}")
    epic_lines.append("- Won't：")
    for row in wont_desc[:3]:
        epic_lines.append(f"  - {row}")

    spec_lines: List[str] = [
        "# L0 Spec",
        "",
        "## 架构边界",
        "- In Scope：",
    ]
    for row in must_desc:
        spec_lines.append(f"  - {row}")
    spec_lines.append("- Out of Scope：")
    for row in wont_desc[:3]:
        spec_lines.append(f"  - {row}")
    spec_lines.extend(
        [
            "",
            "## 数据模型",
            "- 核心实体：",
        ]
    )
    for model, desc in entities:
        spec_lines.append(f"  - `{model}`：{desc}")
    spec_lines.append("- 关键字段：")
    for field_row in key_fields:
        spec_lines.append(f"  - {field_row}")
    spec_lines.extend(
        [
            "",
            "## 接口契约",
            "- 输入契约：",
        ]
    )
    for row in input_contract:
        spec_lines.append(f"  - {row}")
    spec_lines.extend(
        [
            "- 输出契约：",
            "  - 成功：`200/201` + 结构化响应",
            "  - 失败：`4xx/5xx` + `code/message`",
            "- 错误码/失败语义：",
        ]
    )
    for code in errors:
        spec_lines.append(f"  - `{code}`")
    spec_lines.extend(
        [
            "",
            "## 关键流程",
            "- 正向路径：",
            f"  1. {main_flow}",
            "  2. 已选 Must 链路返回可验证结果",
            "- 异常路径：",
            f"  - {exception_flow}",
            "  - 范围外请求返回范围限制提示",
            "- 回退策略：",
            "  - 写入失败不落库，保留日志并支持可重复重试",
            "",
            "## 验收与异常矩阵",
            "- 验收标准：",
            "  - 已选 Must 链路逐条通过 E2E + 异常检查",
            "  - 结果可回显、可复测、可追溯",
            "- 异常矩阵：",
            "  - 未认证访问 -> 401/403",
            "  - 参数非法/规则冲突 -> 400/409",
            "",
            "## 非功能预算（性能/成本/稳定性）",
            "- 性能预算：主链路 P95 < 800ms（按业务特性可调整）",
            "- 成本预算：M0 单环境支撑首轮验证流量",
            "- 稳定性预算：核心接口可用性 >= 99.9%",
        ]
    )

    milestone_lines: List[str] = [
        "# L0 Milestones",
        "",
        "## M0",
        f"- 目标：{profile_phase_strategy(profile, 'M0')}",
        f"- 范围：{'; '.join(must_desc) if must_desc else '已选核心业务链路'}",
        "- 交付：可运行系统、核心测试、可追溯日志",
        "- 验收：M0 门控全通过",
        "- 时长：2-4 周",
        f"- 量化指标：{coverage_target}",
        "",
        "## M1",
        f"- 目标：{profile_phase_strategy(profile, 'M1')}",
        f"- 范围：{m1_scope}",
        "- 交付：稳定性增强、契约补全、集成验证",
        "- 验收：关键路径持续稳定且可回归",
        "- 时长：2-4 周",
        "- 量化指标：关键路径失败率持续下降",
        "",
        "## M2",
        f"- 目标：{profile_phase_strategy(profile, 'M2')}",
        f"- 范围：{m2_scope}",
        "- 交付：发布保障、可观测、审计证据链",
        "- 验收：上线门禁全部通过",
        "- 时长：1-3 周",
        "- 量化指标：故障恢复演练通过率 100%",
    ]

    task_lines: List[str] = ["# L0 Tasks", "", "## M0"]
    last_task_title = ""
    for idx, link_id in enumerate(must_ids, start=1):
        desc = item_desc(link_id)
        title = short_task_title(desc, f"链路 {link_id} 交付")
        dep = "无（首任务）" if idx == 1 else last_task_title
        wont_hint = wont_desc[0] if wont_desc else "未入选范围能力"
        task_lines.extend(
            [
                f"- Task {idx} {title}",
                f"  - 目标：完成 {link_id} 链路交付（{desc}）",
                "  - 输入/输出：按 Spec 契约输入 -> 可验证业务回执",
                f"  - 依赖：{dep}",
                f"  - 实现边界（不做）：{wont_hint}",
                f"  - 可执行验收标准：{link_id} 链路 E2E + 异常检查通过",
                "",
            ]
        )
        last_task_title = title
    task_lines.extend(
        [
            "## M1",
            f"- Task {len(must_ids) + 1} {short_task_title(m1_scope, '成长迭代任务')}",
            "",
            "## M2",
            f"- Task {len(must_ids) + 2} {short_task_title(m2_scope, '成熟治理任务')}",
        ]
    )

    (paths.spec_l0_dir / "epic.md").write_text("\n".join(epic_lines).rstrip("\n") + "\n", encoding="utf-8")
    (paths.spec_l0_dir / "spec.md").write_text("\n".join(spec_lines).rstrip("\n") + "\n", encoding="utf-8")
    (paths.spec_l0_dir / "milestones.md").write_text("\n".join(milestone_lines).rstrip("\n") + "\n", encoding="utf-8")
    (paths.spec_l0_dir / "tasks.md").write_text("\n".join(task_lines).rstrip("\n") + "\n", encoding="utf-8")


def ensure_phase_verify_mapping(paths: Paths, is_frontend: bool) -> None:
    gates_file = paths.base.config_dir / "gates.json"
    if not gates_file.is_file():
        return

    cfg = read_json_obj(gates_file)
    verify = cfg.get("verify")
    if not isinstance(verify, dict):
        verify = {}
    for phase in ("default", "M0", "M1", "M2"):
        if not isinstance(verify.get(phase), list):
            verify[phase] = []

    def add_phase_check(phase: str, name: str, command: str) -> None:
        rows = verify.get(phase)
        if not isinstance(rows, list):
            rows = []
            verify[phase] = rows
        for item in rows:
            if isinstance(item, dict) and str_value(item.get("name", ""), "") == name:
                return
        rows.append({"name": name, "command": command})

    add_phase_check("M1", "spec_state_valid_m1", "bash .claude/workflow/rpi.sh spec verify --scope all --quiet")
    add_phase_check("M1", "scope_guard_passed_m1", "bash .claude/workflow/rpi.sh check scope --quiet")
    add_phase_check("M1", "contract_spec_complete_m1", "bash .claude/workflow/rpi.sh check contract --quiet")
    if is_frontend:
        add_phase_check("M1", "ux_compliance_passed_m1", "bash .claude/workflow/rpi.sh check ux --quiet")

    add_phase_check("M2", "spec_state_valid_m2", "bash .claude/workflow/rpi.sh spec verify --scope all --quiet")
    add_phase_check("M2", "architecture_guard_passed_m2", "bash .claude/workflow/rpi.sh check architecture --quiet")
    add_phase_check("M2", "entry_integrity_passed_m2", "bash .claude/workflow/rpi.sh check entry --quiet")

    cfg["verify"] = verify
    write_json_atomic(gates_file, cfg)


def extract_idea_platform_from_mvp(mvp_file: Path) -> Tuple[str, str]:
    idea = ""
    platform = ""
    if not mvp_file.is_file():
        return idea, platform
    for line in markdown_lines(mvp_file):
        if not idea:
            idea = parse_prefixed_value(line, ["项目设想", "Project Idea", "Idea"])
        if not platform:
            platform = parse_prefixed_value(line, ["运行形态（暂定）", "运行形态", "Platform"])
        if idea and platform:
            break
    return idea, platform


def load_alias_map(project_dir: Path) -> Dict[str, List[str]]:
    spec_paths = spec_state_tool.load_paths_from_project(project_dir)
    return spec_state_tool.load_field_aliases(spec_paths)


def extract_discovery_field(project_dir: Path, discovery_file: Path, alias_key: str, fallback: Sequence[str]) -> str:
    if not discovery_file.is_file():
        return ""
    alias_map = load_alias_map(project_dir)
    aliases = spec_state_tool.aliases_for(alias_map, alias_key, list(fallback))
    return spec_state_tool.extract_field_value(markdown_lines(discovery_file), aliases).strip()


def replace_or_insert_field(lines: List[str], aliases: Sequence[str], value: str, heading: str = "## 结论") -> bool:
    alias_keys = {spec_state_tool.normalize_key(a) for a in aliases if str(a).strip()}
    field_pattern = re.compile(r"^(\s*[-*]\s*)(.+?)(\s*[:：]\s*)(.*)\s*$")

    for i, line in enumerate(lines):
        m = field_pattern.match(line)
        if not m:
            continue
        key = m.group(2)
        if spec_state_tool.normalize_key(key) not in alias_keys:
            continue
        prefix = f"{m.group(1)}{key}{m.group(3)}"
        lines[i] = f"{prefix}{value}"
        return True

    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == heading:
            insert_at = i + 1
            j = insert_at
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            insert_at = j
            break

    field_name = aliases[0] if aliases else "字段"
    lines.insert(insert_at, f"- {field_name}：{value}")
    return True


def ensure_section_snapshot(path: Path, snapshot: str) -> None:
    lines = markdown_lines(path) if path.is_file() else []
    heading = "## 用户确认输入快照"
    if heading not in lines:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(heading)
    ts = utc_now()
    lines.append(f"- [{ts}] {snapshot}")
    write_markdown_lines(path, lines)


def ensure_l0_files(paths: Paths) -> None:
    l0 = paths.spec_l0_dir
    blueprint_l0 = paths.base.project_dir / ".rpi-blueprint" / "specs" / "l0"
    required = ["discovery.md", "epic.md", "spec.md", "milestones.md", "tasks.md"]
    for name in required:
        target = l0 / name
        if target.is_file():
            continue
        source = blueprint_l0 / name
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            title = name.replace(".md", "").upper()
            target.write_text(f"# {title}\n\n", encoding="utf-8")


def resolve_idea_platform(paths: Paths, idea_arg: str = "", platform_arg: str = "") -> Tuple[str, str]:
    idea = idea_arg.strip()
    platform = platform_arg.strip()
    mvp_file = paths.spec_l0_dir / "mvp-skeleton.md"

    if not idea or not platform:
        mvp_idea, mvp_platform = extract_idea_platform_from_mvp(mvp_file)
        if not idea and mvp_idea:
            idea = mvp_idea
        if not platform and mvp_platform:
            platform = mvp_platform

    init_summary = read_json_obj(paths.base.state_dir / "init_summary.json")
    if not idea:
        idea = str_value(init_summary.get("idea", ""), "")
    if not platform:
        platform = str_value(init_summary.get("platform", ""), "")

    if not idea:
        discovery_goal = extract_discovery_field(
            paths.base.project_dir,
            paths.spec_l0_dir / "discovery.md",
            "goal",
            ["目标", "Goal", "Objective"],
        )
        idea = discovery_goal or "RPI 项目"
    if not platform:
        platform = "Web"
    return idea, platform


def detect_direction_from_text(text: str, default: str = "A") -> str:
    content = text.strip()
    m = re.search(r"(?:方向|Direction)\s*[:：]?\s*([ABC])", content, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([ABC])\b", content, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return default


def extract_link_ids(text: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for num in re.findall(r"(?<![A-Za-z0-9])L([1-9][0-9]*)", text):
        key = f"L{num}"
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def extract_labeled_link_ids(text: str, field: str) -> List[str]:
    items = extract_labeled_items(text, field)
    out: List[str] = []
    for item in items:
        token = str(item).strip().upper()
        if re.fullmatch(r"L[1-9][0-9]*", token) and token not in out:
            out.append(token)
    return out


def extract_labeled_items(text: str, field: str) -> List[str]:
    content = str(text or "")
    if not content.strip():
        return []
    if field == "must":
        pattern = re.compile(
            r"(?:M0\s*Must|Must(?:\s*链路)?|Must\s*Links?)\s*[:：]?\s*(.+?)"
            r"(?=(?:[，,;；。]?\s*(?:M0\s*Won['’]?t|Won['’]?t|Wont|不做|不包含))|$)",
            flags=re.IGNORECASE,
        )
    else:
        pattern = re.compile(
            r"(?:M0\s*Won['’]?t|Won['’]?t(?:\s*链路)?|Wont(?:\s*链路)?|不做(?:链路)?|不包含(?:链路)?)\s*[:：]?\s*(.+?)"
            r"(?=(?:[，,;；。]?\s*(?:加权覆盖率目标|加权覆盖率|Weighted Coverage(?: Target)?|覆盖率目标|Coverage Target|Direction|方向))|$)",
            flags=re.IGNORECASE,
        )
    m = pattern.search(content)
    if not m:
        return []
    raw = m.group(1).strip()
    parsed = parse_discovery_list(raw)
    if not parsed and raw:
        parsed = [raw]
    out: List[str] = []
    for item in parsed:
        token = str(item).strip()
        if not token or token in out:
            continue
        out.append(token)
    return out


def parse_link_description_map(lines: Sequence[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    table_pattern = re.compile(r"^\|\s*(L[1-9][0-9]*)\s*\|\s*([^|]+?)\s*\|")
    bullet_pattern = re.compile(r"^\s*[-*]\s*(L[1-9][0-9]*)\s*[:：]\s*(.+)\s*$")
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        tm = table_pattern.match(line)
        if tm:
            key = tm.group(1)
            desc = tm.group(2).strip()
            if key not in mapping and desc:
                mapping[key] = desc
            continue
        bm = bullet_pattern.match(line)
        if bm:
            key = bm.group(1)
            desc = bm.group(2).strip()
            if key not in mapping and desc:
                mapping[key] = desc
    return mapping


def collect_link_descriptions(paths: Paths) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for file in (paths.spec_l0_dir / "mvp-skeleton.md", paths.spec_l0_dir / "discovery.md"):
        if not file.is_file():
            continue
        parsed = parse_link_description_map(markdown_lines(file))
        for key, value in parsed.items():
            if key not in mapping and value:
                mapping[key] = value
    return mapping


def normalized_direction(value: str, default: str = "A") -> str:
    v = str(value or "").strip().upper()
    return v if v in {"A", "B", "C"} else default


def infer_default_direction(paths: Paths) -> str:
    summary = read_json_obj(paths.base.state_dir / "init_summary.json")
    decision = summary.get("decision")
    if isinstance(decision, dict):
        choice = normalized_direction(str_value(decision.get("direction_choice", ""), ""), "")
        if choice:
            return choice
    rec = normalized_direction(str_value(summary.get("recommended", ""), ""), "")
    if rec:
        return rec
    discovery = paths.spec_l0_dir / "discovery.md"
    raw = extract_discovery_field(paths.base.project_dir, discovery, "direction", ["选择方向", "Direction"])
    choice = detect_direction_choice(raw)
    return normalized_direction(choice, "A")


def build_auto_confirmation(paths: Paths, cov_a: int, cov_b: int, cov_c: int) -> Tuple[str, str]:
    direction = infer_default_direction(paths)
    decision = read_json_obj(paths.base.state_dir / "init_summary.json").get("decision", {})
    if not isinstance(decision, dict):
        decision = {}
    idea, _ = resolve_idea_platform(paths)
    profile = infer_business_profile(idea)

    discovery = paths.spec_l0_dir / "discovery.md"
    must_ids = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "m0_must",
            ["M0 Must（1-3）", "M0 Must (1-3)", "M0 Must"],
        )
    )
    wont_ids = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "m0_wont",
            ["M0 Won't（>=3）", "M0 Won't (>=3)", "M0 Wont (>=3)", "M0 Won't"],
        )
    )
    if not must_ids:
        must_ids = decision.get("m0_must")
    if not wont_ids:
        wont_ids = decision.get("m0_wont")
    if not isinstance(must_ids, list):
        must_ids = []
    if not isinstance(wont_ids, list):
        wont_ids = []

    must: List[str] = []
    for item in must_ids:
        sid = str(item).strip().upper()
        if re.fullmatch(r"L[1-9][0-9]*", sid) and sid not in must:
            must.append(sid)
    wont: List[str] = []
    for item in wont_ids:
        raw = str(item).strip()
        if not raw:
            continue
        sid = raw.upper()
        if re.fullmatch(r"L[1-9][0-9]*", sid):
            if sid not in wont:
                wont.append(sid)
            continue
        if raw not in wont:
            wont.append(raw)

    mvp_ids = extract_link_ids(read_text_safe(paths.spec_l0_dir / "mvp-skeleton.md"))
    all_ids = mvp_ids if mvp_ids else ["L1", "L2", "L3", "L4"]
    must_defaults, wont_defaults = profile_must_wont_map(profile, direction)
    if not must:
        candidates = [str(x).strip().upper() for x in must_defaults if str(x).strip()]
        must = [x for x in candidates if re.fullmatch(r"L[1-9][0-9]*", x) and (x in all_ids or not mvp_ids)]
        if not must:
            must = [x for x in all_ids if re.fullmatch(r"L[1-9][0-9]*", x)]
        if not must:
            must = ["L1", "L2"]
    must = must[:3]
    if not wont:
        wont = [str(x).strip() for x in wont_defaults if str(x).strip() and str(x).strip() not in must]
    if not wont:
        wont = [x for x in all_ids if x not in must]
    wont = [x for x in wont if x not in must]
    if spec_state_tool.count_chain_refs(wont) < 1:
        for chain in all_ids:
            if re.fullmatch(r"L[1-9][0-9]*", chain) and chain not in must and chain not in wont:
                wont.insert(0, chain)
                break
        if spec_state_tool.count_chain_refs(wont) < 1 and "L4" not in must:
            wont.insert(0, "L4")
    while len(wont) < 3:
        for fallback in ("L4", "非核心扩展", "低优先级能力", "二期集成"):
            if fallback not in wont and fallback not in must:
                wont.append(fallback)
            if len(wont) >= 3:
                break

    weighted_percent = int_value(decision.get("weighted_coverage_percent"), 0)
    if weighted_percent <= 0:
        weighted_target_raw = str_value(decision.get("weighted_coverage_target", ""), "")
        weighted_percent = parse_percent_from_text(weighted_target_raw) or 0
    if weighted_percent <= 0:
        weighted_percent = {"A": cov_a, "B": cov_b, "C": cov_c}.get(direction, cov_a)

    segment_scope = profile_segment_scope(profile, direction)
    auto = (
        f"确认方向{direction}（业务段选择）；"
        f"Must链路: {', '.join(must)}；"
        f"Won't链路: {', '.join(wont[:3])}；"
        f"加权覆盖率: {weighted_percent}%"
    )
    reason = f"auto-generated from discovery/init_summary (direction={direction}, segment={segment_scope})"
    return auto, reason


def render_link_details(ids: Sequence[str], desc_map: Dict[str, str]) -> List[str]:
    rows: List[str] = []
    for link_id in ids:
        desc = desc_map.get(link_id, "未找到链路描述（请在 mvp-skeleton/discovery 的链路池中补充）")
        rows.append(f"{link_id}：{desc}")
    return rows


def build_profile_link_descriptions(profile: Dict[str, Any]) -> Dict[str, str]:
    return {
        "L1": str_value(profile.get("main_flow", "主链路业务流程")),
        "L2": str_value(profile.get("exception_flow", "关键异常处理链路")),
        "L3": str_value(profile.get("governance_flow", "治理与审计链路")),
        "L4": str_value(profile.get("extension_flow", "扩展业务链路")),
    }


def default_direction_label(direction: str) -> str:
    mapping = {
        "A": "A（业务段 S0：MVP运营段）",
        "B": "B（业务段 S0+S1：成长期）",
        "C": "C（业务段 S0+S1+S2：成熟期）",
    }
    return mapping.get(direction, "A（业务段 S0：MVP运营段）")


def seed_discovery_conclusion(
    paths: Paths,
    idea: str,
    direction: str,
    must_ids: Sequence[str],
    wont_ids: Sequence[str],
    coverage_target: str,
    weighted_target: str,
) -> None:
    discovery = paths.spec_l0_dir / "discovery.md"
    if not discovery.is_file():
        ensure_l0_files(paths)
    lines = markdown_lines(discovery)
    alias_map = load_alias_map(paths.base.project_dir)

    goal_aliases = spec_state_tool.aliases_for(alias_map, "goal", ["目标", "Goal"])
    direction_aliases = spec_state_tool.aliases_for(alias_map, "direction", ["选择方向", "Direction"])
    coverage_aliases = spec_state_tool.aliases_for(alias_map, "coverage_target", ["覆盖率目标", "Coverage Target"])
    weighted_aliases = spec_state_tool.aliases_for(
        alias_map,
        "weighted_coverage_target",
        ["加权覆盖率目标", "Weighted Coverage Target"],
    )
    must_aliases = spec_state_tool.aliases_for(alias_map, "m0_must", ["M0 Must（1-3）", "M0 Must"])
    wont_aliases = spec_state_tool.aliases_for(alias_map, "m0_wont", ["M0 Won't（>=3）", "M0 Won't"])
    abc_scope_aliases = spec_state_tool.aliases_for(alias_map, "abc_scope", ["ABC 业务段选择", "ABC Scope"])
    phase_strategy_aliases = spec_state_tool.aliases_for(
        alias_map,
        "phase_strategy",
        ["M0~M2 阶段扩展策略", "Phase Strategy M0~M2"],
    )
    profile = infer_business_profile(idea)
    segment_scope = profile_segment_scope(profile, direction)
    phase_strategy_text = (
        f"M0={profile_phase_strategy(profile, 'M0')}；"
        f"M1={profile_phase_strategy(profile, 'M1')}；"
        f"M2={profile_phase_strategy(profile, 'M2')}"
    )

    replace_or_insert_field(lines, goal_aliases, idea, heading="## 一句话设想")
    replace_or_insert_field(lines, direction_aliases, default_direction_label(direction), heading="## 结论")
    replace_or_insert_field(lines, abc_scope_aliases, f"{direction}（{segment_scope}）", heading="## 结论")
    replace_or_insert_field(lines, coverage_aliases, coverage_target, heading="## 结论")
    replace_or_insert_field(lines, weighted_aliases, weighted_target, heading="## 结论")
    replace_or_insert_field(lines, phase_strategy_aliases, phase_strategy_text, heading="## 结论")
    replace_or_insert_field(lines, must_aliases, ", ".join(must_ids), heading="## 结论")
    replace_or_insert_field(lines, wont_aliases, ", ".join(wont_ids), heading="## 结论")

    write_markdown_lines(discovery, lines)


def cmd_save_init_summary(paths: Paths, argv: Sequence[str]) -> int:
    _ = argv
    ensure_layout(paths)
    skeleton = paths.spec_l0_dir / "mvp-skeleton.md"
    state_dir = paths.base.state_dir
    summary_file = state_dir / "init_summary.json"

    if not skeleton.is_file():
        safe_print('{"error":"mvp-skeleton.md not found"}', stream=sys.stderr)
        return 1

    content = read_text_safe(skeleton)
    idea = ""
    platform = ""
    project_type = ""
    module_info = ""
    for line in content.splitlines():
        if line.startswith("- 项目设想：") and not idea:
            idea = line.replace("- 项目设想：", "", 1).strip()
        if line.startswith("- 运行形态（暂定）：") and not platform:
            platform = line.replace("- 运行形态（暂定）：", "", 1).strip()
        if line.startswith("- 项目类型：") and not project_type:
            project_type = line.replace("- 项目类型：", "", 1).strip()
        if line.startswith("- 模块数量：") and not module_info:
            module_info = line.replace("- 模块数量：", "", 1).strip()

    is_frontend, _ = detect_project_surface(idea, platform, project_type)
    is_multi_module = "多模块" in module_info

    def extract_direction_must(tag: str) -> str:
        in_section = False
        for line in content.splitlines():
            if line.startswith(f"### 方向 {tag}"):
                in_section = True
                continue
            if in_section and line.startswith("### 方向 "):
                in_section = False
            if in_section and line.startswith("- Must："):
                return line.replace("- Must：", "", 1).strip()
        return ""

    dir_a = extract_direction_must("A")
    dir_b = extract_direction_must("B")
    dir_c = extract_direction_must("C")

    recommended = "A"
    if re.search(r"方向 B.*⭐", content):
        recommended = "B"
    if re.search(r"方向 C.*⭐", content):
        recommended = "C"

    init_phase = "skeleton_generated"
    discovery = paths.spec_l0_dir / "discovery.md"
    if discovery.is_file():
        discovery_text = read_text_safe(discovery)
        direction_match = re.search(
            r"^-\s*(选择方向|Direction)\s*[:：]\s*(.+)$",
            discovery_text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if direction_match:
            direction_value = direction_match.group(2).strip()
            has_direction_choice = bool(re.search(r"[ABC]", direction_value.upper()))
            placeholder_tokens = {"A/B/C", "待确认", "TBD", "N/A", "-"}
            if has_direction_choice and "{{" not in direction_value and direction_value not in placeholder_tokens:
                init_phase = "direction_confirmed"

    direction_raw = extract_discovery_field(
        paths.base.project_dir,
        discovery,
        "direction",
        ["选择方向", "Direction"],
    )
    direction_choice = detect_direction_choice(direction_raw)
    coverage_target = extract_discovery_field(
        paths.base.project_dir,
        discovery,
        "coverage_target",
        ["覆盖率目标", "Coverage Target", "Coverage"],
    )
    weighted_coverage_target = extract_discovery_field(
        paths.base.project_dir,
        discovery,
        "weighted_coverage_target",
        ["加权覆盖率目标", "Weighted Coverage Target", "Weighted Coverage"],
    )
    m0_must = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "m0_must",
            ["M0 Must（1-3）", "M0 Must (1-3)", "M0 Must"],
        )
    )
    m0_wont = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "m0_wont",
            ["M0 Won't（>=3）", "M0 Won't (>=3)", "M0 Wont (>=3)", "M0 Won't"],
        )
    )
    success_metrics = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "success_metrics",
            ["成功指标（2-4）", "Success Metrics (2-4)", "Success Metrics"],
        )
    )
    ubiquitous_language = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "ubiquitous_language",
            ["统一语言（Ubiquitous Language）", "统一语言", "Ubiquitous Language"],
        )
    )
    bounded_contexts = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "bounded_contexts",
            ["限界上下文（Bounded Context）", "限界上下文", "Bounded Context"],
        )
    )
    domain_invariants = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "domain_invariants",
            ["业务不变量（Domain Invariants）", "业务不变量", "Domain Invariants"],
        )
    )
    m0_contexts = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "m0_contexts",
            ["已选上下文（M0）", "已选上下文", "M0 Contexts", "Selected Contexts (M0)"],
        )
    )
    priority_overrides = parse_discovery_list(
        extract_discovery_field(
            paths.base.project_dir,
            discovery,
            "priority_overrides",
            ["优先级调权", "优先级调权（可选）", "Priority Overrides", "Feature Weight Overrides"],
        )
    )

    coverage_percent = parse_percent_from_text(coverage_target)
    weighted_coverage_percent = parse_percent_from_text(weighted_coverage_target)
    decision_complete = bool(
        direction_choice
        and coverage_percent is not None
        and len(m0_must) >= 1
        and len(m0_wont) >= 3
    )

    gates_cfg = read_json_obj(paths.base.config_dir / "gates.json")
    phase_gates = gates_cfg.get("phase_gates", {})
    verify_cfg = gates_cfg.get("verify", {})
    phase_gate_counts: Dict[str, int] = {}
    phase_verify_counts: Dict[str, int] = {}
    for phase in ("M0", "M1", "M2"):
        gates_list = phase_gates.get(phase, []) if isinstance(phase_gates, dict) else []
        verify_list = verify_cfg.get(phase, []) if isinstance(verify_cfg, dict) else []
        phase_gate_counts[phase] = len(gates_list) if isinstance(gates_list, list) else 0
        phase_verify_counts[phase] = len(verify_list) if isinstance(verify_list, list) else 0
    verify_default = verify_cfg.get("default", []) if isinstance(verify_cfg, dict) else []
    verify_default_count = len(verify_default) if isinstance(verify_default, list) else 0

    summary = {
        "idea": idea,
        "platform": platform,
        "project_type": project_type,
        "is_frontend": is_frontend,
        "is_multi_module": is_multi_module,
        "directions": {"A": dir_a, "B": dir_b, "C": dir_c},
        "recommended": recommended,
        "recommendation": recommended,
        "init_phase": init_phase,
        "decision": {
            "direction": direction_raw,
            "direction_choice": direction_choice,
            "coverage_target": coverage_target,
            "coverage_percent": coverage_percent,
            "weighted_coverage_target": weighted_coverage_target,
            "weighted_coverage_percent": weighted_coverage_percent,
            "m0_must": m0_must,
            "m0_wont": m0_wont,
            "success_metrics": success_metrics,
            "priority_overrides": priority_overrides,
            "decision_complete": decision_complete,
        },
        "ddd_lite": {
            "ubiquitous_language_count": len(ubiquitous_language),
            "bounded_contexts_count": len(bounded_contexts),
            "domain_invariants_count": len(domain_invariants),
            "selected_m0_contexts_count": len(m0_contexts),
            "ubiquitous_language": ubiquitous_language,
            "bounded_contexts": bounded_contexts,
            "domain_invariants": domain_invariants,
            "selected_m0_contexts": m0_contexts,
        },
        "phase_artifacts": phase_artifact_status(paths),
        "gate_matrix": {
            "phase_gates_count": phase_gate_counts,
            "verify_default_count": verify_default_count,
            "verify_phase_count": phase_verify_counts,
        },
        "saved_at": utc_now(),
    }
    write_json_atomic(summary_file, summary)
    safe_print("[OK] init summary saved: .rpi-outfile/state/init_summary.json")
    return 0


def cmd_switch_phase(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    if len(argv) < 2:
        safe_print("Usage: bash .claude/workflow/rpi.sh task phase <M0|M1|M2> <reason>", stream=sys.stderr)
        return 1
    phase = argv[0]
    reason = " ".join(argv[1:]).strip()
    if phase not in {"M0", "M1", "M2"}:
        safe_print(f"Invalid phase: {phase} (must be M0|M1|M2)", stream=sys.stderr)
        return 1

    ts = utc_now()
    ratio = task_flow.phase_ratio(phase)
    write_json_atomic(paths.base.phase_file, {"phase": phase, "spec_ratio": ratio, "updated_at": ts})

    current = read_json_obj(paths.base.current_task_file)
    current["phase"] = phase
    current["last_updated_at"] = ts
    write_json_atomic(paths.base.current_task_file, current)

    append_event(paths, {"event": "phase_switch", "phase": phase, "reason": reason})
    safe_print(f"Phase switched to {phase} (Vibe:Spec {ratio})")
    return 0


def cmd_bootstrap_gate(paths: Paths, argv: Sequence[str]) -> int:
    _ = argv
    required = [
        paths.base.project_dir / ".rpi-outfile/specs/00_master_spec.md",
        paths.base.project_dir / ".rpi-outfile/specs/l0/discovery.md",
        paths.base.project_dir / ".rpi-outfile/specs/l0/epic.md",
        paths.base.project_dir / ".rpi-outfile/specs/l0/spec.md",
        paths.base.project_dir / ".rpi-outfile/specs/l0/milestones.md",
        paths.base.project_dir / ".rpi-outfile/specs/l0/tasks.md",
    ]
    missing = [p for p in required if not p.is_file()]
    if missing:
        for p in missing:
            safe_print(f"missing: {p}", stream=sys.stderr)
        safe_print("bootstrap gate failed: missing required spec files", stream=sys.stderr)
        return 1
    safe_print("bootstrap gate passed")
    return 0


def cmd_create_mvp(paths: Paths, argv: Sequence[str]) -> int:
    if not argv:
        safe_print('Usage: bash .claude/workflow/rpi.sh init deepen [idea] [platform]', stream=sys.stderr)
        return 1
    idea = argv[0]
    platform = argv[1] if len(argv) > 1 else "Web"
    l0_dir = paths.spec_l0_dir
    l0_dir.mkdir(parents=True, exist_ok=True)
    mvp_file = l0_dir / "mvp-skeleton.md"
    if mvp_file.is_file():
        snapshot_before_mutation(paths, "create_mvp", [mvp_file], actor="create-mvp")
    runtime = load_runtime(paths)
    cov_a, cov_b, cov_c, low_conf_budget = mvp_coverage_policy(runtime)

    is_frontend, is_headless_cli = detect_project_surface(idea, platform, "")
    module_count = len(re.findall(r"(用户|订单|商品|权限|支付|库存|物流|评论|消息)", idea))
    is_multi_module = module_count >= 3

    if is_frontend:
        project_type = "前端应用项目"
    elif is_headless_cli:
        project_type = "终端/后端服务项目"
    else:
        project_type = "后端服务项目"

    def extract_expr(expr: str) -> str:
        m = re.search(expr, idea, flags=re.IGNORECASE)
        return m.group(0).strip() if m else ""

    extract_core = extract_expr(r"(核心功能|必须|关键功能|主要功能)[^，。,.\n]*")
    extract_wont = extract_expr(r"(不做|不需要|不含|不支持|排除)[^，。,.\n]*")
    extract_tech = extract_expr(r"(React|Vue|Next|Nuxt|Angular|Svelte|Node|Express|Spring|Django|Flask|Go|Rust|Python|Java|TypeScript)[^，。,.\n]*")

    profile = infer_business_profile(idea)
    lines: List[str] = []
    lines.append("# MVP 骨架草案（待确认）")
    lines.append("")
    lines.append("> 阶段目标：先确认方向，不一次性生成全量 Spec。")
    lines.append("")
    lines.append("## 0) 一句话设想")
    lines.append("")
    lines.append(f"- 项目设想：{idea}")
    lines.append(f"- 运行形态（暂定）：{platform}")
    lines.append(f"- 项目类型：{project_type}")
    lines.append(f"- 模块数量：{'多模块（≥3）' if is_multi_module else '单模块'}")
    lines.append("")
    lines.append("## 1) Facts / Assumptions")
    lines.append("")
    lines.append("### Facts（从用户输入提取）")
    lines.append("")
    lines.append(f"1. 一句话设想：{idea}")
    lines.append(
        f"2. 项目特征：前端={'是' if is_frontend else '否'}，终端/无界面={'是' if is_headless_cli else '否'}，多模块={'是' if is_multi_module else '否'}"
    )
    lines.append(f"3. 用户提及核心功能：{extract_core}" if extract_core else "3. 核心功能：未明确提及（由框架推导）")
    lines.append(f"4. 用户提及不做：{extract_wont}" if extract_wont else "4. Won't：未明确提及（由框架推导）")
    lines.append(f"5. 用户提及技术栈：{extract_tech}" if extract_tech else "5. 技术栈：未明确提及（由框架推导）")
    lines.append("")
    lines.append("### Assumptions（可调整）")
    lines.append("")
    lines.append("1. 优先做最短可演示闭环（M0）。")
    lines.append("2. 技术路径优先成熟稳定方案。")
    lines.append("3. 先单租户、基础权限，不提前引入复杂平台能力。")
    lines.append("4. M0 使用 DDD-Lite：统一语言 + 限界上下文 + 业务不变量，不引入全量战术模式。")
    assumption_idx = 5
    if is_frontend:
        lines.append(
            f"{assumption_idx}. 应用型 MVP 必须交付完整可用 UX（关键页面 + 核心交互 + 成功/失败反馈），但仅覆盖已选 MVP Must 范围，禁止占位式半成品界面。"
        )
        assumption_idx += 1
        lines.append(f"{assumption_idx}. 前端项目需要统一 UX 交互标准。")
        assumption_idx += 1
    elif is_headless_cli:
        lines.append(f"{assumption_idx}. 终端/后端场景允许无图形界面，以命令或 API 输出作为可验证结果。")
        assumption_idx += 1
    if is_multi_module:
        lines.append(f"{assumption_idx}. 多模块项目需要先定义全局骨架。")
    lines.append("")
    lines.append("## 2) 业务阶段画布（框架推导，用户确认）")
    lines.append("")
    lines.append("> 先确认阶段与链路，再确定 Must/Won't，避免只做零散功能点。")
    lines.append("")
    lines.append("| 阶段ID | 阶段目标 | 主要输入 | 可验证输出 |")
    lines.append("|---|---|---|---|")
    lines.append("| S1 | 入口建模（角色/对象/入口） | {{角色、对象、触发条件}} | {{可进入业务流程}} |")
    lines.append("| S2 | 核心决策（规则/鉴权/路由） | {{请求、规则、上下文}} | {{决策结果}} |")
    lines.append("| S3 | 执行交付（状态变更/产出） | {{决策结果、执行参数}} | {{业务结果已落地}} |")
    lines.append("| S4 | 回执复用（反馈/追溯/对外复用） | {{执行结果、审计信息}} | {{可复用输出/反馈/追溯}} |")
    lines.append("")
    lines.append("## 3) 核心业务链路候选池（按链路选范围，不按零散功能选范围）")
    lines.append("")
    lines.append("> 每条链路都要跨阶段（S1→S4），并标注优先级与置信度。")
    lines.append("")
    lines.append("| 链路ID | 链路描述 | 覆盖阶段 | 优先级 | 置信度 |")
    lines.append("|---|---|---|---|---|")
    lines.append("| L1 | {{主链路：高频核心业务流程}} | S1→S2→S3→S4 | P0 | 高/中/低 |")
    lines.append("| L2 | {{关键异常链路：失败/拒绝/回滚流程}} | S2→S3→S4 | P0 | 高/中/低 |")
    lines.append("| L3 | {{治理链路：权限/状态/一致性变更}} | S1→S2→S3→S4 | P0/P1 | 高/中/低 |")
    lines.append("| L4 | {{扩展链路：运营/报表/外部集成}} | S3→S4 | P1/P2 | 高/中/低 |")
    lines.append("")
    lines.append("## 4) DDD-Lite 语义与边界（语义收敛层）")
    lines.append("")
    lines.append("> 目标：把需求语言、上下文边界、关键业务规则固化为可校验结构，降低 AI 语义漂移。")
    lines.append("")
    lines.append("- 统一语言（Ubiquitous Language，建议 >=6 条）：")
    lines.append("  - {{术语}}：{{统一定义（避免同词多义）}}")
    lines.append("- 限界上下文（Bounded Context，建议 >=2 个）：")
    lines.append("  - C1 [Core]：{{核心业务上下文}}")
    lines.append("  - C2 [Supporting]：{{支撑协作上下文}}")
    lines.append("  - C3 [Governance]：{{治理/审计上下文（方向 C 建议）}}")
    lines.append("- 业务不变量（Domain Invariants，建议 >=3 条）：")
    lines.append("  - R1：{{业务规则，任何实现都不能违反}}")
    lines.append("- 已选上下文（M0）：")
    lines.append("  - C1 [Core]")
    lines.append("  - C2 [Supporting]")
    lines.append("")
    lines.append("## 5) ABC 业务段选择（范围轴，用户选 1 个）")
    lines.append("")
    lines.append("> A/B/C 表示业务段选择，不等同于阶段深度；阶段深度由 M0/M1/M2 决定。")
    lines.append("> 前端“完整可用 UX”仅针对已选 MVP Must 链路，不要求覆盖 Won't 或后续阶段功能。")
    lines.append("")
    lines.append("### 方向 A：选择 S0（MVP运营段）⭐ 推荐")
    lines.append(f"- 业务段范围：{str_value((profile.get('abc') or {}).get('A', 'A = S0'))}")
    lines.append(f"- 覆盖目标（门槛）：{{{{P0 覆盖率 >= {cov_a}%，至少 1 条主链路 + 1 条关键异常链路}}}}")
    lines.append("- 上下文覆盖：{{至少 1 个 Core 上下文（示例：C1 [Core]）}}")
    lines.append("- 调权策略：{{可选提升 1 项非核心能力；需同步降权 1 项并给出理由}}")
    lines.append("- Must：{{选定链路 IDs（示例：L1,L2）}}")
    lines.append("- Won't：{{未入选链路 + 非核心扩展能力}}")
    lines.append("- 技术栈建议：{{最小可验证实现方案}}")
    lines.append("- 适用：需求仍有不确定性，先证明核心价值可成立")
    lines.append("")
    lines.append("### 方向 B：选择 S0 + S1（成长期）")
    lines.append(f"- 业务段范围：{str_value((profile.get('abc') or {}).get('B', 'B = S0 + S1'))}")
    lines.append(f"- 覆盖目标（门槛）：{{{{P0 覆盖率 >= {cov_b}%，主路径链路可用且可复测}}}}")
    lines.append("- 上下文覆盖：{{Core + 至少 1 个 Supporting（示例：C1,C2）}}")
    lines.append("- 调权策略：{{可选提升 1 项非核心能力；需同步降权 1 项并给出理由}}")
    lines.append("- Must：{{选定链路 IDs（示例：L1,L2,L3）}}")
    lines.append("- Won't：{{运营深水区、重型扩展、低频场景}}")
    lines.append("- 技术栈建议：{{成熟稳定的主流方案}}")
    lines.append("- 适用：方向明确，需要可交付可试运行的核心业务能力")
    lines.append("")
    lines.append("### 方向 C：选择 S0 + S1 + S2（成熟期）")
    lines.append(f"- 业务段范围：{str_value((profile.get('abc') or {}).get('C', 'C = S0 + S1 + S2'))}")
    lines.append(f"- 覆盖目标（门槛）：{{{{P0 覆盖率 = {cov_c}%，并补齐运营治理链路}}}}")
    lines.append("- 上下文覆盖：{{所有 P0 上下文 + 治理上下文（示例：C1,C2,C3[Governance]）}}")
    lines.append("- 调权策略：{{允许调权但不降低治理能力要求}}")
    lines.append("- Must：{{方向 B 链路 + 运营治理链路 IDs（监控/审计/恢复）}}")
    lines.append("- Won't：{{超大规模优化、复杂中间件引入}}")
    lines.append("- 技术栈建议：{{方向 B 技术栈 + 运维治理工具链}}")
    lines.append("- 适用：有明确上线计划，需要稳定运行与审计能力")
    lines.append("")
    lines.append("## 6) 覆盖率与不确定性预算（硬约束）")
    lines.append("")
    lines.append("- 覆盖率公式：`已选 P0 链路数 / P0 总链路数`")
    lines.append(f"- 方向门槛：A >= {cov_a}%，B >= {cov_b}%，C = {cov_c}%（并含治理链路）")
    lines.append("- 测试绑定：每条已选 P0 链路至少绑定 1 条 E2E + 1 条异常测试")
    lines.append(f"- 不确定性预算：低置信度链路占比建议 <= {low_conf_budget}%，超出需先建“验证任务”")
    lines.append("- 变更影响：每次调整 Must/Won't 需说明任务增量、风险变化、工期影响")
    lines.append("")
    lines.append("## 7) 用户优先级调权（可选）")
    lines.append("")
    lines.append("> 当用户希望提升非核心能力优先级时，必须显式记录“提升+降权+理由+加权覆盖率目标”。")
    lines.append("")
    lines.append("- 优先级调权（Priority Overrides）：")
    lines.append("  - 提升 Lx: {{非核心功能提升原因（用户价值/业务时机/风险收益）}}")
    lines.append("  - 降权 Ly: {{被降权项及影响说明}}")
    lines.append("- 加权覆盖率目标：{{例如 80%}}")
    lines.append("- 约束：默认最多提升 1 项（可由 runtime 调整）。")
    lines.append("")
    lines.append("## 8) 方向评分矩阵（1-5 分）")
    lines.append("")
    lines.append("| 维度 | A | B | C |")
    lines.append("|---|---:|---:|---:|")
    lines.append("| 核心业务覆盖度 |  |  |  |")
    lines.append("| 用户价值 |  |  |  |")
    lines.append("| 技术风险（分高=风险低） |  |  |  |")
    lines.append("| 依赖复杂度（分高=复杂度低） |  |  |  |")
    lines.append("| 可验证性 |  |  |  |")
    lines.append("| 运营可持续性 |  |  |  |")
    lines.append("")
    lines.append("## 9) 推荐 M0 核心链路（只包含已选 Must 链路）")
    lines.append("")
    if is_frontend:
        lines.append("1. {{Lx：用户进入关键页面并完成已选主链路操作（S1→S4）}}")
        lines.append("2. {{Ly：系统处理并在界面返回成功/失败/空态/无权限等可理解反馈}}")
        lines.append("3. {{Lz：结果可回显并可复测，且仅覆盖已选 Must 链路范围}}")
    elif is_headless_cli:
        lines.append("1. {{Lx：通过命令/API 触发已选主链路动作（S1→S4）}}")
        lines.append("2. {{Ly：系统完成处理并输出成功/失败可判定结果}}")
        lines.append("3. {{Lz：结果可追溯、可复测，且仅覆盖已选 Must 链路范围}}")
    else:
        lines.append("1. {{Lx：用户或系统触发已选主链路动作（S1→S4）}}")
        lines.append("2. {{Ly：系统完成关键决策与执行交付}}")
        lines.append("3. {{Lz：输出结果可验证且仅覆盖已选 Must 链路范围}}")
    lines.append("")
    lines.append("## 10) M0 Must（由框架推导，1-3 个，仅覆盖已选链路范围）")
    lines.append("")
    if is_frontend:
        lines.append("1. {{Lx：已选主链路页面可独立完成全流程（非静态占位）}}")
        lines.append("2. {{Ly：已选异常链路具备完整反馈（加载/成功/失败/空态/无权限）}}")
        lines.append("3. {{Lz：已选链路结果可回显并可复测，刷新或重进后状态一致}}")
    else:
        lines.append("1. {{Lx：已选主链路可完整走通并可验证}}")
        lines.append("2. {{Ly：至少 1 条关键异常链路可验证}}")
        lines.append("3. {{Lz：可选治理链路（如权限/状态一致性）}}")
    lines.append("")
    lines.append("## 11) M0 Won't（由框架推导，>=3 个）")
    lines.append("")
    lines.append("1. {{未入选链路（按链路 ID 列出，如 L3/L4）}}")
    lines.append("2. {{从设想反推的非核心扩展能力 #1}}")
    lines.append("3. {{从设想反推的非核心扩展能力 #2}}")
    wont_idx = 4
    if is_frontend:
        lines.append(f"{wont_idx}. 占位式 UI 或伪交互（M0 必须真实可用）")
        wont_idx += 1
        lines.append(f"{wont_idx}. 移动端适配（M0 仅桌面端）")
        wont_idx += 1
        lines.append(f"{wont_idx}. 超出已选 MVP Must 范围的扩展页面/流程")
        wont_idx += 1
    if is_multi_module:
        lines.append(f"{wont_idx}. 跨模块复杂事务（M0 仅简单联动）")
        wont_idx += 1
    lines.append(f"{wont_idx}. 低置信度链路直接实现（需先做验证任务）")
    lines.append("")
    lines.append("## 12) 下一步")
    lines.append("")
    lines.append("- 用户确认：选择方向（A/B/C）+ 确认链路 IDs（Must/Won't）+ 覆盖率目标")
    lines.append("- 用户确认：统一语言条目、限界上下文、业务不变量、已选上下文（M0）")
    lines.append("- 如使用调权：确认提升项/降权项/理由/加权覆盖率目标")
    lines.append("- 用户确认：标记低置信度链路并决定是否先建验证任务")
    if is_frontend:
        lines.append("- 前端项目：补全 .rpi-outfile/specs/l0/ux-spec.md（仅覆盖已选 Must 链路对应 UX）")
    if is_multi_module:
        lines.append("- 多模块项目：执行 /rpi-check skeleton-init（初始化全局骨架）")
    lines.append("- 确认后回填 discovery.md，再进入 /rpi-spec expand 补全细节")
    lines.append("")
    segments = profile.get("segments", {})
    if not isinstance(segments, dict):
        segments = {}
    lines.append("## 13) 业务段清单（S0~S3）")
    lines.append("")
    lines.append(f"- S0 MVP运营段：{str_value(segments.get('S0', '核心需求业务线关键功能可用且可运营'))}")
    lines.append(f"- S1 成长期段：{str_value(segments.get('S1', '迭代优化阶段'))}")
    lines.append(f"- S2 成熟期段：{str_value(segments.get('S2', '规模化功能+性能稳定'))}")
    lines.append(f"- S3 生态持续进化段：{str_value(segments.get('S3', '构建壁垒并探索新业务线'))}")
    lines.append("")
    lines.append("## 14) M0~M2 阶段扩展（深度轴）")
    lines.append("")
    lines.append(f"- M0：{profile_phase_strategy(profile, 'M0')}")
    lines.append(f"- M1：{profile_phase_strategy(profile, 'M1')}")
    lines.append(f"- M2：{profile_phase_strategy(profile, 'M2')}")
    lines.append("")
    lines.append("## 15) 基座对齐（Epic / Spec / Milestone / Tasks）")
    lines.append("")
    lines.append("1. Epic：明确用户、问题、价值、成功指标、不做项，并记录 ABC 业务段选择结果。")
    lines.append("2. Spec：锁定架构边界、数据模型、接口契约、关键流程、非功能预算。")
    lines.append("3. Milestone：定义 M0/M1/M2 的目标、范围、交付物、退出标准、时长。")
    lines.append("4. Tasks：按阶段拆解执行任务、依赖关系、验收标准、负责人（可选）。")
    lines.append("")
    replacements = build_mvp_placeholder_replacements(
        profile=profile,
        cov_a=cov_a,
        cov_b=cov_b,
        cov_c=cov_c,
        is_frontend=is_frontend,
        is_headless_cli=is_headless_cli,
    )
    materialized_lines = materialize_mvp_lines(lines, replacements)
    mvp_file.write_text("\n".join(materialized_lines), encoding="utf-8")

    if is_frontend:
        tpl = paths.base.project_dir / ".rpi-blueprint/specs/l0/ux-spec.template.md"
        ux_spec = l0_dir / "ux-spec.md"
        if tpl.is_file() and not ux_spec.is_file():
            ux_spec.write_bytes(tpl.read_bytes())
            safe_print("[OK] 已自动生成 UX 规范骨架：.rpi-outfile/specs/l0/ux-spec.md")
            safe_print("[INFO] 请补全以下内容：")
            safe_print("   - UI 组件库名称和版本")
            safe_print("   - 自定义组件清单")
            safe_print("   - 标杆页面参考")
        if (
            not (l0_dir / "module-linkage.md").is_file()
            or not (l0_dir / "ux-flow.md").is_file()
            or not (l0_dir / "reference-module.md").is_file()
        ):
            _ = cmd_skeleton_init(paths, ["--frontend"])

    if is_multi_module:
        safe_print("")
        safe_print("[WARN] 检测到多模块项目（>=3 个模块）")
        safe_print("[INFO] 建议执行 /rpi-check skeleton-init 初始化全局骨架")
        safe_print("   - 定义模块职责边界")
        safe_print("   - 定义模块联动关系")
        safe_print("   - 定义 UX 流转规范（前端项目）")

    safe_print("[OK] MVP 骨架已生成：.rpi-outfile/specs/l0/mvp-skeleton.md")
    return 0


def cmd_expand_mvp(paths: Paths, argv: Sequence[str]) -> int:
    if not argv:
        safe_print('Usage: bash .claude/workflow/rpi.sh init deepen [idea] [platform]', stream=sys.stderr)
        return 1
    rc = cmd_create_mvp(paths, argv)
    if rc != 0:
        return rc

    idea = argv[0]
    mvp_file = paths.spec_l0_dir / "mvp-skeleton.md"
    if mvp_file.is_file():
        text = read_text_safe(mvp_file)
        if "## 用户补充输入快照" not in text and "## 8) 用户补充输入快照" not in text:
            with mvp_file.open("a", encoding="utf-8") as handle:
                handle.write("\n## 用户补充输入快照\n")
        with mvp_file.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{utc_now()}] {idea}\n")
    safe_print(f"Expanded MVP skeleton: {mvp_file}")
    return 0


def cmd_deepen_mvp(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    idea_arg = argv[0] if len(argv) >= 1 else ""
    platform_arg = argv[1] if len(argv) >= 2 else ""
    idea, platform = resolve_idea_platform(paths, idea_arg, platform_arg)

    mvp_file = paths.spec_l0_dir / "mvp-skeleton.md"
    if not mvp_file.is_file() or idea_arg or platform_arg:
        rc = cmd_create_mvp(paths, [idea, platform])
        if rc != 0:
            return rc

    snapshot_before_mutation(
        paths,
        "deepen_mvp",
        [mvp_file, paths.spec_l0_dir / "discovery.md"],
        actor="deepen-mvp",
    )
    ensure_section_snapshot(mvp_file, f"deepen: idea={idea} platform={platform}")
    cmd_save_init_summary(paths, [])

    runtime = load_runtime(paths)
    cov_a, cov_b, cov_c, low_conf_budget = mvp_coverage_policy(runtime)

    summary = read_json_obj(paths.base.state_dir / "init_summary.json")
    recommended = str_value(summary.get("recommended", "A"), "A").upper()
    if recommended not in {"A", "B", "C"}:
        recommended = "A"
    profile = infer_business_profile(idea)
    selected_must, selected_wont = profile_must_wont_map(profile, recommended)
    coverage = {"A": cov_a, "B": cov_b, "C": cov_c}[recommended]
    coverage_target = f"P0 >= {coverage}%"

    seed_discovery_conclusion(
        paths,
        idea=idea,
        direction=recommended,
        must_ids=selected_must,
        wont_ids=selected_wont,
        coverage_target=coverage_target,
        weighted_target=f"{coverage}%",
    )
    cmd_save_init_summary(paths, [])

    desc_map = collect_link_descriptions(paths)
    for lid, desc in build_profile_link_descriptions(profile).items():
        if lid not in desc_map:
            desc_map[lid] = desc
    must_details = render_link_details(selected_must, desc_map)
    wont_details = render_link_details([x for x in selected_wont if re.fullmatch(r"L[1-9][0-9]*", x)], desc_map)
    auto_confirmation = (
        f"确认方向{recommended}（业务段选择）;"
        f"Must链路: {', '.join(selected_must)}；"
        f"Won't链路: {', '.join(selected_wont[:3])}；"
        f"加权覆盖率: {coverage}%"
    )

    abc_map = profile.get("abc", {})
    if not isinstance(abc_map, dict):
        abc_map = {}
    direction_coverage_map = {"A": cov_a, "B": cov_b, "C": cov_c}
    safe_print("[OK] MVP 想法深化完成")
    safe_print("- ABC 业务段选择：")
    safe_print(f"  - {str_value(abc_map.get('A', 'A = S0'))}")
    safe_print(f"  - {str_value(abc_map.get('B', 'B = S0 + S1'))}")
    safe_print(f"  - {str_value(abc_map.get('C', 'C = S0 + S1 + S2（S3 路线图）'))}")
    safe_print("- ABC 差异对照（范围 + Must/Won't + 覆盖门槛）：")
    for direction in ("A", "B", "C"):
        d_must, d_wont = profile_must_wont_map(profile, direction)
        d_must = d_must[:3]
        d_wont = d_wont[:3]
        cov_value = direction_coverage_map.get(direction, cov_a)
        cov_expr = f"= {cov_value}%" if direction == "C" else f">= {cov_value}%"
        safe_print(f"  - {direction} | 范围：{profile_segment_scope(profile, direction)}")
        safe_print(f"    覆盖门槛：P0 {cov_expr}")
        safe_print(f"    Must 候选：{', '.join(d_must) if d_must else '无'}")
        safe_print(f"    Won't 候选：{', '.join(d_wont) if d_wont else '无'}")
    safe_print(f"- 推荐方向：{recommended}（业务段范围：{profile_segment_scope(profile, recommended)}）")
    safe_print(f"- 覆盖门槛 A/B/C = {cov_a}%/{cov_b}%/{cov_c}%")
    safe_print(f"- M0~M2 阶段扩展：M0={profile_phase_strategy(profile, 'M0')}；M1={profile_phase_strategy(profile, 'M1')}；M2={profile_phase_strategy(profile, 'M2')}")
    safe_print(f"- 推荐 Must：{', '.join(selected_must)}")
    safe_print(f"- 推荐 Won't：{', '.join(selected_wont[:3])}")
    for row in must_details:
        safe_print(f"- Must 详情：{row}")
    for row in wont_details:
        safe_print(f"- Won't 详情：{row}")
    safe_print(f"- 低置信度链路预算：<= {low_conf_budget}%")
    safe_print("- 已回填 discovery 结论字段（方向/覆盖率/M0 Must/Won't）")
    safe_print(f"- 可直接执行：/rpi-spec expand {auto_confirmation}")
    safe_print("- 也可只执行：/rpi-spec expand（自动读取当前确认）")
    return 0


def cmd_spec_expand(paths: Paths, argv: Sequence[str]) -> int:
    ensure_layout(paths)
    runtime = load_runtime(paths)
    cov_a, cov_b, cov_c, _ = mvp_coverage_policy(runtime)
    if not argv:
        confirmation, reason = build_auto_confirmation(paths, cov_a, cov_b, cov_c)
        safe_print("[INFO] spec expand 未提供确认文本，已自动生成确认输入")
        safe_print(f"- 来源：{reason}")
        safe_print(f"- 输入：{confirmation}")
    else:
        confirmation = " ".join(argv).strip()

    idea, platform = resolve_idea_platform(paths)
    ensure_l0_files(paths)
    snapshot_before_mutation(
        paths,
        "spec_expand",
        [
            paths.spec_l0_dir / "discovery.md",
            paths.spec_l0_dir / "epic.md",
            paths.spec_l0_dir / "spec.md",
            paths.spec_l0_dir / "milestones.md",
            paths.spec_l0_dir / "tasks.md",
            paths.spec_l0_dir / "mvp-skeleton.md",
            paths.base.spec_dir / "phases" / "m0.md",
            paths.base.spec_dir / "phases" / "m1.md",
            paths.base.spec_dir / "phases" / "m2.md",
            paths.base.config_dir / "gates.json",
        ],
        actor="spec-expand",
    )

    direction = detect_direction_from_text(confirmation, infer_default_direction(paths))
    coverage_map = {"A": cov_a, "B": cov_b, "C": cov_c}
    coverage = coverage_map.get(direction, cov_a)
    coverage_target = f"P0 >= {coverage}%"

    weighted_match = re.search(
        r"(?:加权覆盖率目标|加权覆盖率|Weighted Coverage(?: Target)?)\s*[:：]?\s*(\d{1,3})\s*%",
        confirmation,
        flags=re.IGNORECASE,
    )
    weighted_target = f"{weighted_match.group(1)}%" if weighted_match else f"{coverage}%"

    profile = infer_business_profile(idea)
    requested_ids = extract_link_ids(confirmation)
    requested_must_items = extract_labeled_items(confirmation, "must")
    requested_wont_items = extract_labeled_items(confirmation, "wont")
    requested_must_ids = [
        str(x).strip().upper()
        for x in requested_must_items
        if re.fullmatch(r"L[1-9][0-9]*", str(x).strip(), flags=re.IGNORECASE)
    ]
    mvp_file = paths.spec_l0_dir / "mvp-skeleton.md"
    mvp_ids = extract_link_ids(read_text_safe(mvp_file))
    all_ids = mvp_ids if mvp_ids else ["L1", "L2", "L3", "L4"]

    if requested_must_ids:
        must_ids = requested_must_ids[:3]
    elif requested_ids and not requested_wont_items:
        must_ids = requested_ids[:3]
    else:
        must_defaults, _ = profile_must_wont_map(profile, direction)
        must_ids = must_defaults[:3] if must_defaults else ["L1", "L2"]

    if requested_wont_items:
        wont_ids = []
        for item in requested_wont_items:
            token = str(item).strip()
            if not token:
                continue
            if re.fullmatch(r"L[1-9][0-9]*", token, flags=re.IGNORECASE):
                token = token.upper()
            if token in must_ids or token in wont_ids:
                continue
            wont_ids.append(token)
    else:
        _, wont_defaults = profile_must_wont_map(profile, direction)
        wont_ids = [x for x in wont_defaults if x not in must_ids]
        if not wont_ids:
            wont_ids = [x for x in all_ids if x not in must_ids]
    if not wont_ids:
        wont_ids = ["L4", "非核心扩展", "低优先级能力"]
    if spec_state_tool.count_chain_refs(wont_ids) < 1:
        for chain in all_ids:
            if chain not in must_ids and chain not in wont_ids:
                wont_ids.insert(0, chain)
                break
        if spec_state_tool.count_chain_refs(wont_ids) < 1 and "L4" not in must_ids:
            wont_ids.insert(0, "L4")
    while len(wont_ids) < 3:
        extra = ["非核心扩展", "低优先级能力", "二期集成"]
        for item in extra:
            if item not in wont_ids:
                wont_ids.append(item)
            if len(wont_ids) >= 3:
                break

    seed_discovery_conclusion(
        paths,
        idea=idea,
        direction=direction,
        must_ids=must_ids,
        wont_ids=wont_ids[:3],
        coverage_target=coverage_target,
        weighted_target=weighted_target,
    )
    materialize_l0_docs(
        paths=paths,
        idea=idea,
        platform=platform,
        profile=profile,
        direction=direction,
        must_ids=must_ids,
        wont_ids=wont_ids[:3],
        coverage_target=coverage_target,
        weighted_target=weighted_target,
    )

    ensure_section_snapshot(paths.spec_l0_dir / "discovery.md", confirmation)
    for name in ("epic.md", "spec.md", "milestones.md", "tasks.md"):
        ensure_section_snapshot(paths.spec_l0_dir / name, confirmation)
    ensure_section_snapshot(paths.spec_l0_dir / "mvp-skeleton.md", confirmation)
    is_frontend, _ = detect_project_surface(idea, platform, "")
    materialize_phase_specs(
        paths=paths,
        idea=idea,
        platform=platform,
        direction=direction,
        must_ids=must_ids,
        wont_ids=wont_ids[:3],
        coverage_target=coverage_target,
        weighted_target=weighted_target,
        is_frontend=is_frontend,
    )
    ensure_section_snapshot(paths.base.spec_dir / "phases" / "m1.md", confirmation)
    ensure_section_snapshot(paths.base.spec_dir / "phases" / "m2.md", confirmation)
    ensure_phase_verify_mapping(paths, is_frontend=is_frontend)

    cmd_save_init_summary(paths, [])
    current_task = read_json_obj(paths.base.current_task_file)
    if "phase" not in current_task or not str_value(current_task.get("phase", ""), ""):
        current_task["phase"] = str_value(read_json_obj(paths.base.phase_file).get("phase", "M0"), "M0")
    contract_file = task_flow.write_portable_contract(
        paths.base,
        current_task,
        transition="spec_expanded",
        reason=confirmation,
    )
    append_event(
        paths,
        {
            "event": "portable_contract_refresh",
            "source": "spec_expand",
            "phase": str_value(current_task.get("phase", "M0"), "M0"),
            "portable_contract": str(contract_file),
        },
    )
    desc_map = collect_link_descriptions(paths)
    for lid, desc in build_profile_link_descriptions(profile).items():
        if lid not in desc_map:
            desc_map[lid] = desc
    must_details = render_link_details(must_ids, desc_map)
    wont_detail_ids = [x for x in wont_ids[:3] if re.fullmatch(r"L[1-9][0-9]*", x)]
    wont_details = render_link_details(wont_detail_ids, desc_map)
    safe_print("[OK] Spec 扩展完成")
    safe_print("- 已更新文件：discovery.md, epic.md, spec.md, milestones.md, tasks.md, phases/m1.md, phases/m2.md, gates.json(verify)")
    safe_print(f"- 方向：{direction}（业务段范围：{profile_segment_scope(profile, direction)}）")
    safe_print(f"- 覆盖率目标：{coverage_target}，加权覆盖率目标：{weighted_target}")
    safe_print(f"- 阶段扩展：M0={profile_phase_strategy(profile, 'M0')}；M1={profile_phase_strategy(profile, 'M1')}；M2={profile_phase_strategy(profile, 'M2')}")
    safe_print(f"- M0 Must：{', '.join(must_ids)}")
    safe_print(f"- M0 Won't：{', '.join(wont_ids[:3])}")
    for row in must_details:
        safe_print(f"- Must 详情：{row}")
    for row in wont_details:
        safe_print(f"- Won't 详情：{row}")
    safe_print(f"- 可移植契约：{contract_file}")
    safe_print("- 下一步：/rpi-task start <task_id>")
    return 0


def cmd_resolve_context_refs(paths: Paths, argv: Sequence[str]) -> int:
    if not argv:
        safe_print(
            "Internal usage: resolve-context-refs <implement|check|debug> [spec_refs_csv] [M0|M1|M2] [task_id] (invoked by /rpi-task start)",
            stream=sys.stderr,
        )
        return 1

    mode = argv[0]
    extra_refs_csv = argv[1] if len(argv) > 1 else ""
    phase_hint = argv[2] if len(argv) > 2 else ""
    task_id_hint = argv[3] if len(argv) > 3 else ""

    phase_hint = normalize_phase(phase_hint, "")
    if not phase_hint:
        phase_hint = normalize_phase(str_value(read_json_obj(paths.base.phase_file).get("phase", "M0"), "M0"), "M0")

    refs: List[str] = []
    ref_keys: List[str] = []

    def dedupe_push(value: str) -> None:
        key = value.split("#", 1)[0]
        for i, k in enumerate(ref_keys):
            if k == key:
                if "#" not in refs[i] and "#" in value:
                    refs[i] = value
                return
        refs.append(value)
        ref_keys.append(key)

    def path_exists_for_ref(ref: str) -> bool:
        raw = ref.split("#", 1)[0]
        if not raw:
            return False
        return (paths.base.project_dir / raw).is_file()

    def normalize_phase_ref(ref: str) -> str:
        path = ref.split("#", 1)[0]
        anchor = ""
        if "#" in ref:
            anchor = "#" + ref.split("#", 1)[1]
        if re.fullmatch(r"\.rpi-outfile/specs/phases/m[0-2]\.md", path):
            path = f".rpi-outfile/specs/phases/{phase_hint.lower()}.md"
        return f"{path}{anchor}"

    def load_manifest(path: Path) -> None:
        if not path.is_file():
            return
        for line in read_text_safe(path).splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            ref = str_value(row.get("file", ""), "")
            if not ref:
                continue
            ref = normalize_phase_ref(ref)
            if path_exists_for_ref(ref):
                dedupe_push(ref)

    task_id_hint = task_flow.normalize_task_id(task_id_hint) if task_id_hint else ""
    if not task_id_hint:
        current = read_json_obj(paths.base.current_task_file)
        task_id_hint = task_flow.normalize_task_id(str_value(current.get("task_id", ""), ""))

    if task_id_hint:
        candidates = [
            paths.base.project_dir / f".rpi-outfile/specs/tasks/{task_id_hint}/{mode}.jsonl",
            paths.base.project_dir / f".rpi-outfile/specs/tasks/{task_id_hint}/context/{mode}.jsonl",
            paths.base.project_dir / f".rpi-outfile/specs/tasks/{task_id_hint.lower()}/{mode}.jsonl",
            paths.base.project_dir / f".rpi-outfile/specs/tasks/{task_id_hint.lower()}/context/{mode}.jsonl",
        ]
        for m in candidates:
            load_manifest(m)

    load_manifest(paths.base.project_dir / f".claude/workflow/context/{mode}.jsonl")

    if extra_refs_csv:
        for ref in [x.strip() for x in extra_refs_csv.split(",") if x.strip()]:
            ref = normalize_phase_ref(ref)
            if path_exists_for_ref(ref):
                dedupe_push(ref)

    if not refs:
        master = paths.base.project_dir / ".rpi-outfile/specs/00_master_spec.md"
        if master.is_file():
            refs.append(".rpi-outfile/specs/00_master_spec.md")
            discovery = paths.base.project_dir / ".rpi-outfile/specs/l0/discovery.md"
            if discovery.is_file():
                refs.append(".rpi-outfile/specs/l0/discovery.md")
            phase_file = paths.base.project_dir / f".rpi-outfile/specs/phases/{phase_hint.lower()}.md"
            if phase_file.is_file():
                refs.append(f".rpi-outfile/specs/phases/{phase_hint.lower()}.md")
        elif extra_refs_csv:
            refs = [extra_refs_csv]

    safe_print(",".join(refs))
    return 0


def cmd_ux_check(paths: Paths, argv: Sequence[str]) -> int:
    quiet = False
    for token in argv:
        if token == "--quiet":
            quiet = True
            continue
        if token in {"--help", "-h"}:
            safe_print("Usage: bash .claude/workflow/rpi.sh check ux [--quiet]")
            safe_print("")
            safe_print("Check frontend UX compliance against .rpi-outfile/specs/l0/ux-spec.md.")
            return 0
        safe_print(f"Unknown argument: {token}", stream=sys.stderr)
        return 1

    def log(text: str = "") -> None:
        if not quiet:
            safe_print(text)

    ux_spec = paths.spec_l0_dir / "ux-spec.md"
    reference_module = paths.spec_l0_dir / "reference-module.md"
    current_task = paths.base.current_task_file
    src_dir = paths.base.project_dir / "src"

    log("========================================")
    log("UX 合规性检查")
    log("========================================")
    log("")

    if not ux_spec.is_file():
        log("[ERROR] 缺少 UX 规范文件")
        log("")
        log("请先完成以下步骤：")
        log("1. 根据模板补全 .rpi-outfile/specs/l0/ux-spec.md")
        log("2. 重新执行检查")
        log("")
        log("模板位置：.rpi-blueprint/specs/l0/ux-spec.template.md")
        return 1

    if not current_task.is_file():
        log("[WARN] 无活动任务，跳过检查")
        return 0

    task_id = str_value(read_json_obj(current_task).get("task_id", "unknown"), "unknown")
    if task_id in {"unknown", "null", ""}:
        log("[WARN] 无活动任务，跳过检查")
        return 0

    log(f"检查任务：{task_id}")
    log("")

    pass_count = 0
    fail_count = 0
    issues: List[str] = []

    front_files: List[Path] = []
    if src_dir.is_dir():
        for p in src_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".vue", ".jsx", ".tsx"}:
                front_files.append(p)

    log("检查 1：禁止的 UX 实现...")
    same_level_form_count = 0
    for f in front_files:
        text = read_text_safe(f)
        has_table = bool(re.search(r"<(el-table|a-table|n-data-table|van-list)", text))
        has_inline_form = bool(re.search(r"<(el-form|a-form|n-form)", text))
        has_modal = bool(re.search(r"<(el-dialog|el-drawer|a-modal|a-drawer|n-modal|n-drawer)", text))
        if has_table and has_inline_form and not has_modal:
            same_level_form_count += 1
            issues.append(f"同级表单块: {f}")
    if same_level_form_count > 0:
        fail_count += 1
    else:
        pass_count += 1

    log("检查 2：删除操作二次确认...")
    delete_without_confirm = 0
    for f in front_files:
        text = read_text_safe(f)
        if re.search(r"(delete|remove|del|destroy)", text, flags=re.IGNORECASE):
            has_confirm = bool(
                re.search(
                    r"(MessageBox\.confirm|Modal\.confirm|confirm\(|ElMessageBox|useConfirm|showConfirmDialog)",
                    text,
                )
            )
            if not has_confirm:
                delete_without_confirm += 1
                issues.append(f"删除操作缺少二次确认: {f}")
    if delete_without_confirm > 0:
        fail_count += 1
    else:
        pass_count += 1

    log("检查 3：表单提交加载状态...")
    submit_without_loading = 0
    for f in front_files:
        text = read_text_safe(f)
        if re.search(r"(handleSubmit|onSubmit|submitForm|@submit|@click.*save|@click.*submit)", text):
            has_loading = bool(re.search(r"(loading|submitting|isLoading|btnLoading|submitLoading)", text))
            if not has_loading:
                submit_without_loading += 1
                issues.append(f"表单提交缺少加载状态: {f}")
    if submit_without_loading > 0:
        fail_count += 1
    else:
        pass_count += 1

    log("检查 4：标杆模块参考...")
    if reference_module.is_file():
        pass_count += 1
    else:
        log("[WARN] 建议：创建标杆模块参考文档")

    log("检查 5：UX 规范完整性...")
    ux_text = read_text_safe(ux_spec)
    missing_sections = [s for s in ["禁止行为", "表格", "表单", "按钮"] if s not in ux_text]
    if missing_sections:
        fail_count += 1
        issues.append(f"UX 规范缺少关键章节: {' '.join(missing_sections)}")
    else:
        pass_count += 1

    log("")
    log("========================================")
    log("检查结果")
    log("========================================")
    log("")
    if fail_count == 0:
        log("[OK] UX 合规性检查通过")
        log("")
        log(f"通过 {pass_count} 个检查")
        return 0

    log(f"[ERROR] 发现 {fail_count} 个问题：")
    log("")
    for idx, item in enumerate(issues, start=1):
        log(f"{idx}. {item}")
    log("")
    log(f"通过 {pass_count} 个检查")
    log("")
    log("请修复以上问题后重新检查")
    log("")
    log("参考：")
    log("- UX 规范：.rpi-outfile/specs/l0/ux-spec.md")
    if reference_module.is_file():
        log("- 标杆模块：.rpi-outfile/specs/l0/reference-module.md")
    return 1


def dispatch(paths: Paths, subcommand: str, argv: Sequence[str]) -> int:
    if subcommand == "harness":
        return cmd_harness(paths, argv)
    if subcommand == "run-evals":
        return cmd_run_evals(paths, argv)
    if subcommand == "suggest-gates":
        return cmd_suggest_gates(paths, argv)
    if subcommand == "anti-entropy":
        return cmd_anti_entropy(paths, argv)
    if subcommand == "build-audit-pack":
        return cmd_build_audit_pack(paths, argv)
    if subcommand == "audit-report":
        return cmd_audit_report(paths, argv)
    if subcommand == "auto-rpi":
        return cmd_auto_rpi(paths, argv)
    if subcommand == "a2a-review":
        return cmd_a2a_review(paths, argv)
    if subcommand == "agent-memory-update":
        return cmd_agent_memory_update(paths, argv)
    if subcommand == "abort-task":
        return cmd_abort_task(paths, argv)
    if subcommand == "pause-task":
        return cmd_pause_task(paths, argv)
    if subcommand == "resume-task":
        return cmd_resume_task(paths, argv)
    if subcommand == "query-logs":
        return cmd_query_logs(paths, argv)
    if subcommand == "recover":
        return cmd_recover(paths, argv)
    if subcommand == "trace-grade":
        return cmd_trace_grade(paths, argv)
    if subcommand == "check-entry":
        return cmd_check_entry_integrity(paths, argv)
    if subcommand == "check-theory":
        return cmd_check_theory(paths, argv)
    if subcommand == "check-skeleton":
        return cmd_check_skeleton(paths, argv)
    if subcommand == "skeleton-init":
        return cmd_skeleton_init(paths, argv)
    if subcommand == "evaluate-requirement":
        return cmd_evaluate_requirement(paths, argv)
    if subcommand == "save-init-summary":
        return cmd_save_init_summary(paths, argv)
    if subcommand == "switch-phase":
        return cmd_switch_phase(paths, argv)
    if subcommand == "bootstrap-gate":
        return cmd_bootstrap_gate(paths, argv)
    if subcommand == "create-mvp":
        return cmd_create_mvp(paths, argv)
    if subcommand == "expand-mvp":
        return cmd_expand_mvp(paths, argv)
    if subcommand == "deepen-mvp":
        return cmd_deepen_mvp(paths, argv)
    if subcommand == "spec-expand":
        return cmd_spec_expand(paths, argv)
    if subcommand == "resolve-context-refs":
        return cmd_resolve_context_refs(paths, argv)
    if subcommand == "ux-check":
        return cmd_ux_check(paths, argv)
    safe_print(f"Unknown automation subcommand: {subcommand}", stream=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("subcommand")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parser.add_argument("--project-dir", dest="project_dir", default="")
    return parser


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    ns = parser.parse_args(list(argv))
    script_dir = Path(__file__).resolve().parent
    project_dir = Path(ns.project_dir).resolve() if ns.project_dir else resolve_project_dir(script_dir)
    paths = build_paths(project_dir)
    return dispatch(paths, ns.subcommand, ns.args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
