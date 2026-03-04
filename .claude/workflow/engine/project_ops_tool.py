#!/usr/bin/env python3
"""Project ops engine for env/doctor/bootstrap flows."""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import artifact_recovery
import guardrails_tool as guardrails
import task_flow_tool as task_flow


def resolve_project_dir(start: Path) -> Path:
    cur = start.resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / ".claude" / "workflow").is_dir():
            return cand
    return cur


def platform_family() -> str:
    plat = sys.platform.lower()
    if plat.startswith("win"):
        return "windows"
    if plat.startswith("darwin"):
        return "macos"
    if plat.startswith("linux"):
        return "linux"
    return "unknown"


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


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


def json_get_path(data: Dict[str, Any], dotted: str, default: Any = "") -> Any:
    cur: Any = data
    for token in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        if token not in cur:
            return default
        cur = cur[token]
    return default if cur is None else cur


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


def write_idle_task(path: Path, phase: str = "M0") -> None:
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
        "guardrails": {"precode": {"status": "unknown", "signature": "", "verified_at": "", "note": ""}},
        "created_at": "",
        "last_updated_at": utc_now(),
        "owner": "",
    }
    write_json(path, payload)


def ensure_layout(paths: Paths) -> None:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.spec_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    if not paths.phase_file.exists():
        write_json(paths.phase_file, {"phase": "M0", "spec_ratio": "6:4", "updated_at": utc_now()})
    if not paths.current_task_file.exists():
        write_idle_task(paths.current_task_file, "M0")
    if not paths.task_stack_file.exists():
        write_json(paths.task_stack_file, [])
    if not paths.runtime_file.exists():
        write_json(paths.runtime_file, default_runtime())
    if not paths.event_log.exists():
        paths.event_log.touch()
    if not paths.gate_log.exists():
        paths.gate_log.touch()


def run_python_capture(argv: Sequence[str], cwd: Path, quiet: bool = False) -> Tuple[int, str]:
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
        list(argv),
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
    )
    out = decode_output(proc.stdout)
    if not quiet and out:
        print(out.rstrip("\n"))
    return int(proc.returncode), out


def resolve_python_executable() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def run_task_flow_capture(paths: Paths, subcommand: str, args: Sequence[str]) -> Tuple[int, str]:
    engine = paths.workflow_dir / "engine" / "task_flow_tool.py"
    if not engine.exists():
        return 127, f"missing engine: {engine}"
    exe = resolve_python_executable()
    return run_python_capture(
        [exe, str(engine), "--project-dir", str(paths.project_dir), subcommand, *list(args)],
        paths.project_dir,
        quiet=True,
    )


def run_automation_capture(paths: Paths, subcommand: str, args: Sequence[str]) -> Tuple[int, str]:
    engine = paths.workflow_dir / "engine" / "automation_tool.py"
    if not engine.exists():
        return 127, f"missing engine: {engine}"
    exe = resolve_python_executable()
    return run_python_capture(
        [exe, str(engine), "--project-dir", str(paths.project_dir), subcommand, *list(args)],
        paths.project_dir,
        quiet=True,
    )


def detect_package_manager() -> str:
    for item in ["apt-get", "dnf", "yum", "apk", "pacman", "zypper", "brew"]:
        if shutil.which(item):
            if item == "apt-get":
                return "apt"
            return item
    return ""


def pkg_name_for_dep(dep: str, manager: str) -> str:
    if dep == "jq":
        return "jq"
    if dep == "rg":
        if manager in {"apt", "dnf", "yum", "apk", "pacman", "zypper", "brew"}:
            return "ripgrep"
        return ""
    if dep == "python3":
        if manager in {"apt", "dnf", "yum", "apk", "pacman", "zypper", "brew"}:
            return "python3"
        return ""
    return ""


def manual_hint_for_dep(dep: str, family: str) -> str:
    if dep == "jq":
        return jq_install_hint()
    if dep == "rg":
        if family == "windows":
            if shutil.which("winget"):
                return "winget install BurntSushi.ripgrep.MSVC"
            if shutil.which("choco"):
                return "choco install ripgrep -y"
            if shutil.which("scoop"):
                return "scoop install ripgrep"
            return "Install ripgrep for Windows and ensure 'rg' is in PATH."
        if family == "macos":
            return "brew install ripgrep"
        if family == "linux":
            return "Install ripgrep with your distro package manager."
        return "Install ripgrep and ensure 'rg' is in PATH."
    if dep == "python3":
        if family == "windows":
            if shutil.which("winget"):
                return "winget install Python.Python.3"
            if shutil.which("choco"):
                return "choco install python -y"
            if shutil.which("scoop"):
                return "scoop install python"
            return "Install Python 3 for Windows and ensure 'python3' (or 'python') is in PATH."
        if family == "macos":
            return "brew install python"
        if family == "linux":
            return "Install python3 with your distro package manager."
        return "Install python3 and ensure it is in PATH."
    return f"Install {dep} and ensure it is in PATH."


def install_dep_set(manager: str, pkgs: Sequence[str]) -> bool:
    if not pkgs:
        return True
    if manager == "apt":
        cmds = [["apt-get", "update", "-y"], ["apt-get", "install", "-y", *pkgs]]
    elif manager in {"dnf", "yum"}:
        cmds = [[manager, "install", "-y", *pkgs]]
    elif manager == "apk":
        cmds = [["apk", "add", *pkgs]]
    elif manager == "pacman":
        cmds = [["pacman", "-Sy", "--noconfirm", *pkgs]]
    elif manager == "zypper":
        cmds = [["zypper", "--non-interactive", "install", *pkgs]]
    elif manager == "brew":
        cmds = [["brew", "install", *pkgs]]
    else:
        return False

    geteuid = getattr(os, "geteuid", None)
    is_root = False
    if callable(geteuid):
        try:
            is_root = int(geteuid()) == 0
        except Exception:
            is_root = False
    use_sudo = shutil.which("sudo") is not None and not is_root
    for cmd in cmds:
        full = (["sudo"] + cmd) if use_sudo and cmd[0] != "brew" else cmd
        if subprocess.run(full, check=False).returncode != 0:
            return False
    return True


