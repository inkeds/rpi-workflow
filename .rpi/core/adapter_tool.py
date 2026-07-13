#!/usr/bin/env python3
"""Generate and inspect Codex/Claude adapters for the platform-neutral RPI core."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
CAPABILITIES = ("instructions", "skills", "session_start", "prompt_submit", "pre_tool_blocking", "post_tool_evidence", "stop_gate")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def command_version(command: str) -> dict[str, Any]:
    executable = shutil.which(command)
    if not executable:
        return {"installed": False, "path": None, "version": None}
    for flag in ("--version", "version"):
        try:
            proc = subprocess.run([executable, flag], text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = (proc.stdout or proc.stderr).strip().splitlines()
        if output:
            return {"installed": True, "path": executable, "version": output[0]}
    return {"installed": True, "path": executable, "version": "unknown"}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "missing"
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def platform_fingerprint(project_dir: Path, platform: str, version: str | None) -> str:
    if platform == "codex":
        parts = [
            version or "missing",
            sha256_file(project_dir / "AGENTS.md"),
            sha256_file(project_dir / ".codex" / "hooks.json"),
            sha256_tree(project_dir / ".agents" / "skills"),
            sha256_file(project_dir / ".rpi" / "adapters" / "hook_bridge.py"),
        ]
    else:
        parts = [
            version or "missing",
            sha256_file(project_dir / "AGENTS.md"),
            sha256_file(project_dir / "CLAUDE.md"),
            sha256_file(project_dir / ".claude" / "settings.json"),
            sha256_tree(project_dir / ".claude" / "skills"),
            sha256_file(project_dir / ".rpi" / "adapters" / "hook_bridge.py"),
        ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def verification_path(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile" / "state" / "compat" / "verification.json"


def runtime_events(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / ".rpi-outfile" / "state" / "compat" / "runtime-events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def codex_hooks() -> dict[str, Any]:
    hooks: dict[str, Any] = {"hooks": {}}
    for event in HOOK_EVENTS:
        group: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": f'python3 "$(git rev-parse --show-toplevel)/.rpi/adapters/hook_bridge.py" --platform codex --event {event} --project-dir "$(git rev-parse --show-toplevel)"',
                    "timeout": 25,
                    "statusMessage": f"RPI {event}",
                }
            ]
        }
        if event in {"PreToolUse", "PostToolUse"}:
            group["matcher"] = "Bash|Edit|Write|MultiEdit|exec_command|apply_patch"
        hooks["hooks"][event] = [group]
    return hooks


def render_codex_config() -> str:
    return """# Generated RPI Codex adapter. Project config loads only for trusted repositories.\n"""


def copy_skills_to(project_dir: Path, target: Path) -> int:
    source = project_dir / ".rpi" / "skills"
    if not source.exists():
        return 0
    copied = 0
    for skill_dir in sorted(path for path in source.iterdir() if path.is_dir()):
        destination = target / skill_dir.name
        destination.mkdir(parents=True, exist_ok=True)
        for item in skill_dir.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(skill_dir)
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(item.read_bytes())
            copied += 1
    return copied


def capability_states(project_dir: Path, platform: str, installed: dict[str, Any], adapter_files: dict[str, bool]) -> dict[str, Any]:
    fingerprint = platform_fingerprint(project_dir, platform, installed.get("version"))
    verification = read_json(verification_path(project_dir), {"platforms": {}})
    saved = ((verification.get("platforms") or {}).get(platform) or {}) if isinstance(verification, dict) else {}
    saved_caps = saved.get("capabilities", {}) if isinstance(saved, dict) else {}
    event_map = {
        "SessionStart": "session_start",
        "UserPromptSubmit": "prompt_submit",
        "PreToolUse": "pre_tool_blocking",
        "PostToolUse": "post_tool_evidence",
        "Stop": "stop_gate",
    }
    observed = {
        event_map[row.get("event")]
        for row in runtime_events(project_dir)
        if row.get("platform") == platform and row.get("event") in event_map and row.get("fingerprint") == fingerprint
    }
    configured = {
        "instructions": adapter_files.get("instructions", adapter_files.get("config", False)),
        "skills": adapter_files.get("skills", False),
        "session_start": adapter_files.get("hooks", adapter_files.get("settings", False)),
        "prompt_submit": adapter_files.get("hooks", adapter_files.get("settings", False)),
        "pre_tool_blocking": adapter_files.get("hooks", adapter_files.get("settings", False)),
        "post_tool_evidence": adapter_files.get("hooks", adapter_files.get("settings", False)),
        "stop_gate": adapter_files.get("hooks", adapter_files.get("settings", False)),
    }
    states: dict[str, Any] = {}
    for capability in CAPABILITIES:
        saved_item = saved_caps.get(capability, {}) if isinstance(saved_caps, dict) else {}
        if not installed.get("installed"):
            status = "missing"
            reason = "CLI not installed in current environment"
        elif not configured.get(capability, False):
            status = "missing"
            reason = "adapter file missing"
        elif capability in observed:
            status = "verified"
            reason = "observed from a real lifecycle hook"
        elif saved_item.get("status") == "verified" and saved.get("fingerprint") == fingerprint:
            status = "verified"
            reason = str(saved_item.get("evidence") or "explicit verification")
        elif saved_item.get("status") == "verified":
            status = "stale"
            reason = "CLI version or adapter content changed"
        else:
            status = "configured"
            reason = "configured but not runtime-verified"
        states[capability] = {"status": status, "reason": reason}
    return {"fingerprint": fingerprint, "capabilities": states}


