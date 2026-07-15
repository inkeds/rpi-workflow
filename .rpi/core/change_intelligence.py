#!/usr/bin/env python3
"""Deterministic first-pass change classification for RPI.

The classifier does not approve product decisions.  It routes natural-language
requests into the appropriate governance path and emits a reviewable impact
record that can be linked to specs and tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import state_store
import schema_validation


DOMAIN_SIGNALS = {
    "identity": ("用户", "账户", "账号", "登录", "注册", "成员", "user", "account", "login"),
    "authorization": ("权限", "角色", "管理员", "可见", "查看", "代操作", "授权", "permission", "role", "admin"),
    "assets": ("文件", "资产", "项目", "内容", "所有者", "归属", "删除", "导出", "恢复", "file", "asset"),
    "collaboration": ("团队", "协作", "邀请", "共享", "共同编辑", "组织", "team", "share", "invite"),
    "billing": ("付费", "支付", "订阅", "账单", "价格", "计费", "积分", "额度", "余额", "billing", "payment"),
    "data": ("数据", "数据库", "迁移", "导入", "导出", "csv", "备份", "恢复", "编码", "乱码", "data"),
    "ai": ("模型", "提示词", "生成", "识别", "推理", "token", "eval", "ai"),
    "deployment": ("部署", "升级", "配置", "环境变量", "监控", "告警", "容灾", "deploy", "config"),
    "privacy": ("隐私", "敏感", "审计", "合规", "加密", "脱敏", "privacy", "audit"),
}

MUTATION_SIGNALS = (
    "新增", "增加", "添加", "支持", "允许", "改成", "修改", "调整", "删除", "移除", "替换",
    "修复", "解决", "优化", "重构", "实现", "接入", "升级", "迁移", "change", "add", "fix", "implement",
    "再加", "补充",
)
BUG_SIGNALS = ("修复", "bug", "错误", "异常", "失败", "乱码", "崩溃", "不生效", "无法")
EXPLORATION_SIGNALS = ("试试", "实验", "探索", "验证可行性", "原型", "spike", "对比模型")
PRODUCT_MODEL_SIGNALS = ("多租户", "saas", "组织付费", "组织统一付费", "平台供模", "用户自带 key", "商业化", "团队版", "私有部署")
INVARIANT_SIGNALS = ("资产归属", "所有权", "管理员读取全部", "代用户", "永久删除", "费用承担", "扣费", "退款")
QUESTION_PREFIXES = ("为什么", "如何", "怎么", "是否", "能否", "是什么", "请解释", "分析一下")
EXPLICIT_CONFIRMATIONS = (
    "确认以上决策",
    "确认以上 p0 决策",
    "确认以上P0决策",
    "按以上推荐方案继续",
    "确认该变更决策",
)
ACTIVE_TASK_LINK_SIGNALS = ("当前任务", "这个任务", "本任务", "在此基础上", "顺便", "同时再", "再加", "补充到当前")

HIGH_IMPACT_DOMAINS = {"authorization", "assets", "billing", "privacy", "collaboration"}
AUTHORITY_BASELINE_FILES = (
    ".rpi-outfile/product/current_facts.json",
    ".rpi-outfile/product/capabilities.json",
    ".rpi-outfile/product/invariants.json",
    ".rpi-outfile/state/project_phase.json",
)
DESIGN_BASELINE_FILES = (
    ".rpi-outfile/specs/l0/discovery.md",
    ".rpi-outfile/specs/l0/spec.md",
    ".rpi-outfile/specs/l0/milestones.md",
)
CONFLICT_RESOLUTIONS = {"preserve", "amend", "coexist", "deprecate", "split", "reject", "defer"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _file_digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _fingerprint(values: dict[str, str]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_baseline(project_dir: Path) -> dict[str, Any]:
    authority = {item: _file_digest(project_dir / item) for item in AUTHORITY_BASELINE_FILES}
    design = {item: _file_digest(project_dir / item) for item in DESIGN_BASELINE_FILES}
    phase_doc = state_store.read_json(project_dir / ".rpi-outfile/state/project_phase.json", {})
    phase = str(phase_doc.get("phase", "unknown")) if isinstance(phase_doc, dict) else "unknown"
    return {
        "captured_at": utc_now(),
        "phase": phase,
        "authority_fingerprint": _fingerprint(authority),
        "design_fingerprint": _fingerprint(design),
        "authority_files": authority,
        "design_files": design,
    }


def compare_baseline(project_dir: Path, baseline: Any) -> dict[str, Any]:
    current = capture_baseline(project_dir)
    if not isinstance(baseline, dict) or not baseline.get("authority_fingerprint"):
        return {"status": "missing", "authority_changed": False, "design_changed": False, "current": current}
    return {
        "status": "stale" if baseline.get("authority_fingerprint") != current["authority_fingerprint"] else "current",
        "authority_changed": baseline.get("authority_fingerprint") != current["authority_fingerprint"],
        "design_changed": baseline.get("design_fingerprint") != current["design_fingerprint"],
        "current": current,
    }


def _conflict(
    request: str,
    kind: str,
    severity: str,
    source_refs: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    refs = [str(item) for item in source_refs if str(item)]
    return {
        "conflict_id": stable_id("CNF", f"{request}:{kind}:{':'.join(refs)}"),
        "kind": kind,
        "severity": severity,
        "source_refs": refs,
        "proposed_behavior": request,
        "reason": reason,
        "status": "pending",
        "resolution": None,
        "resolution_evidence": [],
    }


def detect_governance_conflicts(project_dir: Path, request: str, domains: Sequence[str]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    inv_doc = state_store.read_json(project_dir / ".rpi-outfile/product/invariants.json", {"invariants": []})
    invariants = inv_doc.get("invariants", []) if isinstance(inv_doc, dict) else []
    lowered = request.lower()
    broad_access = _contains_any(request, ("管理员查看全部", "管理员读取全部", "查看所有", "删除成员", "代用户", "any user", "all assets"))
    billing_shift = _contains_any(request, ("用户付费", "组织付费", "组织统一付费", "改为付费", "取消退款", "user funded", "organization funded"))
    model_shift = _contains_any(request, PRODUCT_MODEL_SIGNALS)
    design_text_parts: list[tuple[str, str]] = []
    for relative in DESIGN_BASELINE_FILES:
        path = project_dir / relative
        try:
            if path.exists() and path.stat().st_size <= 512_000:
                design_text_parts.append((relative, path.read_text(encoding="utf-8", errors="ignore").lower()))
        except OSError:
            continue
    if broad_access and ("authorization" in domains or "assets" in domains):
        for relative, design_text in design_text_parts:
            if _contains_any(design_text, ("仅所有者", "只能所有者", "所有者可见", "owner only", "owner_scoped")):
                conflicts.append(_conflict(request, "design_semantics_review", "high", [relative], "拟议访问范围与当前设计文档中的所有者隔离规则可能相反"))
    if billing_shift and "billing" in domains:
        for relative, design_text in design_text_parts:
            if re.search(r"不做[^。\n]{0,12}支付|不支持[^。\n]{0,12}支付|暂不做[^。\n]{0,12}支付", design_text):
                conflicts.append(_conflict(request, "product_model_change", "high", [relative], "拟议计费能力与当前设计文档明确排除支付的范围相反"))
    for raw in invariants if isinstance(invariants, list) else []:
        if not isinstance(raw, dict) or raw.get("status") == "retired":
            continue
        inv_id = str(raw.get("id", ""))
        inv_text = " ".join(
            [str(raw.get("title", "")), str(raw.get("selected_option", "")), str(raw.get("change_policy", ""))]
        ).lower()
        if broad_access and ("authorization" in domains or "assets" in domains) and _contains_any(
            inv_text, ("最小授权", "least_privilege", "owner_scoped", "所有权", "可见性")
        ):
            conflicts.append(_conflict(request, "invariant_change", "high", [inv_id], "拟议访问范围可能反转当前授权或资产可见性不变量"))
        elif billing_shift and "billing" in domains and _contains_any(
            inv_text, ("platform_funded", "user_funded", "organization_funded", "费用承担", "账本")
        ):
            conflicts.append(_conflict(request, "invariant_change", "high", [inv_id], "拟议费用承担或账本语义与当前计费不变量可能不一致"))
        elif model_shift and _contains_any(inv_text, ("preserve_current_model", "交付", "运营形态", "产品模式")):
            conflicts.append(_conflict(request, "product_model_change", "high", [inv_id], "拟议交付模式可能替代当前产品模型"))

    cap_doc = state_store.read_json(project_dir / ".rpi-outfile/product/capabilities.json", {"capabilities": []})
    capabilities = cap_doc.get("capabilities", []) if isinstance(cap_doc, dict) else []
    removal_requested = _contains_any(request, ("删除", "移除", "取消", "下线", "废弃", "不再支持", "remove", "deprecate"))
    if removal_requested:
        request_tokens = set(re.findall(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", lowered))
        for raw in capabilities if isinstance(capabilities, list) else []:
            if not isinstance(raw, dict) or raw.get("status") == "retired":
                continue
            cap_text = f"{raw.get('name', '')} {raw.get('user_outcome', '')}".lower()
            if any(token in cap_text for token in request_tokens if len(token) >= 2):
                conflicts.append(
                    _conflict(request, "capability_deprecation", "medium", [str(raw.get("id", ""))], "请求可能删除或废弃现有用户能力，需要兼容窗口或显式退役决策")
                )
                break
    by_id = {item["conflict_id"]: item for item in conflicts}
    return list(by_id.values())


def _refresh_change_state(result: dict[str, Any]) -> None:
    decisions = result.get("decisions_required", []) if isinstance(result.get("decisions_required", []), list) else []
    conflicts = result.get("conflicts", []) if isinstance(result.get("conflicts", []), list) else []
    pending_decisions = any(isinstance(item, dict) and item.get("status") != "confirmed" for item in decisions)
    pending_conflicts = any(isinstance(item, dict) and item.get("status") == "pending" for item in conflicts)
    if pending_decisions or pending_conflicts:
        result["status"] = "pending_decision"
        result["implementation_allowed"] = False
        result["governance_level"] = "decision_required"
        result["next_action"] = "resolve_conflict" if pending_conflicts else "request_decision"
    elif result.get("repository_change_requested"):
        result["status"] = "spec_update_required"
        result["implementation_allowed"] = False
        result["next_action"] = "prepare_spec_and_task"


def _contains_any(text: str, signals: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(signal.lower() in lowered for signal in signals)


def detect_domains(text: str) -> list[str]:
    lowered = text.lower()
    return sorted(
        domain
        for domain, signals in DOMAIN_SIGNALS.items()
        if any(signal.lower() in lowered for signal in signals)
    )


def _is_question(text: str) -> bool:
    stripped = text.strip()
    has_question_form = stripped.endswith(("?", "？")) or stripped.startswith(QUESTION_PREFIXES)
    return has_question_form and not _contains_any(stripped, MUTATION_SIGNALS)


def is_explicit_confirmation(text: str) -> bool:
    normalized = re.sub(r"[。！!\s]+$", "", text.strip()).lower()
    if normalized in {item.lower() for item in EXPLICIT_CONFIRMATIONS}:
        return True
    return bool(re.fullmatch(r"确认变更\s*CHG-[A-Za-z0-9_-]+", normalized, flags=re.IGNORECASE))


def targets_active_task(text: str) -> bool:
    return _contains_any(text, ACTIVE_TASK_LINK_SIGNALS)


DECISION_TEMPLATES = {
    "authorization_scope": {
        "options": ["least_privilege", "delegated_admin"],
        "recommended_option": "least_privilege",
    },
    "asset_ownership_visibility": {
        "options": ["owner_scoped", "explicit_shared_scope"],
        "recommended_option": "owner_scoped",
    },
    "cost_and_billing_model": {
        "options": ["platform_funded", "user_funded", "organization_funded"],
        "recommended_option": "platform_funded",
    },
    "product_delivery_model": {
        "options": ["preserve_current_model", "adopt_proposed_model"],
        "recommended_option": "preserve_current_model",
    },
}


def _decision_cards(text: str, domains: Sequence[str], change_type: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []

    def add(topic: str, reason: str) -> None:
        template = DECISION_TEMPLATES[topic]
        cards.append(
            {
                "decision_id": stable_id("DEC", f"{text}:{topic}"),
                "topic": topic,
                "reason": reason,
                "status": "pending",
                "options": template["options"],
                "recommended_option": template["recommended_option"],
                "selected_option": None,
                "confirmation_evidence": [],
            }
        )

    if "authorization" in domains or _contains_any(text, INVARIANT_SIGNALS):
        add("authorization_scope", "角色可见性或代操作范围具有产品和安全后果")
    if "assets" in domains and ("collaboration" in domains or "authorization" in domains):
        add("asset_ownership_visibility", "需要确认资产主体、共享范围、删除和恢复责任")
    if "billing" in domains:
        add("cost_and_billing_model", "需要确认费用承担方、账本语义和失败返还")
    if change_type == "product_model_change":
        add("product_delivery_model", "产品交付、账户主体或运营模式正在改变")
    seen: set[str] = set()
    return [card for card in cards if not (card["topic"] in seen or seen.add(card["topic"]))]


def _documents_for(domains: Sequence[str], change_type: str) -> list[str]:
    docs = [".rpi-outfile/specs/l0/spec.md", ".rpi-outfile/specs/l0/tasks.md"]
    if change_type not in {"question", "diagnosis", "local_fix", "documentation_only"}:
        docs.append(".rpi-outfile/specs/l0/discovery.md")
    if set(domains) & HIGH_IMPACT_DOMAINS:
        docs.extend([
            ".rpi-outfile/specs/l0/business-capabilities.md",
            ".rpi-outfile/specs/l0/architecture-invariants.md",
        ])
    return list(dict.fromkeys(docs))


def analyze_change(text: str) -> dict[str, Any]:
    request = text.strip()
    if not request:
        raise ValueError("change request must not be empty")

    domains = detect_domains(request)
    repository_change_requested = _contains_any(request, MUTATION_SIGNALS)

    if _is_question(request):
        change_type = "question"
    elif _contains_any(request, EXPLORATION_SIGNALS):
        change_type = "exploration"
        repository_change_requested = True
    elif _contains_any(request, PRODUCT_MODEL_SIGNALS):
        change_type = "product_model_change"
        repository_change_requested = True
    elif _contains_any(request, INVARIANT_SIGNALS):
        change_type = "invariant_change"
        repository_change_requested = True
    elif _contains_any(request, BUG_SIGNALS) and repository_change_requested:
        change_type = "local_fix"
    elif repository_change_requested and len(set(domains) & HIGH_IMPACT_DOMAINS) >= 2:
        change_type = "cross_domain_change"
    elif repository_change_requested:
        change_type = "feature_change"
    elif _contains_any(request, BUG_SIGNALS):
        change_type = "diagnosis"
    else:
        change_type = "question"

    decisions = _decision_cards(request, domains, change_type)
    decision_required = change_type in {"product_model_change", "invariant_change"} or bool(decisions)
    implementation_allowed = repository_change_requested and change_type in {"local_fix", "exploration"} and not decision_required
    governance_level = "decision_required" if decision_required else (
        "spec_update_required" if change_type in {"feature_change", "cross_domain_change"} else "lightweight"
    )

    lifecycle_impacts: list[str] = []
    if "assets" in domains:
        lifecycle_impacts.extend(["ownership", "visibility", "deletion", "recovery"])
    if "identity" in domains:
        lifecycle_impacts.extend(["creation", "disablement", "recovery", "session"])
    if "billing" in domains:
        lifecycle_impacts.extend(["grant", "consume", "refund", "adjust", "expire"])

    return {
        "schema_version": 2,
        "change_id": stable_id("CHG", request),
        "analyzed_at": utc_now(),
        "request_text": request,
        "change_type": change_type,
        "confidence": "high" if change_type in {"question", "local_fix", "product_model_change", "invariant_change"} else "medium",
        "repository_change_requested": repository_change_requested,
        "affected_domains": domains,
        "affected_capabilities": [],
        "affected_invariants": [],
        "affected_specs": _documents_for(domains, change_type),
        "affected_code": [],
        "affected_tests": [],
        "lifecycle_impacts": list(dict.fromkeys(lifecycle_impacts)),
        "baseline": {},
        "baseline_history": [],
        "conflicts": [],
        "decisions_required": decisions,
        "documents_to_update": _documents_for(domains, change_type) if repository_change_requested else [],
        "governance_level": governance_level,
        "implementation_allowed": implementation_allowed,
        "status": (
            "pending_decision" if decision_required
            else "spec_update_required" if change_type in {"feature_change", "cross_domain_change"}
            else "ready" if repository_change_requested
            else "observed"
        ),
        "next_action": (
            "answer_read_only" if not repository_change_requested
            else "request_decision" if decision_required
            else "prepare_spec_and_task" if change_type == "feature_change"
            else "start_exploration" if change_type == "exploration"
            else "start_lightweight_task"
        ),
    }


def persist_analysis(project_dir: Path, result: dict[str, Any]) -> Path:
    result["baseline"] = capture_baseline(project_dir)
    result["conflicts"] = detect_governance_conflicts(
        project_dir,
        str(result.get("request_text", "")),
        result.get("affected_domains", []) if isinstance(result.get("affected_domains", []), list) else [],
    )
    _refresh_change_state(result)
    schema_validation.validate(result, "change-impact.schema.json", project_dir)
    changes_dir = project_dir / ".rpi-outfile" / "state" / "changes"
    changes_dir.mkdir(parents=True, exist_ok=True)
    path = changes_dir / f"{result['change_id']}.json"
    with state_store.exclusive_lock(changes_dir / ".transaction"):
        state_store.write_json_unlocked(path, result)
        state_store.write_json_unlocked(changes_dir / "latest.json", result)
    return path


def confirm_change(
    project_dir: Path,
    change_id: str,
    evidence: str,
    decision_id: str = "",
    option: str = "",
) -> dict[str, Any]:
    if not evidence.strip():
        raise ValueError("confirmation evidence must not be empty")
    changes_dir = project_dir / ".rpi-outfile" / "state" / "changes"
    path = changes_dir / f"{change_id}.json"
    with state_store.exclusive_lock(changes_dir / ".transaction"):
        result = state_store.read_json(path, {})
        if not result:
            raise ValueError(f"unknown change: {change_id}")
        decisions = result.get("decisions_required", [])
        if not isinstance(decisions, list) or not decisions:
            raise ValueError(f"change has no pending decisions: {change_id}")
        matched = False
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            current_id = str(decision.get("decision_id", ""))
            if decision_id and current_id != decision_id:
                continue
            selected = option.strip() or str(decision.get("recommended_option", ""))
            options = [str(item) for item in decision.get("options", [])] if isinstance(decision.get("options", []), list) else []
            if selected not in options:
                raise ValueError(f"invalid option for {current_id}: {selected}")
            decision["selected_option"] = selected
            decision["status"] = "confirmed"
            decision["confirmed_at"] = utc_now()
            decision["confirmation_evidence"] = sorted(set([*decision.get("confirmation_evidence", []), evidence.strip()]))
            matched = True
        if not matched:
            raise ValueError(f"unknown decision for change {change_id}: {decision_id}")
        pending = [item for item in decisions if isinstance(item, dict) and item.get("status") != "confirmed"]
        if not pending:
            result["confirmed_at"] = utc_now()
        _refresh_change_state(result)
        schema_validation.validate(result, "change-impact.schema.json", project_dir)
        state_store.write_json_unlocked(path, result)
        state_store.write_json_unlocked(changes_dir / "latest.json", result)
        return result


def resolve_conflict(
    project_dir: Path,
    change_id: str,
    conflict_id: str,
    resolution: str,
    evidence: str,
) -> dict[str, Any]:
    if resolution not in CONFLICT_RESOLUTIONS:
        raise ValueError(f"invalid conflict resolution: {resolution}")
    if not evidence.strip():
        raise ValueError("conflict resolution evidence must not be empty")
    changes_dir = project_dir / ".rpi-outfile/state/changes"
    path = changes_dir / f"{change_id}.json"
    with state_store.exclusive_lock(changes_dir / ".transaction"):
        result = state_store.read_json(path, {})
        if not result:
            raise ValueError(f"unknown change: {change_id}")
        matched = False
        conflicts = result.get("conflicts", []) if isinstance(result.get("conflicts", []), list) else []
        for conflict in conflicts:
            if not isinstance(conflict, dict) or str(conflict.get("conflict_id", "")) != conflict_id:
                continue
            conflict["status"] = "rejected" if resolution == "reject" else "deferred" if resolution == "defer" else "resolved"
            conflict["resolution"] = resolution
            conflict["resolved_at"] = utc_now()
            conflict["resolution_evidence"] = sorted(set([*conflict.get("resolution_evidence", []), evidence.strip()]))
            matched = True
        if not matched:
            raise ValueError(f"unknown conflict for change {change_id}: {conflict_id}")
        _refresh_change_state(result)
        schema_validation.validate(result, "change-impact.schema.json", project_dir)
        state_store.write_json_unlocked(path, result)
        state_store.write_json_unlocked(changes_dir / "latest.json", result)
        return result


def rebase_change(project_dir: Path, change_id: str, evidence: str) -> dict[str, Any]:
    if not evidence.strip():
        raise ValueError("rebase evidence must not be empty")
    changes_dir = project_dir / ".rpi-outfile/state/changes"
    path = changes_dir / f"{change_id}.json"
    with state_store.exclusive_lock(changes_dir / ".transaction"):
        result = state_store.read_json(path, {})
        if not result:
            raise ValueError(f"unknown change: {change_id}")
        unresolved = [item for item in result.get("conflicts", []) if isinstance(item, dict) and item.get("status") == "pending"]
        pending_decisions = [item for item in result.get("decisions_required", []) if isinstance(item, dict) and item.get("status") != "confirmed"]
        if unresolved or pending_decisions:
            raise ValueError("cannot rebase a change with unresolved conflicts or decisions")
        previous = result.get("baseline", {}) if isinstance(result.get("baseline", {}), dict) else {}
        history = result.get("baseline_history", []) if isinstance(result.get("baseline_history", []), list) else []
        if previous:
            history.append({**previous, "superseded_at": utc_now(), "evidence": evidence.strip()})
        result["baseline_history"] = history
        result["baseline"] = capture_baseline(project_dir)
        result["rebased_at"] = utc_now()
        result["rebase_evidence"] = evidence.strip()
        schema_validation.validate(result, "change-impact.schema.json", project_dir)
        state_store.write_json_unlocked(path, result)
        state_store.write_json_unlocked(changes_dir / "latest.json", result)
        return result


def cmd_analyze(project_dir: Path, text: str, persist: bool) -> int:
    result = analyze_change(text)
    if persist and result["repository_change_requested"]:
        path = persist_analysis(project_dir, result)
        result = {**result, "saved_to": str(path.relative_to(project_dir))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_status(project_dir: Path) -> int:
    latest = project_dir / ".rpi-outfile" / "state" / "changes" / "latest.json"
    if not latest.exists():
        print(json.dumps({"status": "empty", "latest": None}, ensure_ascii=False, indent=2))
        return 0
    print(latest.read_text(encoding="utf-8"), end="")
    return 0


def cmd_confirm(project_dir: Path, change_id: str, evidence: str, decision_id: str, option: str) -> int:
    print(json.dumps(confirm_change(project_dir, change_id, evidence, decision_id, option), ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(project_dir: Path, change_id: str, conflict_id: str, resolution: str, evidence: str) -> int:
    print(json.dumps(resolve_conflict(project_dir, change_id, conflict_id, resolution, evidence), ensure_ascii=False, indent=2))
    return 0


def cmd_rebase(project_dir: Path, change_id: str, evidence: str) -> int:
    print(json.dumps(rebase_change(project_dir, change_id, evidence), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI change intelligence")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("text")
    analyze.add_argument("--no-persist", action="store_true")
    sub.add_parser("status")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("change_id")
    confirm.add_argument("--evidence", required=True)
    confirm.add_argument("--decision", default="")
    confirm.add_argument("--option", default="")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("change_id")
    resolve.add_argument("conflict_id")
    resolve.add_argument("--resolution", required=True, choices=sorted(CONFLICT_RESOLUTIONS))
    resolve.add_argument("--evidence", required=True)
    rebase = sub.add_parser("rebase")
    rebase.add_argument("change_id")
    rebase.add_argument("--evidence", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            return cmd_analyze(args.project_dir.resolve(), args.text, not args.no_persist)
        if args.command == "status":
            return cmd_status(args.project_dir.resolve())
        if args.command == "confirm":
            return cmd_confirm(args.project_dir.resolve(), args.change_id, args.evidence, args.decision, args.option)
        if args.command == "resolve":
            return cmd_resolve(args.project_dir.resolve(), args.change_id, args.conflict_id, args.resolution, args.evidence)
        if args.command == "rebase":
            return cmd_rebase(args.project_dir.resolve(), args.change_id, args.evidence)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