def cmd_check_environment(paths: Paths, args: argparse.Namespace) -> int:
    family = platform_family()
    shell_name = os.environ.get("SHELL", "unknown")
    jq_path = shutil.which("jq") or ""
    rg_path = shutil.which("rg") or ""
    python_path = shutil.which("python3") or shutil.which("python") or ""
    auto_fix_attempted = "false"
    auto_fix_result = "skipped"
    manual_action_required = "false"

    missing_required: List[str] = []
    missing_recommended: List[str] = []

    def collect_missing() -> None:
        missing_required.clear()
        missing_recommended.clear()
        if not shutil.which("jq"):
            missing_required.append("jq")
        if not shutil.which("python3") and not shutil.which("python"):
            missing_required.append("python3")
        if not shutil.which("rg"):
            missing_recommended.append("rg")

    collect_missing()

    print("[RPI Env Check]")
    print(f"platform={family}")
    print(f"shell={shell_name}")
    print(f"project_dir={paths.project_dir}")
    if jq_path:
        print(f"jq=installed ({jq_path})")
    else:
        print("jq=missing")
        print(f"jq_install_hint={jq_install_hint()}")
    if rg_path:
        print(f"rg=installed ({rg_path})")
    else:
        print("rg=missing [recommended, auto-fallback to grep -E]")
    if python_path:
        print(f"python=installed ({python_path}) [required]")
    else:
        print("python=missing [required]")

    if args.auto_fix and (missing_required or missing_recommended):
        auto_fix_attempted = "true"
        if family == "windows":
            auto_fix_result = "skipped_windows_manual"
            manual_action_required = "true"
        else:
            manager = detect_package_manager()
            if not manager:
                auto_fix_result = "failed_no_package_manager"
            else:
                pkgs: List[str] = []
                for dep in [*missing_required, *missing_recommended]:
                    pkg = pkg_name_for_dep(dep, manager)
                    if pkg:
                        pkgs.append(pkg)
                if not pkgs:
                    auto_fix_result = "failed_no_package_mapping"
                elif install_dep_set(manager, pkgs):
                    auto_fix_result = "success"
                else:
                    auto_fix_result = "failed_install_command"
        jq_path = shutil.which("jq") or ""
        rg_path = shutil.which("rg") or ""
        collect_missing()

    print(f"auto_fix_attempted={auto_fix_attempted}")
    print(f"auto_fix_result={auto_fix_result}")
    for dep in missing_required:
        print(f"required_missing={dep}")
        print(f"manual_install_hint_{dep}={manual_hint_for_dep(dep, family)}")
    for dep in missing_recommended:
        print(f"recommended_missing={dep}")
        print(f"manual_install_hint_{dep}={manual_hint_for_dep(dep, family)}")

    gate_runtime_missing = "none"
    gates_file = paths.config_dir / "gates.json"
    if jq_path and gates_file.exists():
        cfg = read_json_obj(gates_file)
        commands = cfg.get("commands", {})
        if isinstance(commands, dict):
            missing_bins: List[str] = []
            for val in commands.values():
                cmd = str_value(val, "").strip()
                if not cmd or cmd == "__REQUIRED__":
                    continue
                bin_name = cmd.split(" ", 1)[0]
                if bin_name in {"bash", "sh", "npm", "pnpm", "yarn", "python", "python3", "go", "cargo", "mvn", "gradle", "docker", "make", "pytest", "uv", "node"}:
                    if not shutil.which(bin_name):
                        missing_bins.append(bin_name)
            if missing_bins:
                gate_runtime_missing = ",".join(sorted(set(missing_bins))) + " (manual install required before /rpi-gates run)"
    print(f"gate_runtime_missing={gate_runtime_missing}")

    env_ready = "false" if missing_required else "true"
    if missing_required and family == "windows":
        manual_action_required = "true"
    print(f"env_ready={env_ready}")
    print(f"manual_action_required={manual_action_required}")

    if args.require_jq:
        if env_ready == "true":
            return 0
        if manual_action_required == "true":
            return 3
        return 2
    return 0


def cmd_init_state(paths: Paths, _args: argparse.Namespace) -> int:
    ensure_layout(paths)
    print(f"Initialized workflow output under: {paths.output_dir}")
    print(f"Phase file: {paths.phase_file}")
    print(f"Task file: {paths.current_task_file}")
    return 0


def parse_percent_value(text: str) -> Optional[int]:
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


def is_placeholder_text(text: str) -> bool:
    v = str(text or "").strip()
    if not v:
        return True
    if "{{" in v and "}}" in v:
        return True
    if v.lower() in {"tbd", "n/a", "todo", "待确认", "待输入", "-"}:
        return True
    return False


