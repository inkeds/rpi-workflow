#!/usr/bin/env python3
"""Reconcile RPI task intent, design updates, implementation, and tests."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import change_intelligence
import state_store
import schema_validation


CODE_EXT = re.compile(r"\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|rb|php|cs|swift|scala|sh|sql)$", re.IGNORECASE)
TEST_PATH = re.compile(r"(^|/)(?:tests?|specs?)(/|_)|(?:^|/).*[_\.-](?:test|spec)\.", re.IGNORECASE)
SPEC_PATH = re.compile(r"(^|/)(?:\.rpi-outfile/)?specs?/|(^|/)(?:docs|design)/", re.IGNORECASE)
MIGRATION_PATH = re.compile(r"(^|/)(?:migrations?|schema)(/|\.)", re.IGNORECASE)
HIGH_IMPACT_DOMAINS = {"authorization", "assets", "billing", "privacy", "collaboration"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    return state_store.read_json(path, default)


def write_json(path: Path, payload: Any) -> None:
    state_store.write_json(path, payload)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _paths_since(project_dir: Path, created_at: str) -> list[str]:
    event_log = project_dir / ".rpi-outfile" / "logs" / "events.jsonl"
    paths: list[str] = []
    for row in read_jsonl(event_log):
        if str(row.get("event", "")) != "post_tool_use":
            continue
        ts = str(row.get("ts", ""))
        if created_at and ts and ts < created_at:
            continue
        path = str(row.get("path", "")).strip()
        if not path and row.get("targets_code"):
            path = "<opaque-code-mutation>"
        elif not path and row.get("targets_specs"):
            path = "<opaque-spec-mutation>"
        if path and path not in paths:
            paths.append(path)
    return paths


def _reconcile_unlocked(project_dir: Path, task_id: str = "") -> dict[str, Any]:
    task = read_json(project_dir / ".rpi-outfile/state/current_task.json", {})
    current_task_id = str(task.get("task_id", ""))
    if task_id and current_task_id and task_id != current_task_id:
        raise ValueError(f"active task is {current_task_id}, not {task_id}")
    effective_task_id = task_id or current_task_id
    if not effective_task_id:
        raise ValueError("no active task to reconcile")

    created_at = str(task.get("created_at", ""))
    changed_paths = _paths_since(project_dir, created_at)
    code_paths = [p for p in changed_paths if (CODE_EXT.search(p) or p == "<opaque-code-mutation>") and not TEST_PATH.search(p) and not SPEC_PATH.search(p)]
    test_paths = [p for p in changed_paths if TEST_PATH.search(p)]
    spec_paths = [p for p in changed_paths if SPEC_PATH.search(p)]
    migration_paths = [p for p in changed_paths if MIGRATION_PATH.search(p)]
    primary_change = task.get("change", {}) if isinstance(task.get("change", {}), dict) else {}
    change_ids: list[str] = []
    if str(primary_change.get("change_id", "")):
        change_ids.append(str(primary_change.get("change_id", "")))
    refs = task.get("change_refs", []) if isinstance(task.get("change_refs", []), list) else []
    change_ids.extend(str(item) for item in refs if str(item))
    change_ids = list(dict.fromkeys(change_ids))
    changes: list[dict[str, Any]] = []
    for change_id in change_ids:
        loaded = read_json(project_dir / ".rpi-outfile/state/changes" / f"{change_id}.json", {})
        if isinstance(loaded, dict) and loaded:
            changes.append(loaded)
    if not changes and primary_change:
        changes.append(primary_change)
    change_types = {str(item.get("change_type", "")) for item in changes if str(item.get("change_type", ""))}
    change_type = ",".join(sorted(change_types))
    domains = {
        str(domain)
        for item in changes
        for domain in (item.get("affected_domains", []) if isinstance(item.get("affected_domains", []), list) else [])
        if str(domain)
    }
    invariant_refs = {
        str(invariant)
        for item in changes
        for invariant in (item.get("affected_invariants", []) if isinstance(item.get("affected_invariants", []), list) else [])
        if str(invariant)
    }
    spec_refs = task.get("spec_refs", []) if isinstance(task.get("spec_refs", []), list) else []
    tdd = task.get("tdd", {}) if isinstance(task.get("tdd", {}), dict) else {}

    issues: list[dict[str, Any]] = []

    def issue(severity: str, category: str, message: str) -> None:
        issues.append({"severity": severity, "category": category, "message": message})

    if code_paths and not spec_refs:
        issue("high", "spec_reference_missing", "implementation changed without task spec_refs")
    if code_paths and not test_paths:
        issue("high", "test_evidence_missing", "implementation changed without a targeted test-file update")
    if code_paths and str(tdd.get("latest_test_status", "unknown")) != "pass":
        issue("high", "test_execution_missing", "implementation changed without passing test execution evidence on the active task")
    if code_paths and change_types & {"feature_change", "cross_domain_change", "product_model_change", "invariant_change"} and not spec_paths:
        issue("high", "design_update_missing", "functional implementation changed without a design/spec write-back")
    if migration_paths and not spec_paths:
        issue("high", "migration_design_missing", "schema or migration changed without documenting data compatibility and recovery")
    if code_paths and domains & HIGH_IMPACT_DOMAINS:
        high_impact_design = [
            p for p in spec_paths
            if any(name in p.lower() for name in ("invariant", "capabilit", "spec.md", "decision"))
        ]
        if not high_impact_design:
            issue("high", "high_impact_design_missing", "high-impact domain changed without an applicable Spec, capability, invariant, or decision update")
        if not invariant_refs:
            issue("high", "invariant_reference_missing", "high-impact change has no generated or selected invariant references")

    pending_changes = [str(item.get("change_id", "")) for item in changes if item.get("status") == "pending_decision"]
    if pending_changes:
        issue("high", "pending_change_decision", f"linked changes still require decisions: {','.join(pending_changes)}")
    unresolved_conflicts = [
        str(conflict.get("conflict_id", ""))
        for item in changes
        for conflict in (item.get("conflicts", []) if isinstance(item.get("conflicts", []), list) else [])
        if isinstance(conflict, dict) and conflict.get("status") == "pending" and str(conflict.get("conflict_id", ""))
    ]
    if unresolved_conflicts:
        issue("high", "unresolved_design_conflict", "linked changes have unresolved conflicts: " + ",".join(unresolved_conflicts))

    implementation_domains: set[str] = set()
    for path_text in code_paths:
        if path_text.startswith("<"):
            continue
        path = project_dir / path_text
        content = ""
        try:
            if path.exists() and path.stat().st_size <= 512_000:
                content = path.read_text(encoding="utf-8", errors="ignore")[:120_000]
        except OSError:
            content = ""
        implementation_domains.update(change_intelligence.detect_domains(f"{path_text}\n{content}"))
    undeclared_high_impact = (implementation_domains & HIGH_IMPACT_DOMAINS) - domains
    if undeclared_high_impact:
        issue(
            "high",
            "implementation_scope_untracked",
            "implementation touches high-impact domains absent from change analysis: " + ",".join(sorted(undeclared_high_impact)),
        )

    high_issues = [item for item in issues if item["severity"] == "high"]
    status = "pass" if not high_issues else "fail"
    report = {
        "schema_version": 2,
        "task_id": effective_task_id,
        "generated_at": utc_now(),
        "status": status,
        "classification": "aligned" if status == "pass" else "design_or_evidence_gap",
        "change_id": change_ids[0] if change_ids else "",
        "change_ids": change_ids,
        "change_type": change_type,
        "affected_domains": sorted(domains),
        "implementation_domains": sorted(implementation_domains),
        "invariant_refs": sorted(invariant_refs),
        "changed_paths": changed_paths,
        "code_paths": code_paths,
        "test_paths": test_paths,
        "spec_paths": spec_paths,
        "migration_paths": migration_paths,
        "issues": issues,
    }
    out_dir = project_dir / ".rpi-outfile/state/reconciliation"
    schema_validation.validate(report, "reconciliation.schema.json", project_dir)
    write_json(out_dir / f"{effective_task_id}.json", report)
    write_json(out_dir / "latest.json", report)

    if status == "pass" and change_ids:
        for change_id in change_ids:
            change_path = project_dir / ".rpi-outfile/state/changes" / f"{change_id}.json"
            change_doc = read_json(change_path, {})
            if isinstance(change_doc, dict) and change_doc:
                change_doc["status"] = "reconciled"
                change_doc["reconciled_at"] = report["generated_at"]
                schema_validation.validate(change_doc, "change-impact.schema.json", project_dir)
                write_json(change_path, change_doc)
        latest_doc = read_json(project_dir / ".rpi-outfile/state/changes" / f"{change_ids[-1]}.json", {})
        if latest_doc:
            write_json(project_dir / ".rpi-outfile/state/changes/latest.json", latest_doc)
    return report


def reconcile(project_dir: Path, task_id: str = "") -> dict[str, Any]:
    transaction = project_dir / ".rpi-outfile/state/reconciliation-transaction"
    with state_store.exclusive_lock(transaction):
        return _reconcile_unlocked(project_dir, task_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI design/implementation reconciliation")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--task", default="")
    sub.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    try:
        if args.command == "run":
            report = reconcile(project_dir, args.task)
        else:
            report = read_json(project_dir / ".rpi-outfile/state/reconciliation/latest.json", {"status": "empty"})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"pass", "empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
