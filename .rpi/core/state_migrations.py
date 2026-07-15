#!/usr/bin/env python3
"""Idempotent migrations for persisted RPI governance state."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Sequence

import change_intelligence
import state_store
import schema_validation


CURRENT_SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def migrate_change(doc: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(doc)
    request = str(migrated.get("request_text", ""))
    if request:
        baseline = change_intelligence.analyze_change(request)
        for key, value in baseline.items():
            migrated.setdefault(key, value)
    decisions = []
    for raw in _list(migrated.get("decisions_required")):
        if not isinstance(raw, dict):
            continue
        decision = dict(raw)
        topic = str(decision.get("topic", ""))
        template = change_intelligence.DECISION_TEMPLATES.get(topic, {"options": ["preserve_current", "adopt_proposed"], "recommended_option": "preserve_current"})
        decision.setdefault("decision_id", change_intelligence.stable_id("DEC", f"{request}:{topic}"))
        decision.setdefault("status", "confirmed" if decision.get("selected_option") else "pending")
        decision.setdefault("options", list(template["options"]))
        decision.setdefault("recommended_option", template["recommended_option"])
        decision.setdefault("selected_option", None)
        evidence = decision.get("confirmation_evidence", [])
        if isinstance(evidence, str):
            evidence = [evidence]
        decision["confirmation_evidence"] = _list(evidence)
        decisions.append(decision)
    migrated["decisions_required"] = decisions
    migrated.setdefault("affected_capabilities", [])
    migrated.setdefault("affected_invariants", [])
    migrated.setdefault("affected_specs", [])
    migrated.setdefault("documents_to_update", [])
    migrated.setdefault("lifecycle_impacts", [])
    migrated.setdefault("relation", "unassigned")
    if decisions and any(item.get("status") != "confirmed" for item in decisions):
        migrated["status"] = "pending_decision"
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    return migrated


def migrate_capabilities(doc: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(doc)
    by_id: dict[str, dict[str, Any]] = {}
    for raw in _list(migrated.get("capabilities")):
        if not isinstance(raw, dict) or not str(raw.get("id", "")):
            continue
        item = dict(raw)
        item.setdefault("status", "candidate")
        item.setdefault("confidence", "low")
        item.setdefault("dependencies", [])
        item.setdefault("invariants", [])
        item.setdefault("spec_refs", [])
        item.setdefault("test_refs", [])
        item.setdefault("aliases", [])
        item.setdefault("source_changes", _list(item.get("source_claims")))
        item.setdefault("supersedes", [])
        review_required = bool(item.get("decomposition_review"))
        item.setdefault(
            "decomposition",
            {
                "status": "required" if review_required else "not_required",
                "reasons": ["legacy_review_flag"] if review_required else [],
                "suggested_slices": [],
                "source_changes": _list(item.get("source_changes")) if review_required else [],
            },
        )
        by_id[str(item["id"])] = item
    migrated["capabilities"] = list(by_id.values())
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    migrated.setdefault("updated_at", utc_now())
    return migrated


def migrate_invariants(doc: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(doc)
    by_id: dict[str, dict[str, Any]] = {}
    for raw in _list(migrated.get("invariants")):
        if not isinstance(raw, dict) or not str(raw.get("id", "")):
            continue
        item = dict(raw)
        item.setdefault("status", "candidate")
        item.setdefault("scope", [])
        item.setdefault("source", [])
        item.setdefault("enforcement", {"tests": [], "constraints": [], "static_checks": []})
        item.setdefault("change_policy", "explicit_decision_required")
        by_id[str(item["id"])] = item
    migrated["invariants"] = list(by_id.values())
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    migrated.setdefault("updated_at", utc_now())
    return migrated


def migrate_reconciliation(doc: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(doc)
    change_id = str(migrated.get("change_id", ""))
    migrated.setdefault("change_ids", [change_id] if change_id else [])
    migrated.setdefault("implementation_domains", [])
    migrated.setdefault("invariant_refs", [])
    migrated.setdefault("issues", [])
    migrated.setdefault("task_id", "")
    migrated.setdefault("status", "fail")
    migrated.setdefault("classification", "unknown")
    migrated.setdefault("changed_paths", [])
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    return migrated


def _migrate_file(
    path: Path,
    migrator: Callable[[dict[str, Any]], dict[str, Any]],
    dry_run: bool,
    project_dir: Path,
    schema_name: str,
    collection_key: str = "",
) -> dict[str, Any]:
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "from": None,
            "to": CURRENT_SCHEMA_VERSION,
            "changed": False,
            "status": "error",
            "error": f"invalid or unreadable JSON: {exc}",
        }
    if not isinstance(before, dict):
        return {
            "path": str(path),
            "from": None,
            "to": CURRENT_SCHEMA_VERSION,
            "changed": False,
            "status": "error",
            "error": "top-level state must be a JSON object",
        }
    raw_version = before.get("schema_version", 0)
    try:
        source_version = int(raw_version or 0)
    except (TypeError, ValueError):
        return {
            "path": str(path),
            "from": None,
            "to": CURRENT_SCHEMA_VERSION,
            "changed": False,
            "status": "error",
            "error": f"invalid schema_version: {raw_version!r}",
        }
    if source_version > CURRENT_SCHEMA_VERSION:
        return {
            "path": str(path),
            "from": source_version,
            "to": CURRENT_SCHEMA_VERSION,
            "changed": False,
            "status": "skipped_future",
        }
    after = migrator(before)
    try:
        if collection_key:
            values = after.get(collection_key, [])
            if not isinstance(values, list):
                raise schema_validation.SchemaValidationError(f"$.{collection_key}: expected array")
            schema_validation.validate_items(values, schema_name, project_dir, f"$.{collection_key}")
        else:
            schema_validation.validate(after, schema_name, project_dir)
    except schema_validation.SchemaValidationError as exc:
        return {
            "path": str(path), "from": source_version, "to": CURRENT_SCHEMA_VERSION,
            "changed": False, "status": "error", "error": f"migrated state failed Schema validation: {exc}",
        }
    changed = after != before
    if changed and not dry_run:
        state_store.write_json(path, after)
    return {
        "path": str(path),
        "from": source_version,
        "to": CURRENT_SCHEMA_VERSION,
        "changed": changed,
        "status": "would_migrate" if changed and dry_run else "migrated" if changed else "unchanged",
    }


def migrate_project(project_dir: Path, dry_run: bool = False, governance_locked: bool = False) -> dict[str, Any]:
    state_root = project_dir / ".rpi-outfile/state"
    product_root = project_dir / ".rpi-outfile/product"
    results: list[dict[str, Any]] = []
    governance_context = nullcontext() if governance_locked else state_store.exclusive_lock(state_root / "governance-transaction")
    with governance_context:
        with state_store.exclusive_lock(state_root / "migration-transaction"):
            changes_dir = state_root / "changes"
            with state_store.exclusive_lock(changes_dir / ".transaction"):
                if changes_dir.exists():
                    for path in sorted(changes_dir.glob("CHG-*.json")):
                        results.append(_migrate_file(path, migrate_change, dry_run, project_dir, "change-impact.schema.json"))
                    latest = changes_dir / "latest.json"
                    if latest.exists():
                        results.append(_migrate_file(latest, migrate_change, dry_run, project_dir, "change-impact.schema.json"))
            cap_path = product_root / "capabilities.json"
            inv_path = product_root / "invariants.json"
            if cap_path.exists():
                results.append(_migrate_file(cap_path, migrate_capabilities, dry_run, project_dir, "capability.schema.json", "capabilities"))
            if inv_path.exists():
                results.append(_migrate_file(inv_path, migrate_invariants, dry_run, project_dir, "invariant.schema.json", "invariants"))
            reconciliation_dir = state_root / "reconciliation"
            if reconciliation_dir.exists():
                for path in sorted(reconciliation_dir.glob("*.json")):
                    results.append(_migrate_file(path, migrate_reconciliation, dry_run, project_dir, "reconciliation.schema.json"))
            report = {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "migrated_at": utc_now(),
                "dry_run": dry_run,
                "changed_count": sum(1 for item in results if item["changed"]),
                "error_count": sum(1 for item in results if item.get("status") == "error"),
                "skipped_future_count": sum(1 for item in results if item.get("status") == "skipped_future"),
                "files": results,
            }
            if not dry_run:
                schema_validation.validate(report, "migration-report.schema.json", project_dir)
                state_store.write_json(state_root / "migrations/latest.json", report)
            return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI governance state migrations")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = migrate_project(args.project_dir.resolve(), args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