def extract_chain_ids(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in items:
        for num in re.findall(r"(?<![A-Za-z0-9])L([1-9][0-9]*)", str(raw or ""), flags=re.IGNORECASE):
            key = f"L{int(num)}"
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def markdown_materialized(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
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


def score_ratio(passed: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round((passed * 100.0) / float(total)))


def evaluate_artifact_quality(paths: Paths) -> Dict[str, Any]:
    discovery = paths.project_dir / ".rpi-outfile/specs/l0/discovery.md"
    spec = paths.project_dir / ".rpi-outfile/specs/l0/spec.md"
    tasks = paths.project_dir / ".rpi-outfile/specs/l0/tasks.md"
    if not (discovery.exists() and spec.exists() and tasks.exists()):
        return {"available": False, "reason": "core spec files missing"}

    state_file = paths.state_dir / "spec" / "state.json"
    if guardrails.build_spec_state(paths.project_dir, quiet=True) != 0 or not state_file.is_file():
        return {"available": False, "reason": "spec state build failed"}

    state = read_json_obj(state_file)
    if not state:
        return {"available": False, "reason": "spec state invalid"}

    fields = json_get_path(state, "discovery.fields", {})
    sections = json_get_path(state, "discovery.sections", {})
    tasks_state = json_get_path(state, "tasks", {})
    spec_state = json_get_path(state, "spec", {})
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(sections, dict):
        sections = {}
    if not isinstance(tasks_state, dict):
        tasks_state = {}
    if not isinstance(spec_state, dict):
        spec_state = {}

    direction_choice = str_value(fields.get("direction_choice", ""), "")
    coverage_target = str_value(fields.get("coverage_target", ""), "")
    weighted_target = str_value(fields.get("weighted_coverage_target", ""), "")
    m0_must = fields.get("m0_must", [])
    m0_wont = fields.get("m0_wont", [])
    success_metrics = fields.get("success_metrics", [])
    if not isinstance(m0_must, list):
        m0_must = []
    if not isinstance(m0_wont, list):
        m0_wont = []
    if not isinstance(success_metrics, list):
        success_metrics = []

    must_chain_ids = extract_chain_ids([str(x) for x in m0_must])
    wont_chain_ids = extract_chain_ids([str(x) for x in m0_wont])

    completeness_checks: List[Tuple[str, bool]] = [
        ("goal", not is_placeholder_text(fields.get("goal", ""))),
        ("target_user", not is_placeholder_text(fields.get("target_user", ""))),
        ("high_freq_scenario", not is_placeholder_text(fields.get("high_freq_scenario", ""))),
        ("time_window", not is_placeholder_text(fields.get("time_window", ""))),
        ("direction", direction_choice in {"A", "B", "C"}),
        ("coverage_target", parse_percent_value(coverage_target) is not None),
        ("m0_must_count", 1 <= len(m0_must) <= 3),
        ("m0_wont_count", len(m0_wont) >= 3),
        ("success_metrics_count", 2 <= len(success_metrics) <= 4),
        ("facts_count", int(json_get_path(sections, "facts_count", 0)) >= 1),
        ("assumptions_count", int(json_get_path(sections, "assumptions_count", 0)) >= 1),
        ("open_questions_count", int(json_get_path(sections, "open_questions_count", 0)) >= 1),
    ]

    runtime = read_json_obj(paths.runtime_file)
    ddd_min_glossary = int_value(runtime.get("ddd_min_glossary_terms", 6), 6)
    ddd_min_contexts = int_value(runtime.get("ddd_min_bounded_contexts", 2), 2)
    ddd_min_invariants = int_value(runtime.get("ddd_min_invariants", 3), 3)
    ubiquitous_language = fields.get("ubiquitous_language", [])
    bounded_contexts = fields.get("bounded_contexts", [])
    domain_invariants = fields.get("domain_invariants", [])
    selected_contexts = fields.get("m0_contexts", [])
    if not isinstance(ubiquitous_language, list):
        ubiquitous_language = []
    if not isinstance(bounded_contexts, list):
        bounded_contexts = []
    if not isinstance(domain_invariants, list):
        domain_invariants = []
    if not isinstance(selected_contexts, list):
        selected_contexts = []

    semantic_checks: List[Tuple[str, bool]] = [
        ("must_has_chain", len(must_chain_ids) >= 1),
        ("wont_has_chain", len(wont_chain_ids) >= 1),
        ("coverage_parseable", parse_percent_value(coverage_target) is not None),
        ("weighted_parseable_or_empty", (not weighted_target) or parse_percent_value(weighted_target) is not None),
        ("direction_chain_density", direction_choice not in {"B", "C"} or len(must_chain_ids) >= 2),
        ("ddd_glossary", len(ubiquitous_language) >= ddd_min_glossary),
        ("ddd_contexts", len(bounded_contexts) >= ddd_min_contexts),
        ("ddd_invariants", len(domain_invariants) >= ddd_min_invariants),
        ("selected_contexts", len(selected_contexts) >= 1),
    ]

    corpus_parts: List[str] = [
        tasks.read_text(encoding="utf-8", errors="ignore"),
        (paths.spec_dir / "phases" / "m1.md").read_text(encoding="utf-8", errors="ignore")
        if (paths.spec_dir / "phases" / "m1.md").is_file()
        else "",
        (paths.spec_dir / "phases" / "m2.md").read_text(encoding="utf-8", errors="ignore")
        if (paths.spec_dir / "phases" / "m2.md").is_file()
        else "",
    ]
    corpus = "\n".join(corpus_parts)
    must_link_bind_hits = 0
    for chain_id in must_chain_ids:
        if re.search(rf"(^|[^A-Za-z0-9]){re.escape(chain_id)}([^A-Za-z0-9]|$)", corpus):
            must_link_bind_hits += 1
    must_bind_ratio = 1.0
    if must_chain_ids:
        must_bind_ratio = must_link_bind_hits / float(len(must_chain_ids))

    links = guardrails.build_spec_links(paths.project_dir, quiet=True)
    links_nodes = int_value(links.get("nodes", 0), 0) if isinstance(links, dict) else 0
    links_edges = int_value(links.get("edges", 0), 0) if isinstance(links, dict) else 0
    m0_task_count = int_value(tasks_state.get("m0_task_count", 0), 0)
    task_ids = tasks_state.get("task_ids", [])
    if not isinstance(task_ids, list):
        task_ids = []
    out_of_scope_count = int_value(spec_state.get("out_of_scope_count", 0), 0)

    traceability_checks: List[Tuple[str, bool]] = [
        ("task_ids_present", len(task_ids) >= 1),
        ("m0_task_count_range", 1 <= m0_task_count <= 6),
        ("must_chain_bindings", must_bind_ratio >= 0.5),
        ("links_graph_nodes", links_nodes >= 5),
        ("links_graph_edges", links_edges >= 5),
        ("out_of_scope_defined", out_of_scope_count >= 1),
        ("phase_m1_materialized", markdown_materialized(paths.spec_dir / "phases" / "m1.md")),
        ("phase_m2_materialized", markdown_materialized(paths.spec_dir / "phases" / "m2.md")),
    ]

    completeness_pass = len([c for _, c in completeness_checks if c])
    semantic_pass = len([c for _, c in semantic_checks if c])
    traceability_pass = len([c for _, c in traceability_checks if c])

    completeness_score = score_ratio(completeness_pass, len(completeness_checks))
    semantic_score = score_ratio(semantic_pass, len(semantic_checks))
    traceability_score = score_ratio(traceability_pass, len(traceability_checks))

    overall = int(round(completeness_score * 0.4 + semantic_score * 0.35 + traceability_score * 0.25))
    grade = "F"
    if overall >= 90:
        grade = "A"
    elif overall >= 80:
        grade = "B"
    elif overall >= 70:
        grade = "C"
    elif overall >= 60:
        grade = "D"

    failed_items = {
        "completeness": [name for name, ok in completeness_checks if not ok],
        "semantic": [name for name, ok in semantic_checks if not ok],
        "traceability": [name for name, ok in traceability_checks if not ok],
    }

    return {
        "available": True,
        "overall": overall,
        "grade": grade,
        "completeness": completeness_score,
        "semantic": semantic_score,
        "traceability": traceability_score,
        "failed_items": failed_items,
        "must_bind_ratio": must_bind_ratio,
    }


def cmd_doctor(paths: Paths, _args: argparse.Namespace) -> int:
    missing = 0
    missing_non_runtime = 0
    warnings = 0
    blocking = 0

    print(f"[RPI Doctor] project={paths.project_dir}")

    required_dirs = [
        ".rpi-outfile/specs",
        ".claude/hooks",
        ".claude/commands",
        ".claude/workflow/engine",
        ".claude/workflow/config",
    ]
    required_files = [
        "README.md",
        "prd.md",
        ".rpi-outfile/specs/00_master_spec.md",
        ".rpi-outfile/specs/l0/discovery.md",
        ".rpi-outfile/specs/l0/mvp-skeleton.md",
        ".rpi-outfile/specs/l0/spec.md",
        ".rpi-outfile/specs/l0/tasks.md",
        ".claude/workflow/engine/spec_state_tool.py",
        ".claude/workflow/engine/guardrails_tool.py",
        ".claude/workflow/engine/task_flow_tool.py",
        ".claude/workflow/engine/project_ops_tool.py",
        ".claude/workflow/engine/automation_tool.py",
        ".claude/workflow/engine/pre_tool_use_core.py",
        ".claude/workflow/engine/post_tool_use_core.py",
        ".claude/workflow/engine/session_start_core.py",
        ".claude/workflow/engine/user_prompt_submit_core.py",
        ".claude/workflow/engine/stop_gate_core.py",
        ".claude/workflow/config/architecture.rules.json",
        ".claude/workflow/config/risk_matrix.json",
        ".claude/workflow/config/evals.json",
        ".claude/workflow/config/gates.json",
        ".claude/workflow/config/runtime.json",
    ]

    for rel in required_dirs:
        p = paths.project_dir / rel
        if p.is_dir():
            print(f"[OK] dir: {rel}")
        else:
            print(f"[MISS] dir: {rel}")
            missing += 1
            if not rel.startswith(".rpi-outfile/"):
                missing_non_runtime += 1

    for rel in required_files:
        p = paths.project_dir / rel
        if p.is_file():
            print(f"[OK] file: {rel}")
        else:
            print(f"[MISS] file: {rel}")
            missing += 1
            if not rel.startswith(".rpi-outfile/"):
                missing_non_runtime += 1

    legacy_scripts_dir = paths.workflow_dir / "scripts"
    if legacy_scripts_dir.exists():
        print("[WARN] legacy runtime directory detected: .claude/workflow/scripts (must be removed)")
        warnings += 1
        blocking += 1

    legacy_ref_rx = re.compile(r"\.claude/workflow/scripts/")
    legacy_ref_scan_targets = [
        paths.project_dir / "README.md",
        paths.project_dir / "QUICKSTART.md",
        paths.project_dir / "prd.md",
        paths.project_dir / ".claude" / "settings.json",
        paths.project_dir / ".claude" / "commands",
        paths.project_dir / ".claude" / "skills",
        paths.project_dir / ".claude" / "workflow" / "config",
    ]
    legacy_ref_ignore = {
        ".claude/workflow/config/evals.json",
    }
    legacy_ref_hits: List[str] = []
    for target in legacy_ref_scan_targets:
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = [f for f in target.rglob("*") if f.is_file()]
        else:
            continue
        for f in candidates:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if legacy_ref_rx.search(text):
                rel = str(f.relative_to(paths.project_dir)).replace("\\", "/")
                if rel in legacy_ref_ignore:
                    continue
                legacy_ref_hits.append(rel)
    legacy_ref_hits = sorted(set(legacy_ref_hits))
    if legacy_ref_hits:
        print("[WARN] legacy script path references detected (must be removed):")
        for rel in legacy_ref_hits[:10]:
            print(f"[WARN] {rel}")
        if len(legacy_ref_hits) > 10:
            print(f"[WARN] ... and {len(legacy_ref_hits) - 10} more")
        warnings += 1
        blocking += 1

    if (paths.project_dir / ".rpi-outfile/specs/l0/discovery.md").exists():
        discovery = guardrails.check_discovery(paths.project_dir, quiet=True)
        if str_value(discovery.get("status", ""), "") == "pass":
            print("[OK] discovery looks filled")
        else:
            print(
                "[WARN] discovery appears incomplete — fill: target user, direction (A/B/C chosen), coverage target, "
                "M0 Must with chain IDs (1-3), M0 Won't with chain IDs (>=3), success metrics, "
                "DDD-Lite（统一语言/限界上下文/业务不变量/已选上下文）, 调权项（如使用）"
            )
            errors = discovery.get("errors", [])
            if isinstance(errors, list):
                for line in errors:
                    print(f"[WARN] {line}")
            warnings += 1
            blocking += 1

    if (paths.workflow_dir / "engine" / "task_flow_tool.py").exists():
        rc, out = run_task_flow_capture(paths, "artifact-status", ["--json"])
        if rc in {0, 1} and out.strip():
            try:
                artifact = json.loads(out)
            except Exception:
                artifact = {}
            if isinstance(artifact, dict):
                state = str_value(artifact.get("state", "unknown"), "unknown")
                apply_ready = bool_value(artifact.get("applyReady", False), False)
                if apply_ready:
                    print(f"[OK] artifacts apply-ready (state={state})")
                else:
                    print(f"[WARN] artifacts not apply-ready (state={state}) — /rpi-task start strict mode requires all spec artifacts to be in apply-ready state. Complete discovery/spec/tasks.")
                    next_ready = artifact.get("nextReady", [])
                    if isinstance(next_ready, list) and next_ready:
                        print(f"[WARN] next ready artifacts: {','.join([str(x) for x in next_ready])}")
                    warnings += 1
                    blocking += 1

    tasks_file = paths.project_dir / ".rpi-outfile/specs/l0/tasks.md"
    if tasks_file.exists():
        text = tasks_file.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"task[ -]?0*1", text, flags=re.IGNORECASE):
            print("[OK] tasks include Task-001 baseline")
        else:
            print("[WARN] tasks missing Task-001 style IDs — /rpi-task start auto-matching will be less reliable. Use format: '## Task-001: <title>'")
            warnings += 1

    if (paths.project_dir / ".rpi-outfile/specs/l0/spec.md").exists():
        contract = guardrails.check_contract_spec(paths.project_dir, quiet=True)
        if str_value(contract.get("status", ""), "") == "pass":
            print("[OK] contract spec looks filled")
        else:
            print("[WARN] contract spec appears incomplete — fill: architecture overview, data model, contract/API, flow, acceptance matrix")
            errors = contract.get("errors", [])
            if isinstance(errors, list):
                for line in errors:
                    print(f"[WARN] {line}")
            warnings += 1
            blocking += 1

    if (paths.workflow_dir / "engine" / "automation_tool.py").exists():
        rc, _ = run_automation_capture(paths, "check-entry", ["--quiet"])
        if rc == 0:
            print("[OK] entry integrity check passed")
        else:
            print("[WARN] entry integrity check failed — CLAUDE.md or spec index may be out of sync; run: bash .claude/workflow/rpi.sh check entry")
            warnings += 1
            blocking += 1

    if (paths.workflow_dir / "engine" / "automation_tool.py").exists():
        rc, _ = run_automation_capture(paths, "check-theory", ["--quiet"])
        if rc == 0:
            print("[OK] theory drift check passed (Vibe-Spec + RPI)")
        else:
            print("[WARN] theory drift check failed; run: bash .claude/workflow/rpi.sh check theory")
            warnings += 1
            blocking += 1

    if (
        (paths.project_dir / ".rpi-outfile/specs/l0/discovery.md").exists()
        and (paths.project_dir / ".rpi-outfile/specs/l0/tasks.md").exists()
        and (paths.project_dir / ".rpi-outfile/specs/l0/spec.md").exists()
    ):
        scope_guard = guardrails.check_scope_guard(paths.project_dir, quiet=True)
        if str_value(scope_guard.get("status", ""), "") == "pass":
            print("[OK] scope guard looks compact")
        else:
            print("[WARN] scope guard failed — M0 scope may be over-designed (too many tasks, broad spec, or discovery/tasks mismatch)")
            errors = scope_guard.get("errors", [])
            if isinstance(errors, list):
                for line in errors:
                    print(f"[WARN] {line}")
            warnings += 1
            blocking += 1

    quality = evaluate_artifact_quality(paths)
    if bool_value(quality.get("available", False), False):
        overall = int_value(quality.get("overall", 0), 0)
        grade = str_value(quality.get("grade", "F"), "F")
        completeness = int_value(quality.get("completeness", 0), 0)
        semantic = int_value(quality.get("semantic", 0), 0)
        traceability = int_value(quality.get("traceability", 0), 0)
        print(
            f"[OK] artifact quality score={overall}/100 grade={grade} "
            f"(completeness={completeness} semantic={semantic} traceability={traceability})"
        )
        failed_items = quality.get("failed_items", {})
        if isinstance(failed_items, dict):
            for key in ("completeness", "semantic", "traceability"):
                rows = failed_items.get(key, [])
                if isinstance(rows, list) and rows:
                    print(f"[WARN] quality gaps [{key}]: {', '.join([str(x) for x in rows[:6]])}")
                    warnings += 1
        if overall < 60:
            print("[WARN] artifact quality too low (<60): fix quality gaps before production task execution")
            warnings += 1
            blocking += 1
        elif overall < 75:
            print("[WARN] artifact quality below recommended baseline (<75): complete missing quality dimensions")
            warnings += 1
    else:
        reason = str_value(quality.get("reason", "unknown"), "unknown")
        if missing == 0:
            print(f"[WARN] artifact quality score unavailable: {reason}")
            warnings += 1

    gates_file = paths.config_dir / "gates.json"
    if gates_file.exists():
        cfg = read_json_obj(gates_file)
        verify_default = json_get_path(cfg, "verify.default", [])
        if isinstance(verify_default, list):
            print("[OK] gates verify layer exists")
        else:
            print("[WARN] gates verify layer missing")
            warnings += 1
            verify_default = []

        def has_verify(name: str) -> bool:
            for row in verify_default:
                if isinstance(row, dict) and str_value(row.get("name", ""), "") == name:
                    return True
            return False

        for check_name in [
            "discovery_complete",
            "contract_spec_complete",
            "scope_guard_passed",
            "spec_state_valid",
            "architecture_guard_passed",
        ]:
            if has_verify(check_name):
                print(f"[OK] verify includes {check_name}")
            else:
                print(f"[WARN] verify missing {check_name}")
                warnings += 1

        commands = cfg.get("commands", {})
        unresolved = []
        if isinstance(commands, dict):
            unresolved = [k for k, v in commands.items() if str_value(v, "") == "__REQUIRED__"]
        if unresolved:
            print(f"[WARN] unresolved gate commands: {','.join(unresolved)}")
            warnings += 1
            blocking += 1
        else:
            print("[OK] gate commands resolved")

    runtime_file = paths.config_dir / "runtime.json"
    if runtime_file.exists():
        rt = read_json_obj(runtime_file)
        profile_name = str_value(rt.get("profile_name", "unknown"), "unknown")
        strict_mode = str_value(rt.get("strict_mode", True), "true")
        start_require_ready = str_value(rt.get("start_require_ready", True), "true")
        close_require_spec_sync = str_value(rt.get("close_require_spec_sync", True), "true")
        allow_generic_red = str_value(rt.get("allow_generic_red", False), "false")
        architecture_enforce = str_value(rt.get("architecture_enforce", False), "false")
        auto_rpi_enabled = str_value(rt.get("auto_rpi_enabled", False), "false")
        print(
            "[OK] runtime.profile="
            + f"{profile_name} strict_mode={strict_mode} start_require_ready={start_require_ready} "
            + f"close_require_spec_sync={close_require_spec_sync} allow_generic_red={allow_generic_red} "
            + f"architecture_enforce={architecture_enforce} auto_rpi_enabled={auto_rpi_enabled}"
        )

    print("---")
    if missing > 0:
        if missing_non_runtime == 0:
            print(f"Doctor result: BLOCKED (runtime not initialized — missing={missing} warnings={warnings} blocking={blocking})")
            print('Hint: run /rpi-init <one-line-idea> to bootstrap the project, or: bash .claude/workflow/rpi.sh init bootstrap "<idea>" [platform]')
            return 2
        print(f"Doctor result: FAIL (missing={missing} non_runtime_missing={missing_non_runtime} warnings={warnings} blocking={blocking})")
        print("Hint: missing framework files detected — reinstall or run /rpi-init to regenerate scaffolding")
        return 1

    if blocking > 0:
        print(f"Doctor result: BLOCKED (warnings={warnings} blocking={blocking})")
        print("Next: resolve the [WARN] items above before running /rpi-task start on a production task")
        return 2

    print(f"Doctor result: PASS (warnings={warnings} blocking={blocking})")
    if warnings > 0:
        print("Next: resolve remaining warnings when convenient (non-blocking)")
    return 0


def trim(text: str) -> str:
    return (text or "").strip()


def first_nonempty(*vals: str) -> str:
    for item in vals:
        s = trim(item)
        if s:
            return s
    return ""


def clamp_percent(raw: Any, default: int) -> int:
    if isinstance(raw, bool):
        val = int(raw)
    elif isinstance(raw, int):
        val = raw
    elif isinstance(raw, float):
        val = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r"-?[0-9]+", raw.strip()):
        val = int(raw.strip())
    else:
        val = default
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


