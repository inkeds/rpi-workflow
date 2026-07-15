#!/usr/bin/env python3
"""Project-specific RPI governance registries and AGENTS.md routing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import change_intelligence
import state_store
import state_migrations
import schema_validation


MANAGED_START = "<!-- RPI:PROJECT-GOVERNANCE:START -->"
MANAGED_END = "<!-- RPI:PROJECT-GOVERNANCE:END -->"

ROUTES = {
    "identity": ("Identity", "账户、登录、恢复和会话", "AUTH-*"),
    "authorization": ("Authorization", "角色、权限、管理员能力和可见性", "AUTH-*"),
    "assets": ("Assets", "资产归属、共享、导出、删除和恢复", "ASSET-*"),
    "collaboration": ("Collaboration", "邀请、共享、共同编辑和冲突处理", "ASSET-*,AUTH-*"),
    "billing": ("Billing", "费用承担、额度、账本、退款和并发一致性", "COST-*,CREDIT-*"),
    "data": ("Data", "数据模型、迁移、备份、恢复和兼容性", "DATA-*"),
    "ai": ("AI", "模型调用、人工门禁、重试、成本和 Eval", "AI-*,COST-*"),
    "deployment": ("Operations", "部署、配置、升级、监控和恢复", "OPS-*"),
    "privacy": ("Privacy", "敏感数据、审计、合规和内容边界", "PRIVACY-*"),
}

SKIP_DIRS = {".git", ".rpi-outfile", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".sql", ".sh"}
INVARIANT_TOPICS = {
    "authorization_scope": ("AUTH", "权限范围遵循已确认的最小授权或委托管理模式"),
    "asset_ownership_visibility": ("ASSET", "资产所有权和可见性遵循已确认的主体边界"),
    "cost_and_billing_model": ("COST", "费用承担和账本语义遵循已确认的成本模式"),
    "product_delivery_model": ("OPS", "交付和运营形态遵循已确认的产品模式"),
}
CAPABILITY_NOISE = ("新增", "增加", "添加", "支持", "允许", "实现", "功能", "优化", "修改", "调整", "当前任务", "这个任务", "再加")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    return state_store.read_json(path, default)


def write_json(path: Path, payload: Any) -> None:
    state_store.write_json(path, payload)


def validate_registry_documents(project_dir: Path, capabilities: list[Any], invariants: list[Any]) -> None:
    schema_validation.validate_items(capabilities, "capability.schema.json", project_dir, "$.capabilities")
    schema_validation.validate_items(invariants, "invariant.schema.json", project_dir, "$.invariants")


def stable_id(prefix: str, text: str) -> str:
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:10]}"


def capability_tokens(text: str) -> set[str]:
    normalized = text.lower()
    for noise in CAPABILITY_NOISE:
        normalized = normalized.replace(noise.lower(), " ")
    tokens = set(re.findall(r"[a-z0-9_+-]{2,}", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        tokens.add(chunk)
        tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return {item for item in tokens if item.strip()}


def capability_similarity(left: str, right: str) -> float:
    left_tokens = capability_tokens(left)
    right_tokens = capability_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def find_matching_capability(capabilities: Sequence[dict[str, Any]], request: str, domains: Sequence[str]) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    requested_domains = set(domains)
    for capability in capabilities:
        if not isinstance(capability, dict) or capability.get("status") == "retired":
            continue
        existing_domains = set(str(item) for item in capability.get("affected_domains", []) if str(item)) if isinstance(capability.get("affected_domains", []), list) else set()
        if requested_domains and existing_domains and not (requested_domains & existing_domains):
            continue
        candidates = [str(capability.get("name", "")), str(capability.get("user_outcome", ""))]
        candidates.extend(str(item) for item in capability.get("aliases", []) if str(item)) if isinstance(capability.get("aliases", []), list) else None
        score = max((capability_similarity(request, candidate) for candidate in candidates if candidate), default=0.0)
        if score > best_score:
            best = capability
            best_score = score
    return (best, best_score) if best_score >= 0.42 else (None, best_score)


def decomposition_assessment(request: str, domains: Sequence[str], change_id: str) -> dict[str, Any]:
    high_impact = sorted(set(domains) & {"authorization", "assets", "collaboration", "billing", "privacy"})
    reasons: list[str] = []
    if len(high_impact) >= 3:
        reasons.append("crosses_three_or_more_high_impact_domains")
    if "同时" in request or "另一个" in request:
        reasons.append("contains_multiple_outcome_connector")
    return {
        "status": "required" if reasons else "not_required",
        "reasons": reasons,
        "suggested_slices": high_impact if reasons else [],
        "source_changes": [change_id] if reasons and change_id else [],
    }


def capability_registry_path(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile" / "product" / "capabilities.json"


def invariant_registry_path(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile" / "product" / "invariants.json"


def ensure_layout(project_dir: Path) -> None:
    cap_path = capability_registry_path(project_dir)
    inv_path = invariant_registry_path(project_dir)
    if not cap_path.exists():
        write_json(cap_path, {"schema_version": 2, "updated_at": utc_now(), "capabilities": []})
    if not inv_path.exists():
        write_json(inv_path, {"schema_version": 2, "updated_at": utc_now(), "invariants": []})


def _audit_bucket(path: Path, rel: str) -> str:
    lowered = rel.lower()
    suffix = path.suffix.lower()
    if re.search(r"(^|/)(tests?|specs?)(/|_)|test_|_test\.", lowered):
        return "tests"
    if re.search(r"(^|/)(migrations?|schema)(/|\.)", lowered):
        return "migrations"
    if any(token in lowered for token in ("docker", "deploy", "compose", "k8s", "helm", "terraform", "ci/", "workflows/")):
        return "deployment"
    if suffix == ".md" and any(token in lowered for token in ("readme", "prd", "require", "需求", "discovery", "epic")):
        return "requirements"
    if suffix == ".md" or any(token in lowered for token in ("design", "architecture", "adr", "spec")):
        return "design"
    return "code" if suffix in TEXT_SUFFIXES - {".md", ".txt"} else ""


def _audit_document(inventory: dict[str, list[str]]) -> dict[str, Any]:
    has_code = bool(inventory["code"])
    has_design = bool(inventory["requirements"] or inventory["design"])
    project_state = "existing_code" if has_code else "design_only" if has_design else "new"
    conflicts: list[str] = []
    if len([item for item in inventory["requirements"] if "readme" in item.lower()]) > 1:
        conflicts.append("multiple README-like requirement entry points require explicit routing")
    return {
        "schema_version": 2,
        "audited_at": utc_now(),
        "project_state": project_state,
        "inventory": {key: sorted(values) for key, values in inventory.items()},
        "conflicts": conflicts,
    }


def audit_project(project_dir: Path) -> dict[str, Any]:
    audit, _, _ = _scan_project(project_dir, [], use_index=False)
    return audit


def project_index_path(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile/state/index/project-files.json"


def _route_bucket(path: Path, rel: str) -> str:
    lowered_rel = rel.lower()
    if re.search(r"(^|/)(tests?|specs?)(/|_)|test_|_test\.", lowered_rel):
        return "tests"
    if re.search(r"(^|/)(migrations?|schema)(/|\.)", lowered_rel):
        return "migrations"
    if path.suffix.lower() == ".md" or "/docs/" in f"/{lowered_rel}":
        return "design"
    return "code"


def _scan_project(
    project_dir: Path,
    domains: Sequence[str],
    use_index: bool = True,
) -> tuple[dict[str, Any], dict[str, dict[str, list[str]]], dict[str, Any]]:
    routes = {domain: {"design": [], "code": [], "tests": [], "migrations": []} for domain in domains if domain in ROUTES}
    inventory = {"requirements": [], "design": [], "code": [], "tests": [], "migrations": [], "deployment": []}
    cache_path = project_index_path(project_dir)
    previous = read_json(cache_path, {"entries": {}}) if use_index else {"entries": {}}
    cache_rebuilt = False
    if use_index and cache_path.exists():
        try:
            schema_validation.validate(previous, "project-index.schema.json", project_dir)
        except schema_validation.SchemaValidationError:
            previous = {"entries": {}}
            cache_rebuilt = True
    old_entries = previous.get("entries", {}) if isinstance(previous, dict) and isinstance(previous.get("entries"), dict) else {}
    entries: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = {"files": 0, "cache_hits": 0, "cache_misses": 0, "skipped": 0, "walks": 1, "cache_rebuilt": cache_rebuilt}
    project_root = project_dir.resolve()
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS and not (Path(root) / item).is_symlink()]
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.is_symlink():
                stats["skipped"] += 1
                continue
            rel = str(path.relative_to(project_dir))
            stats["files"] += 1
            audit_bucket = _audit_bucket(path, rel)
            if audit_bucket:
                inventory[audit_bucket].append(rel)
            if not routes:
                continue
            try:
                stat = path.stat()
                if stat.st_size > 512_000 or project_root not in path.resolve().parents:
                    stats["skipped"] += 1
                    continue
            except OSError:
                stats["skipped"] += 1
                continue
            cached = old_entries.get(rel, {}) if isinstance(old_entries.get(rel), dict) else {}
            if cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("ctime_ns") == stat.st_ctime_ns and cached.get("size") == stat.st_size:
                detected = [str(item) for item in cached.get("domains", [])]
                bucket = str(cached.get("bucket", _route_bucket(path, rel)))
                stats["cache_hits"] += 1
            else:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")[:120_000]
                except OSError:
                    stats["skipped"] += 1
                    continue
                detected = change_intelligence.detect_domains(f"{rel}\n{content}")
                bucket = _route_bucket(path, rel)
                stats["cache_misses"] += 1
            entries[rel] = {"mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns, "size": stat.st_size, "domains": detected, "bucket": bucket}
            matched = set(detected) & set(routes)
            for domain in matched:
                if len(routes[domain][bucket]) < 5 and rel not in routes[domain][bucket]:
                    routes[domain][bucket].append(rel)
    if use_index and routes and (entries != old_entries or cache_rebuilt):
        index_doc = {"schema_version": 1, "updated_at": utc_now(), "entries": entries}
        schema_validation.validate(index_doc, "project-index.schema.json", project_dir)
        write_json(cache_path, index_doc)
    return _audit_document(inventory), routes, stats


def _discover_routes(project_dir: Path, domains: Sequence[str]) -> tuple[dict[str, dict[str, list[str]]], dict[str, Any]]:
    _, routes, stats = _scan_project(project_dir, domains)
    return routes, stats


def _update_registries_from_change(project_dir: Path) -> None:
    change = read_json(project_dir / ".rpi-outfile/state/changes/latest.json", {})
    if not isinstance(change, dict) or change.get("status") not in {"spec_update_required", "active", "reconciled", "closed"}:
        return
    decisions = change.get("decisions_required", []) if isinstance(change.get("decisions_required", []), list) else []
    if any(isinstance(item, dict) and item.get("status") != "confirmed" for item in decisions):
        return

    inv_doc = read_json(invariant_registry_path(project_dir), {"schema_version": 1, "invariants": []})
    invariants = inv_doc.get("invariants", []) if isinstance(inv_doc, dict) else []
    inv_by_id = {str(item.get("id", "")): item for item in invariants if isinstance(item, dict)}
    change_inv_ids: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("status") != "confirmed":
            continue
        topic = str(decision.get("topic", ""))
        if topic not in INVARIANT_TOPICS:
            continue
        prefix, title = INVARIANT_TOPICS[topic]
        inv_id = stable_id(prefix, f"{topic}:{decision.get('selected_option', '')}")
        change_inv_ids.append(inv_id)
        inv_by_id[inv_id] = {
            "id": inv_id,
            "title": title,
            "status": "candidate",
            "selected_option": decision.get("selected_option"),
            "scope": [],
            "source": [str(decision.get("decision_id", "")), str(change.get("change_id", ""))],
            "enforcement": {"tests": [], "constraints": [], "static_checks": []},
            "change_policy": "explicit_decision_required",
        }
    inv_doc = {"schema_version": 2, "updated_at": utc_now(), "invariants": list(inv_by_id.values())}
    validate_registry_documents(project_dir, [], inv_doc["invariants"])
    write_json(invariant_registry_path(project_dir), inv_doc)

    cap_doc = read_json(capability_registry_path(project_dir), {"schema_version": 1, "capabilities": []})
    capabilities = cap_doc.get("capabilities", []) if isinstance(cap_doc, dict) else []
    cap_by_id = {str(item.get("id", "")): item for item in capabilities if isinstance(item, dict)}
    change_id = str(change.get("change_id", ""))
    request_text = str(change.get("request_text", ""))
    domains = [str(item) for item in change.get("affected_domains", [])] if isinstance(change.get("affected_domains", []), list) else []
    generic_only = bool(domains) and set(domains).issubset({"identity", "authorization", "deployment", "privacy", "billing"})
    matched, match_score = find_matching_capability(list(cap_by_id.values()), request_text, domains)
    decomposition = decomposition_assessment(request_text, domains, change_id)
    if matched is not None:
        cap_id = str(matched["id"])
        already_linked = change_id in matched.get("source_changes", [])
        if request_text and request_text not in {matched.get("name"), matched.get("user_outcome")}:
            matched["aliases"] = sorted(set([*matched.get("aliases", []), request_text]))
        matched["source_claims"] = sorted(set([*matched.get("source_claims", []), change_id]))
        matched["source_changes"] = sorted(set([*matched.get("source_changes", []), change_id]))
        matched["invariants"] = sorted(set([*matched.get("invariants", []), *change_inv_ids]))
        matched["spec_refs"] = sorted(
            set([*matched.get("spec_refs", []), *([str(item) for item in change.get("affected_specs", [])] if isinstance(change.get("affected_specs", []), list) else [])])
        )
        matched["affected_domains"] = sorted(set([*matched.get("affected_domains", []), *domains]))
        broad_change = decomposition["status"] == "required"
        matched["decomposition_review"] = bool(matched.get("decomposition_review")) or broad_change
        current_decomposition = matched.get("decomposition", {}) if isinstance(matched.get("decomposition"), dict) else {}
        if broad_change:
            matched["decomposition"] = {
                "status": "required",
                "reasons": sorted(set([*current_decomposition.get("reasons", []), *decomposition["reasons"]])),
                "suggested_slices": sorted(set([*current_decomposition.get("suggested_slices", []), *decomposition["suggested_slices"]])),
                "source_changes": sorted(set([*current_decomposition.get("source_changes", []), *decomposition["source_changes"]])),
            }
        elif not current_decomposition:
            matched["decomposition"] = decomposition
        if not already_linked:
            matched.setdefault("merge_history", []).append({"change_id": change_id, "matched_at": utc_now(), "similarity": round(match_score, 3)})
        cap_by_id[cap_id] = matched
    else:
        cap_id = stable_id("CAP", change_id or request_text)
        cap_by_id[cap_id] = {
            "id": cap_id,
            "name": request_text[:120],
            "classification": "supporting" if generic_only else "core",
            "classification_basis": "candidate derived from an accepted change; review before promotion",
            "confidence": "medium",
            "user_outcome": request_text,
            "source_claims": [change_id],
            "source_changes": [change_id],
            "status": "candidate",
            "dependencies": [],
            "invariants": change_inv_ids,
            "spec_refs": [str(item) for item in change.get("affected_specs", [])] if isinstance(change.get("affected_specs", []), list) else [],
            "test_refs": [],
            "aliases": [],
            "affected_domains": domains,
            "decomposition_review": decomposition["status"] == "required",
            "decomposition": decomposition,
        }
    for inv_id in change_inv_ids:
        if inv_id in inv_by_id:
            inv_by_id[inv_id]["scope"] = sorted(set([*inv_by_id[inv_id].get("scope", []), cap_id]))
    change["affected_capabilities"] = [cap_id]
    change["affected_invariants"] = change_inv_ids
    schema_validation.validate(change, "change-impact.schema.json", project_dir)
    validate_registry_documents(project_dir, list(cap_by_id.values()), list(inv_by_id.values()))
    change_path = project_dir / ".rpi-outfile/state/changes" / f"{change_id}.json"
    write_json(change_path, change)
    write_json(project_dir / ".rpi-outfile/state/changes/latest.json", change)
    write_json(invariant_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "invariants": list(inv_by_id.values())})
    write_json(capability_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "capabilities": list(cap_by_id.values())})


def _project_text(project_dir: Path) -> str:
    candidates = [
        project_dir / ".rpi-outfile/product/current_facts.json",
        project_dir / ".rpi-outfile/state/changes/latest.json",
        project_dir / ".rpi-outfile/specs/l0/discovery.md",
        project_dir / ".rpi-outfile/specs/l0/spec.md",
        project_dir / ".rpi-outfile/specs/l0/tasks.md",
    ]
    chunks: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _managed_section(project_dir: Path, domains: Sequence[str], routes: dict[str, dict[str, list[str]]]) -> str:
    lines = [
        MANAGED_START,
        "## RPI Project Governance",
        "",
        "- Product facts: `.rpi-outfile/product/current_facts.json`.",
        "- Capability registry: `.rpi-outfile/product/capabilities.json`.",
        "- Invariant registry: `.rpi-outfile/product/invariants.json`.",
        "- Current specifications: `.rpi-outfile/specs/`; implementation facts remain in code, migrations, configuration, tests, and runtime evidence.",
        "- Natural-language feature requests are proposed changes. Resolve `.rpi-outfile/state/changes/latest.json` before production implementation.",
        "- Do not convert implementation drift into a product decision automatically.",
        "",
        "### Project Knowledge Routing",
        "",
    ]
    active_domains = [domain for domain in domains if domain in ROUTES]
    if not active_domains:
        lines.append("- No project-specific domain route has enough evidence yet; read Discovery, current Spec, related code, migrations, and tests.")
    else:
        for domain in active_domains:
            title, purpose, invariants = ROUTES[domain]
            route = routes.get(domain, {})
            refs = []
            for bucket in ("design", "code", "migrations", "tests"):
                values = route.get(bucket, []) if isinstance(route, dict) else []
                if values:
                    refs.append(f"{bucket}=" + ",".join(f"`{item}`" for item in values[:3]))
            suffix = "; ".join(refs) if refs else "actual paths unresolved; inspect related code, migrations, and tests"
            lines.append(f"- **{title}**: {purpose}. Invariants `{invariants}`; {suffix}.")
    lines.extend([
        "",
        "### Change Maintenance",
        "",
        "- Local fixes update task evidence and tests; update design only when behavior or contract changes.",
        "- Feature changes update the current Spec and capability references before implementation.",
        "- Product-model or invariant changes require explicit decision evidence before implementation.",
        "- Task closure requires design/implementation reconciliation; unresolved excess behavior must not be normalized into the Spec.",
        MANAGED_END,
    ])
    return "\n".join(lines)


def update_agents(project_dir: Path, domains: Sequence[str], routes: dict[str, dict[str, list[str]]]) -> Path:
    path = project_dir / "AGENTS.md"
    agents_lock = project_dir / ".rpi-outfile/state/locks/agents"
    with state_store.exclusive_lock(agents_lock):
        existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "# AGENTS.md\n"
        section = _managed_section(project_dir, domains, routes)
        pattern = re.compile(re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END), re.DOTALL)
        if pattern.search(existing):
            updated = pattern.sub(section, existing)
        else:
            updated = existing.rstrip() + "\n\n" + section + "\n"
        if updated != existing:
            state_store.write_text_unlocked(path, updated)
    return path


def build_governance(project_dir: Path) -> dict[str, Any]:
    transaction = project_dir / ".rpi-outfile/state/governance-transaction"
    with state_store.exclusive_lock(transaction):
        journal = project_dir / ".rpi-outfile/state/transactions/governance.json"
        state_store.recover_transaction(journal, allowed_root=project_dir)
        migration = state_migrations.migrate_project(project_dir, governance_locked=True)
        if migration["error_count"]:
            raise RuntimeError("governance state migration failed; inspect .rpi-outfile/state/migrations/latest.json")
        if migration["skipped_future_count"]:
            raise RuntimeError("governance state uses a newer Schema version; upgrade RPI before building governance")
        changes_transaction = project_dir / ".rpi-outfile/state/changes/.transaction"
        with state_store.exclusive_lock(changes_transaction):
            latest_change = read_json(project_dir / ".rpi-outfile/state/changes/latest.json", {})
            change_id = str(latest_change.get("change_id", "")) if isinstance(latest_change, dict) else ""
            governed_paths = [
                capability_registry_path(project_dir),
                invariant_registry_path(project_dir),
                project_dir / ".rpi-outfile/product/material-audit.json",
                project_dir / ".rpi-outfile/state/changes/latest.json",
                project_index_path(project_dir),
                project_dir / "AGENTS.md",
            ]
            if change_id:
                governed_paths.append(project_dir / ".rpi-outfile/state/changes" / f"{change_id}.json")
            with state_store.atomic_file_transaction(journal, governed_paths, root=project_dir):
                ensure_layout(project_dir)
                _update_registries_from_change(project_dir)
                domains = change_intelligence.detect_domains(_project_text(project_dir))
                audit, routes, index_stats = _scan_project(project_dir, domains)
                write_json(project_dir / ".rpi-outfile/product/material-audit.json", audit)
                agents_path = update_agents(project_dir, domains, routes)
        return {
            "status": "built",
            "migration_changed_count": migration["changed_count"],
            "domains": domains,
            "project_state": audit["project_state"],
            "material_audit": str(project_dir / ".rpi-outfile/product/material-audit.json"),
            "routes": routes,
            "index": index_stats,
            "agents_file": str(agents_path),
            "capability_registry": str(capability_registry_path(project_dir)),
            "invariant_registry": str(invariant_registry_path(project_dir)),
        }


def verify_governance(project_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    capabilities_doc = read_json(capability_registry_path(project_dir), {"capabilities": []})
    invariants_doc = read_json(invariant_registry_path(project_dir), {"invariants": []})
    capabilities = capabilities_doc.get("capabilities", []) if isinstance(capabilities_doc, dict) else []
    invariants = invariants_doc.get("invariants", []) if isinstance(invariants_doc, dict) else []
    try:
        validate_registry_documents(project_dir, capabilities if isinstance(capabilities, list) else [], invariants if isinstance(invariants, list) else [])
    except schema_validation.SchemaValidationError as exc:
        errors.append(f"Schema validation failed: {exc}")
    cap_ids = {str(item.get("id", "")) for item in capabilities if isinstance(item, dict)}
    inv_ids = {str(item.get("id", "")) for item in invariants if isinstance(item, dict)}
    if len(cap_ids) != len([item for item in capabilities if isinstance(item, dict)]):
        errors.append("duplicate capability ids detected")
    if len(inv_ids) != len([item for item in invariants if isinstance(item, dict)]):
        errors.append("duplicate invariant ids detected")

    for cap in capabilities:
        if not isinstance(cap, dict):
            errors.append("capability entry must be an object")
            continue
        cap_id = str(cap.get("id", ""))
        if not re.fullmatch(r"CAP-[A-Za-z0-9_-]+", cap_id):
            errors.append(f"invalid capability id: {cap_id or '<empty>'}")
        for dependency in cap.get("dependencies", []) if isinstance(cap.get("dependencies", []), list) else []:
            if str(dependency) not in cap_ids:
                errors.append(f"{cap_id} references unknown capability dependency {dependency}")
        for invariant in cap.get("invariants", []) if isinstance(cap.get("invariants", []), list) else []:
            if str(invariant) not in inv_ids:
                errors.append(f"{cap_id} references unknown invariant {invariant}")
        decomposition = cap.get("decomposition", {})
        if cap.get("decomposition_review") and not isinstance(decomposition, dict):
            errors.append(f"{cap_id} decomposition review lacks structured assessment")
        elif isinstance(decomposition, dict) and decomposition.get("status") == "required" and not decomposition.get("reasons"):
            errors.append(f"{cap_id} decomposition review has no reason")

    for invariant in invariants:
        if not isinstance(invariant, dict):
            errors.append("invariant entry must be an object")
            continue
        inv_id = str(invariant.get("id", ""))
        if not re.fullmatch(r"(?:AUTH|ASSET|DATA|AI|COST|CREDIT|PRIVACY|OPS)-[A-Za-z0-9_-]+", inv_id):
            errors.append(f"invalid invariant id: {inv_id or '<empty>'}")

    dependency_graph = {
        str(cap.get("id", "")): [str(item) for item in cap.get("dependencies", [])]
        for cap in capabilities
        if isinstance(cap, dict) and isinstance(cap.get("dependencies", []), list)
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(cap_id: str) -> None:
        if cap_id in visiting:
            errors.append(f"capability dependency cycle detected at {cap_id}")
            return
        if cap_id in visited:
            return
        visiting.add(cap_id)
        for dependency in dependency_graph.get(cap_id, []):
            if dependency in dependency_graph:
                visit(dependency)
        visiting.remove(cap_id)
        visited.add(cap_id)

    for cap_id in dependency_graph:
        visit(cap_id)

    agents_path = project_dir / "AGENTS.md"
    if not agents_path.exists():
        warnings.append("AGENTS.md is missing")
    elif MANAGED_START not in agents_path.read_text(encoding="utf-8", errors="ignore"):
        warnings.append("AGENTS.md has no generated project-governance routing section")

    return {"status": "pass" if not errors else "fail", "errors": errors, "warnings": warnings}


def _rewrite_capability_refs(project_dir: Path, replacements: dict[str, list[str]], capabilities: list[dict[str, Any]], invariants: list[dict[str, Any]]) -> list[Path]:
    changed_paths: list[Path] = []
    for capability in capabilities:
        dependencies = capability.get("dependencies", []) if isinstance(capability.get("dependencies"), list) else []
        rewritten: list[str] = []
        for dependency in dependencies:
            rewritten.extend(replacements.get(str(dependency), [str(dependency)]))
        capability["dependencies"] = sorted(set(item for item in rewritten if item != capability.get("id")))
    for invariant in invariants:
        scope = invariant.get("scope", []) if isinstance(invariant.get("scope"), list) else []
        rewritten = []
        for capability_id in scope:
            rewritten.extend(replacements.get(str(capability_id), [str(capability_id)]))
        invariant["scope"] = sorted(set(rewritten))
    changes_dir = project_dir / ".rpi-outfile/state/changes"
    for path in sorted(changes_dir.glob("CHG-*.json")) if changes_dir.exists() else []:
        change = read_json(path, {})
        if not isinstance(change, dict):
            continue
        refs = change.get("affected_capabilities", []) if isinstance(change.get("affected_capabilities"), list) else []
        rewritten = []
        for capability_id in refs:
            rewritten.extend(replacements.get(str(capability_id), [str(capability_id)]))
        if rewritten != refs:
            change["affected_capabilities"] = sorted(set(rewritten))
            schema_validation.validate(change, "change-impact.schema.json", project_dir)
            write_json(path, change)
            changed_paths.append(path)
            latest = changes_dir / "latest.json"
            latest_doc = read_json(latest, {})
            if isinstance(latest_doc, dict) and latest_doc.get("change_id") == change.get("change_id"):
                write_json(latest, change)
                changed_paths.append(latest)
    return changed_paths


@contextmanager
def capability_mutation(project_dir: Path):
    governance_lock = project_dir / ".rpi-outfile/state/governance-transaction"
    changes_lock = project_dir / ".rpi-outfile/state/changes/.transaction"
    with state_store.exclusive_lock(governance_lock):
        with state_store.exclusive_lock(changes_lock):
            changes_dir = project_dir / ".rpi-outfile/state/changes"
            paths = [capability_registry_path(project_dir), invariant_registry_path(project_dir), changes_dir / "latest.json"]
            paths.extend(sorted(changes_dir.glob("CHG-*.json")) if changes_dir.exists() else [])
            journal = project_dir / ".rpi-outfile/state/transactions/capability-mutation.json"
            with state_store.atomic_file_transaction(journal, paths, root=project_dir):
                yield


def merge_capabilities(project_dir: Path, target_id: str, source_ids: Sequence[str], evidence: str) -> dict[str, Any]:
    if not evidence.strip() or not source_ids:
        raise ValueError("merge requires source capabilities and evidence")
    with capability_mutation(project_dir):
        cap_doc = read_json(capability_registry_path(project_dir), {"schema_version": 2, "capabilities": []})
        inv_doc = read_json(invariant_registry_path(project_dir), {"schema_version": 2, "invariants": []})
        capabilities = [item for item in cap_doc.get("capabilities", []) if isinstance(item, dict)]
        invariants = [item for item in inv_doc.get("invariants", []) if isinstance(item, dict)]
        by_id = {str(item.get("id", "")): item for item in capabilities}
        if target_id not in by_id:
            raise ValueError(f"unknown target capability: {target_id}")
        sources = [source for source in dict.fromkeys(source_ids) if source != target_id]
        if not sources:
            raise ValueError("merge requires at least one source different from the target")
        missing = [source for source in sources if source not in by_id]
        if missing:
            raise ValueError(f"unknown source capabilities: {', '.join(missing)}")
        target = by_id[target_id]
        for source_id in sources:
            source = by_id[source_id]
            for key in ("aliases", "source_claims", "source_changes", "dependencies", "invariants", "spec_refs", "test_refs", "affected_domains"):
                target[key] = sorted(set([*target.get(key, []), *source.get(key, [])]))
            target["aliases"] = sorted(set([*target.get("aliases", []), str(source.get("name", ""))]) - {""})
            source["status"] = "retired"
            source["superseded_by"] = target_id
        target.setdefault("governance_history", []).append({"action": "manual_merge", "sources": sources, "evidence": evidence.strip(), "at": utc_now()})
        _rewrite_capability_refs(project_dir, {source: [target_id] for source in sources}, capabilities, invariants)
        validate_registry_documents(project_dir, capabilities, invariants)
        write_json(capability_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "capabilities": capabilities})
        write_json(invariant_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "invariants": invariants})
        return {"status": "merged", "target": target_id, "sources": sources}


def split_capability(project_dir: Path, capability_id: str, slices: Sequence[str], evidence: str) -> dict[str, Any]:
    clean_slices = list(dict.fromkeys(item.strip() for item in slices if item.strip()))
    if len(clean_slices) < 2 or not evidence.strip():
        raise ValueError("split requires at least two slices and evidence")
    with capability_mutation(project_dir):
        cap_doc = read_json(capability_registry_path(project_dir), {"schema_version": 2, "capabilities": []})
        inv_doc = read_json(invariant_registry_path(project_dir), {"schema_version": 2, "invariants": []})
        capabilities = [item for item in cap_doc.get("capabilities", []) if isinstance(item, dict)]
        invariants = [item for item in inv_doc.get("invariants", []) if isinstance(item, dict)]
        by_id = {str(item.get("id", "")): item for item in capabilities}
        if capability_id not in by_id:
            raise ValueError(f"unknown capability: {capability_id}")
        parent = by_id[capability_id]
        child_ids: list[str] = []
        for slice_name in clean_slices:
            child_id = stable_id("CAP", f"{capability_id}:{slice_name}")
            if child_id in by_id:
                raise ValueError(f"split child already exists: {child_id}")
            child_ids.append(child_id)
            capabilities.append({
                "id": child_id, "name": slice_name, "classification": parent.get("classification", "supporting"),
                "classification_basis": f"manual split from {capability_id}", "confidence": "high",
                "user_outcome": slice_name, "source_claims": list(parent.get("source_claims", [])),
                "source_changes": list(parent.get("source_changes", [])), "status": "candidate", "dependencies": [],
                "invariants": list(parent.get("invariants", [])), "spec_refs": list(parent.get("spec_refs", [])),
                "test_refs": list(parent.get("test_refs", [])), "aliases": [],
                "affected_domains": list(parent.get("affected_domains", [])), "decomposition_review": False,
                "decomposition": {"status": "not_required", "reasons": [], "suggested_slices": [], "source_changes": []},
                "split_from": capability_id,
            })
        parent["status"] = "retired"
        parent["split_into"] = child_ids
        parent["decomposition_review"] = False
        prior_decomposition = parent.get("decomposition", {}) if isinstance(parent.get("decomposition"), dict) else {}
        parent["decomposition"] = {
            "reasons": list(prior_decomposition.get("reasons", [])),
            "suggested_slices": list(prior_decomposition.get("suggested_slices", [])),
            "source_changes": list(prior_decomposition.get("source_changes", [])),
            **prior_decomposition,
            "status": "resolved",
            "resolved_at": utc_now(),
            "evidence": evidence.strip(),
        }
        parent.setdefault("governance_history", []).append({"action": "manual_split", "children": child_ids, "evidence": evidence.strip(), "at": utc_now()})
        _rewrite_capability_refs(project_dir, {capability_id: child_ids}, capabilities, invariants)
        validate_registry_documents(project_dir, capabilities, invariants)
        write_json(capability_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "capabilities": capabilities})
        write_json(invariant_registry_path(project_dir), {"schema_version": 2, "updated_at": utc_now(), "invariants": invariants})
        return {"status": "split", "source": capability_id, "children": child_ids}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI project governance")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    sub.add_parser("verify")
    capability = sub.add_parser("capability")
    capability_sub = capability.add_subparsers(dest="capability_action", required=True)
    list_parser = capability_sub.add_parser("list")
    list_parser.add_argument("--status", default="")
    merge_parser = capability_sub.add_parser("merge")
    merge_parser.add_argument("target")
    merge_parser.add_argument("sources", nargs="+")
    merge_parser.add_argument("--evidence", required=True)
    split_parser = capability_sub.add_parser("split")
    split_parser.add_argument("capability_id")
    split_parser.add_argument("--slice", action="append", required=True)
    split_parser.add_argument("--evidence", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    if args.command == "build":
        print(json.dumps(build_governance(project_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "capability":
        if args.capability_action == "merge":
            report = merge_capabilities(project_dir, args.target, args.sources, args.evidence)
        elif args.capability_action == "split":
            report = split_capability(project_dir, args.capability_id, args.slice, args.evidence)
        else:
            doc = read_json(capability_registry_path(project_dir), {"capabilities": []})
            capabilities = doc.get("capabilities", []) if isinstance(doc, dict) else []
            if args.status:
                capabilities = [item for item in capabilities if isinstance(item, dict) and item.get("status") == args.status]
            report = {"status": "ok", "capabilities": capabilities}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    report = verify_governance(project_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