def compatibility_report(project_dir: Path) -> dict[str, Any]:
    codex = command_version("codex")
    claude = command_version("claude")
    codex_files = {
        "config": (project_dir / ".codex" / "config.toml").exists(),
        "hooks": (project_dir / ".codex" / "hooks.json").exists(),
        "skills": (project_dir / ".agents" / "skills").exists(),
        "instructions": (project_dir / "AGENTS.md").exists(),
    }
    claude_files = {
        "settings": (project_dir / ".claude" / "settings.json").exists(),
        "hooks": (project_dir / ".claude" / "settings.json").exists(),
        "instructions": (project_dir / "CLAUDE.md").exists(),
        "skills": (project_dir / ".claude" / "skills").exists(),
    }
    report = {
        "schema_version": 2,
        "generated_at": utc_now(),
        "platforms": {
            "codex": {
                **codex,
                "adapter_files": codex_files,
                **capability_states(project_dir, "codex", codex, codex_files),
                "requires_project_trust": True,
                "requires_hook_review": True,
            },
            "claude": {
                **claude,
                "adapter_files": claude_files,
                **capability_states(project_dir, "claude", claude, claude_files),
                "requires_workspace_trust": True,
            },
        },
        "shared_core": {
            "agents_entry": (project_dir / "AGENTS.md").exists(),
            "schemas": (project_dir / ".rpi" / "schemas").exists(),
            "runtime_facts": ".rpi-outfile",
        },
    }
    return report


def cmd_setup(project_dir: Path) -> int:
    codex_dir = project_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(render_codex_config(), encoding="utf-8")
    write_json(codex_dir / "hooks.json", codex_hooks())
    codex_skills = copy_skills_to(project_dir, project_dir / ".agents" / "skills")
    claude_skills = copy_skills_to(project_dir, project_dir / ".claude" / "skills")
    report = compatibility_report(project_dir)
    report["generated_skill_files"] = {"codex": codex_skills, "claude": claude_skills}
    report_path = project_dir / ".rpi-outfile" / "state" / "compatibility.json"
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(project_dir: Path) -> int:
    report = compatibility_report(project_dir)
    problems = []
    if not report["shared_core"]["agents_entry"]:
        problems.append("AGENTS.md missing")
    if not report["platforms"]["codex"]["adapter_files"]["hooks"]:
        problems.append("Codex hooks adapter missing; run compat setup")
    if not report["platforms"]["claude"]["adapter_files"]["settings"]:
        problems.append("Claude settings adapter missing")
    runtime = read_json(project_dir / ".claude" / "workflow" / "config" / "runtime.json", {})
    profile = str(runtime.get("profile_name") or "balanced-enterprise")
    required = {
        "auto-lab": (),
        "balanced-enterprise": ("instructions", "skills", "pre_tool_blocking", "post_tool_evidence", "stop_gate"),
        "strict-regulated": CAPABILITIES,
    }.get(profile, ("instructions", "skills", "pre_tool_blocking", "post_tool_evidence", "stop_gate"))
    degradation = []
    for platform, data in report["platforms"].items():
        if not data.get("installed"):
            continue
        for capability in required:
            status = data["capabilities"][capability]["status"]
            if status != "verified":
                degradation.append({"platform": platform, "capability": capability, "status": status})
    if profile == "auto-lab":
        status = "ready_with_warnings" if problems or degradation else "ready"
    else:
        status = "degraded" if problems or degradation else "ready"
    report["profile"] = profile
    report["governance"] = {"status": status, "required_capabilities": list(required), "degradation": degradation}
    report["status"] = status
    report["problems"] = problems
    write_json(project_dir / ".rpi-outfile" / "state" / "compatibility.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status in {"ready", "ready_with_warnings"} else 2


def cmd_verify(project_dir: Path, platform: str, capability: str, evidence: str) -> int:
    if capability not in (*CAPABILITIES, "all"):
        raise ValueError(f"unknown capability: {capability}")
    report = compatibility_report(project_dir)
    platform_data = report["platforms"][platform]
    if not platform_data.get("installed"):
        raise ValueError(f"{platform} CLI is not installed")
    path = verification_path(project_dir)
    doc = read_json(path, {"schema_version": 1, "platforms": {}})
    platforms = doc.setdefault("platforms", {})
    entry = platforms.setdefault(platform, {"capabilities": {}})
    entry["fingerprint"] = platform_data["fingerprint"]
    entry["version"] = platform_data.get("version")
    entry["verified_at"] = utc_now()
    target_capabilities = CAPABILITIES if capability == "all" else (capability,)
    for name in target_capabilities:
        entry.setdefault("capabilities", {})[name] = {
            "status": "verified",
            "evidence": evidence,
            "verified_at": utc_now(),
        }
    write_json(path, doc)
    print(json.dumps({name: entry["capabilities"][name] for name in target_capabilities}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI CLI adapter generator")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("doctor")
    verify = sub.add_parser("verify")
    verify.add_argument("platform", choices=("codex", "claude"))
    verify.add_argument("capability", choices=(*CAPABILITIES, "all"))
    verify.add_argument("--evidence", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    if args.command == "setup":
        return cmd_setup(project_dir)
    if args.command == "doctor":
        return cmd_doctor(project_dir)
    if args.command == "verify":
        try:
            return cmd_verify(project_dir, args.platform, args.capability, args.evidence)
        except ValueError as exc:
            print(str(exc))
            return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