def write_spec_file(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        return
    write_text_atomic(path, content.rstrip("\n") + "\n")


def guess_idea(project_dir: Path, idea_arg: str) -> str:
    summary_file = project_dir / ".rpi-outfile/state/init_summary.json"
    mvp_file = project_dir / ".rpi-outfile/specs/l0/mvp-skeleton.md"
    from_summary = ""
    from_mvp = ""
    if summary_file.exists():
        from_summary = str_value(read_json_obj(summary_file).get("idea", ""), "")
    if mvp_file.exists():
        for line in mvp_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("- 项目设想："):
                from_mvp = line.replace("- 项目设想：", "", 1)
                break
    return first_nonempty(idea_arg, from_summary, from_mvp, "业务系统MVP")


def seed_full_l0_baseline(
    spec_root: Path,
    idea: str,
    platform: str,
    force: bool,
    cov_a: int,
    cov_b: int,
    cov_c: int,
    low_conf_budget: int,
) -> None:
    write_spec_file(
        spec_root / "00_master_spec.md",
        f"""# 00 Master Spec（唯一事实源索引）

## 项目概览
- 项目设想：{idea}
- 运行形态：{platform}
- 当前阶段：M0

## Discovery（未定案项目必填）
- .rpi-outfile/specs/l0/discovery.md

## L0 必选底座
- .rpi-outfile/specs/l0/epic.md
- .rpi-outfile/specs/l0/spec.md
- .rpi-outfile/specs/l0/milestones.md
- .rpi-outfile/specs/l0/tasks.md

## L1 痛点模块（按需）
- .rpi-outfile/specs/l1/

## L2 工程护栏
- .rpi-outfile/specs/l2/engineering-guardrails.md
""",
        force,
    )

    write_spec_file(
        spec_root / "l0/discovery.md",
        f"""# L0 Discovery

## 一句话设想
- 目标：{idea}
- 目标用户：一线业务操作人员
- 高频使用场景：每天高频处理核心业务记录
- 时间窗口：2 周内完成 M0 验证

## Facts / Assumptions / Open Questions

### Facts
1. 当前流程仍有大量手工步骤，效率和一致性不足。

### Assumptions
1. 用户优先需要稳定、可追踪、可复现的最小闭环能力。

### Open Questions
1. 是否需要在 M1 引入通知或审批能力。

## 4 阶段业务画布（S1-S4）

| 阶段ID | 阶段目标 | 主要输入 | 可验证输出 |
|---|---|---|---|
| S1 | 入口建模 | 角色、对象、触发条件 | 可进入业务流程 |
| S2 | 核心决策 | 请求、规则、上下文 | 决策结果 |
| S3 | 执行交付 | 决策结果、执行参数 | 业务结果已落地 |
| S4 | 回执复用 | 执行结果、审计信息 | 可复用输出/反馈/追溯 |

## 核心链路候选池

| 链路ID | 链路描述 | 覆盖阶段 | 优先级 | 置信度 |
|---|---|---|---|---|
| L1 | 主链路：用户登录并创建核心记录 | S1->S2->S3->S4 | P0 | 高 |
| L2 | 异常链路：鉴权失败或冲突拒绝 | S2->S3->S4 | P0 | 中 |
| L3 | 治理链路：状态/权限变更同步生效 | S1->S2->S3->S4 | P1 | 中 |
| L4 | 扩展链路：报表/运营分析 | S3->S4 | P2 | 中 |

## MVP 候选方向评分（A/B/C）

| 维度 | A | B | C |
|---|---:|---:|---:|
| 核心业务覆盖度 | 4 | 5 | 5 |
| 用户价值 | 4 | 5 | 5 |
| 交付速度 | 5 | 3 | 2 |
| 技术风险 | 4 | 3 | 2 |
| 依赖复杂度 | 5 | 3 | 2 |
| 可验证性 | 5 | 4 | 4 |
| 运营可持续性 | 2 | 4 | 5 |

## 覆盖率与不确定性预算
- 覆盖率公式：已选 P0 链路数 / P0 总链路数
- A：>={cov_a}%（至少 1 主链路 + 1 异常链路）
- B：>={cov_b}%（主路径可用且可复测）
- C：={cov_c}%（并补齐治理链路）
- 低置信度链路占比建议 <= {low_conf_budget}%

## DDD-Lite 语义与边界
- 统一语言（Ubiquitous Language）：
  - 用户：有权限执行核心业务动作的系统主体
  - 会话：用户在一次登录周期内的操作上下文
  - 核心记录：系统最小可追踪业务对象
  - 状态：核心记录在生命周期中的阶段值
  - 鉴权：判断用户是否可执行目标动作
  - 冲突：同一记录被重复或并发变更的异常场景
  - 回执：系统对业务动作产出的结果反馈
  - 审计轨迹：可用于追溯操作与结果的证据链
- 限界上下文（Bounded Context）：
  - C1 [Core]：身份与访问上下文
  - C2 [Supporting]：核心记录上下文
  - C3 [Governance]：审计与回执上下文
- 业务不变量（Domain Invariants）：
  - R1：未通过鉴权的请求不得创建或修改核心记录
  - R2：核心记录创建后必须有唯一标识且状态可追溯
  - R3：冲突场景必须返回可解释错误，不允许静默覆盖
  - R4：每次状态变更必须写入审计轨迹并可回放
- 已选上下文（M0）：
  - C1 [Core]
  - C2 [Supporting]

## 结论
- 选择方向：A（核心可证级）
- 覆盖率目标：P0 >= {cov_a}%
- 优先级调权（可选）：
  - 提升 L4: 用户在首版即需要基础统计入口（提升可见价值）
  - 降权 L2: 将冲突细分处理降级到 M1（保留基础冲突提示）
- 加权覆盖率目标：{cov_a}%
- M0 Must（1-3）：
  - L1：用户登录并创建核心记录
  - L2：鉴权失败/冲突时返回可解释结果
- M0 Won't（>=3）：
  - L3：状态/权限治理链路
  - L4：报表与统计分析
  - 多租户与复杂权限编排
- 成功指标（2-4）：
  - 已选 P0 链路 E2E 通过率 = 100%
  - 核心记录创建成功率 >= 99%
  - 单次录入 P95 耗时 < 30 秒
""",
        force,
    )

    write_spec_file(
        spec_root / "l0/epic.md",
        """# L0 Epic

## Facts / Assumptions / Open Questions

### Facts
1. 业务价值取决于核心记录链路是否可稳定执行。

### Assumptions
1. M0 只聚焦最小业务闭环，不引入非关键能力。

### Open Questions
1. M1 是否需要增强检索或导出能力。

- 目标用户：一线业务操作人员
- 核心问题：现有流程难追踪、耗时高、容易遗漏
- 核心价值：用最小系统化流程替代手工处理
- 成功指标（2-4 项）：
  - 关键流程可全程留痕
  - 创建与查询链路稳定通过
- Must：
  - 用户登录
  - 创建核心记录
  - 查询核心记录
- Won't：
  - 报表中心
  - 多租户
  - 自动审批
""",
        force,
    )

    write_spec_file(
        spec_root / "l0/spec.md",
        """# L0 Spec

## 架构边界
- In Scope：
  - 认证、核心记录创建、核心记录查询
- Out of Scope：
  - 报表分析
  - 多租户治理
  - 复杂自动化流程

## 数据模型
- 核心实体：
  - `user`：系统用户
  - `item`：核心业务记录
- 关键字段：
  - `item.id`：唯一标识
  - `item.title`：记录标题
  - `item.status`：状态（open/in_progress/closed）
  - `item.created_at`：创建时间

## 接口契约
- 输入契约：
  - `POST /api/items`：`title` 必填
  - `GET /api/items`：支持分页与状态过滤
- 输出契约：
  - 成功：`200/201` + JSON 数据体
  - 失败：`4xx/5xx` + `code/message`
- 错误码/失败语义：
  - `VALIDATION_ERROR`
  - `UNAUTHORIZED`

## 关键流程
- 正向路径：
  1. 用户登录
  2. 提交核心记录
  3. 在列表页看到新记录
- 异常路径：
  - 输入非法时返回明确错误码与提示
- 回退策略：
  - 写入失败不落库并支持重试

## 验收与异常矩阵
- 验收标准：
  - 创建核心记录并可在列表中查询到
  - 非法输入被拦截并返回错误
- 异常矩阵：
  - 未认证访问 -> 401
  - 非法参数 -> 400

## 非功能预算（性能/成本/稳定性）
- 性能预算：创建接口 P95 < 300ms
- 成本预算：M0 单实例可支撑首轮验证流量
- 稳定性预算：核心接口可用性 >= 99.9%
""",
        force,
    )

    write_spec_file(
        spec_root / "l0/milestones.md",
        """# L0 Milestones

## M0
- 目标：打通最小业务闭环并完成可验证交付
- 范围：登录 + 创建核心记录 + 列表查询
- 交付：可运行服务、最小测试、审计留痕
- 验收：M0 门控全通过
- 时长：2 周
- 量化指标：创建成功率 >= 99%

## M1
- 目标：提升可用性与协作效率
- 范围：检索、筛选、基础通知
- 交付：稳定性增强与运维补强
- 验收：关键路径稳定运行 2 周
- 时长：2-4 周
- 量化指标：主要查询 P95 < 500ms

## M2
- 目标：上线准备与运营保障
- 范围：安全、监控、回滚预案
- 交付：上线包、演练记录、审计材料
- 验收：上线门禁全部通过
- 时长：1-2 周
- 量化指标：故障恢复演练通过率 100%
""",
        force,
    )

    write_spec_file(
        spec_root / "l0/tasks.md",
        """# L0 Tasks

## M0
- Task 1 登录能力
  - 目标：保障用户可认证访问系统
  - 输入/输出：账号凭证 -> 登录态
  - 依赖：用户基础数据
  - 实现边界（不做）：第三方 SSO
  - 可执行验收标准：合法账号可登录，非法账号被拒绝

- Task 2 创建核心记录
  - 目标：完成核心数据录入
  - 输入/输出：记录表单 -> 新记录 ID
  - 依赖：登录能力
  - 实现边界（不做）：批量导入
  - 可执行验收标准：创建成功并返回唯一 ID

- Task 3 核心记录列表查询
  - 目标：支持按状态查看记录
  - 输入/输出：过滤条件 -> 记录列表
  - 依赖：创建核心记录
  - 实现边界（不做）：复杂报表统计
  - 可执行验收标准：可按状态筛选并返回正确结果

## M1
- Task 4 检索与筛选增强

## M2
- Task 5 上线保障与回滚演练
""",
        force,
    )


def cmd_bootstrap(paths: Paths, args: argparse.Namespace) -> int:
    ensure_layout(paths)
    spec_dir = paths.project_dir / ".rpi-outfile/specs"
    blueprint_dir = paths.project_dir / ".rpi-blueprint/specs"
    workflow_dir = paths.project_dir / ".claude/workflow"
    init_summary_file = paths.project_dir / ".rpi-outfile/state/init_summary.json"
    mvp_file = paths.project_dir / ".rpi-outfile/specs/l0/mvp-skeleton.md"

    spec_dir.mkdir(parents=True, exist_ok=True)
    if not blueprint_dir.is_dir():
        print(f"Missing blueprint directory: {blueprint_dir}", file=sys.stderr)
        return 1

    idea = guess_idea(paths.project_dir, args.idea or "")
    platform = first_nonempty(args.platform or "", "Web")
    runtime = read_json_obj(paths.runtime_file) if paths.runtime_file.exists() else default_runtime()
    cov_a, cov_b, cov_c, low_conf_budget = mvp_coverage_policy(runtime)

    if args.force and spec_dir.is_dir():
        existing_spec_files = [p for p in spec_dir.rglob("*") if p.is_file()]
        snapshot_rows = artifact_recovery.snapshot_files(
            project_dir=paths.project_dir,
            targets=existing_spec_files,
            reason="bootstrap_force",
            actor="project-ops",
        )
        if snapshot_rows:
            print(f"Snapshot before force bootstrap: {len(snapshot_rows)} files")

    seed_full_l0_baseline(
        spec_dir,
        idea,
        platform,
        force=args.force,
        cov_a=cov_a,
        cov_b=cov_b,
        cov_c=cov_c,
        low_conf_budget=low_conf_budget,
    )

    for src in sorted(blueprint_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(blueprint_dir)
        dst = spec_dir / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    gates_file = workflow_dir / "config/gates.json"
    if not gates_file.exists():
        write_json(
            gates_file,
            {
                "phase_gates": {"M0": ["bootstrap_check"], "M1": ["bootstrap_check"], "M2": ["bootstrap_check"]},
                "commands": {"bootstrap_check": "bash .claude/workflow/rpi.sh check bootstrap"},
                "verify": {
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
                },
            },
        )

    runtime_file = workflow_dir / "config/runtime.json"
    if not runtime_file.exists():
        write_json(runtime_file, default_runtime())

    # Keep bootstrap and doctor expectations consistent:
    # ensure mvp-skeleton exists in full baseline output.
    if args.force or not mvp_file.exists():
        rc, out = run_automation_capture(paths, "create-mvp", [idea, platform])
        if rc != 0:
            if out.strip():
                print(out.rstrip("\n"), file=sys.stderr)
            print("bootstrap failed: unable to seed .rpi-outfile/specs/l0/mvp-skeleton.md", file=sys.stderr)
            return 1

    _ = init_summary_file
    print("Bootstrap completed for empty project mode")
    print(f"Spec root: {spec_dir}")
    print(f"Blueprint root: {blueprint_dir}")
    print(f"Seeded full baseline with idea: {idea}")
    print(f"Baseline overwrite mode: {'true' if args.force else 'false'}")
    print(f"Gate config: {gates_file}")
    print(f"Runtime config: {runtime_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI project ops engine")
    parser.add_argument("--project-dir", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_env = sub.add_parser("check-environment")
    p_env.add_argument("--require-jq", action="store_true")
    p_env.add_argument("--auto-fix", action="store_true")
    p_env.add_argument("--include-recommended", action="store_true")

    sub.add_parser("init-state")

    p_doctor = sub.add_parser("doctor")

    p_bootstrap = sub.add_parser("bootstrap")
    p_bootstrap.add_argument("--force", action="store_true")
    p_bootstrap.add_argument("idea", nargs="?")
    p_bootstrap.add_argument("platform", nargs="?")

    return parser


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    project_dir = Path(ns.project_dir).resolve() if ns.project_dir else resolve_project_dir(Path(__file__).resolve().parent)
    paths = build_paths(project_dir)
    if ns.cmd == "check-environment":
        return cmd_check_environment(paths, ns)
    if ns.cmd == "init-state":
        return cmd_init_state(paths, ns)
    if ns.cmd == "doctor":
        return cmd_doctor(paths, ns)
    if ns.cmd == "bootstrap":
        return cmd_bootstrap(paths, ns)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
