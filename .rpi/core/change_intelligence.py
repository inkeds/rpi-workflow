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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


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
        result["status"] = "pending_decision" if pending else "spec_update_required"
        if not pending:
            result["confirmed_at"] = utc_now()
        result["implementation_allowed"] = False
        result["next_action"] = "request_decision" if pending else "prepare_spec_and_task"
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
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
