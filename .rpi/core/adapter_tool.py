#!/usr/bin/env python3
"""Generate and inspect Codex/Claude adapters for the platform-neutral RPI core."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")


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


def compatibility_report(project_dir: Path) -> dict[str, Any]:
    codex = command_version("codex")
    claude = command_version("claude")
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "platforms": {
            "codex": {
                **codex,
                "adapter_files": {
                    "config": (project_dir / ".codex" / "config.toml").exists(),
                    "hooks": (project_dir / ".codex" / "hooks.json").exists(),
                    "skills": (project_dir / ".agents" / "skills").exists(),
                },
                "requires_project_trust": True,
                "requires_hook_review": True,
            },
            "claude": {
                **claude,
                "adapter_files": {
                    "settings": (project_dir / ".claude" / "settings.json").exists(),
                    "instructions": (project_dir / "CLAUDE.md").exists(),
                    "skills": (project_dir / ".claude" / "skills").exists(),
                },
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
    report["status"] = "ready" if not problems else "degraded"
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI CLI adapter generator")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    if args.command == "setup":
        return cmd_setup(project_dir)
    if args.command == "doctor":
        return cmd_doctor(project_dir)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
