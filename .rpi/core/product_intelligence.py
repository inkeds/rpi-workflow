#!/usr/bin/env python3
"""RPI product-intelligence core.

This module deliberately treats user input as source material, not as an
approved requirement.  It stores immutable raw captures, derives reviewable
claims, and requires an explicit promotion step before a claim can become a
current product fact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


CLAIM_STATES = {
    "raw",
    "inferred",
    "hypothesis",
    "selected",
    "validated",
    "fact",
    "rejected",
    "expired",
    "superseded",
}

MARKETING_TERMS = {
    "一键": "需要定义实际操作步骤与允许的前置配置",
    "实时": "需要定义延迟预算、断线行为与一致性要求",
    "无缝": "需要定义切换、失败恢复和冲突处理",
    "智能": "需要定义输入、输出、质量指标和失败边界",
    "全自动": "需要定义人工确认、错误恢复和不可逆操作边界",
    "所有应用": "需要验证目标平台、协议范围与系统权限",
    "完全安全": "需要定义威胁模型、数据边界和可验证控制",
    "无需安装": "通常限制系统级能力，需要确认 Web 或便携形态",
    "零配置": "需要定义安全默认值及仍不可避免的授权步骤",
    "无限": "需要给出容量、成本、速率或保留期限预算",
}

PLATFORM_SIGNALS = {
    "web": ("web", "网页", "网站", "浏览器页面", "在线后台"),
    "browser_extension": ("浏览器插件", "浏览器扩展", "chrome", "firefox", "划词"),
    "windows_desktop": ("windows", "win32", ".net", "桌面端", "托盘", "exe"),
    "system_network": ("驱动", "ndis", "wfp", "windivert", "所有应用流量", "抓包", "透明代理"),
    "mobile": ("android", "ios", "手机", "移动端", "app"),
    "cloud": ("云端", "在线协作", "团队协作", "跨设备同步", "账号", "多租户"),
    "local_ai": ("本地模型", "离线 ai", "本地 ai", "不上传", "本机推理"),
}

CONFLICT_RULES = (
    (
        "无需安装",
        ("驱动", "所有应用", "系统级", "抓包", "透明代理"),
        "“无需安装”与系统级/全应用能力通常冲突；需要在 Web 体验与本地客户端能力之间取舍。",
    ),
    (
        "不上传",
        ("在线协作", "团队协作", "云端同步", "跨设备同步"),
        "纯本地数据与在线协作存在张力；需要定义可上传的数据范围或端到端加密方案。",
    ),
    (
        "离线",
        ("实时协作", "云端实时", "在线实时"),
        "离线可用与实时在线协作需要明确离线编辑、冲突合并和恢复策略。",
    ),
    (
        "全自动",
        ("付款", "转账", "删除", "发布", "系统设置"),
        "全自动执行涉及高影响操作；必须增加人工确认、权限和回滚边界。",
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def product_dir(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile" / "product"


def stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def split_fragments(text: str) -> list[str]:
    chunks = re.split(r"[\n；;。]+|(?<=[，,])(?=(?:支持|可以|能够|无需|不需要|数据|用户|系统))", text)
    return [chunk.strip(" \t，,。；;") for chunk in chunks if chunk.strip(" \t，,。；;")]


def find_platforms(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    results: list[dict[str, Any]] = []
    for platform, signals in PLATFORM_SIGNALS.items():
        matched = sorted({signal for signal in signals if signal.lower() in lowered})
        if matched:
            results.append({"platform": platform, "signals": matched})
    return results


def find_marketing_terms(text: str) -> list[dict[str, str]]:
    return [
        {"term": term, "clarification": clarification}
        for term, clarification in MARKETING_TERMS.items()
        if term.lower() in text.lower()
    ]


def find_conflicts(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    conflicts: list[dict[str, Any]] = []
    for left, right_terms, explanation in CONFLICT_RULES:
        matched_right = [term for term in right_terms if term.lower() in lowered]
        if left.lower() in lowered and matched_right:
            conflicts.append(
                {
                    "left": left,
                    "right": matched_right,
                    "severity": "high",
                    "explanation": explanation,
                }
            )
    platforms = {item["platform"] for item in find_platforms(text)}
    if "web" in platforms and "system_network" in platforms:
        conflicts.append(
            {
                "left": "web",
                "right": ["system_network"],
                "severity": "high",
                "explanation": "Web 沙箱无法直接提供系统级网络能力，通常需要桌面客户端、服务或驱动。",
            }
        )
    return conflicts


def analyze_text(text: str, source_id: str) -> dict[str, Any]:
    fragments = split_fragments(text)
    platforms = find_platforms(text)
    conflicts = find_conflicts(text)
    uncertainty = "high" if conflicts or len(platforms) >= 3 or len(fragments) >= 6 else "medium"
    if not conflicts and len(platforms) <= 1 and len(fragments) <= 3:
        uncertainty = "low"

    claims = []
    for fragment in fragments:
        claim_id = stable_id("CLM", f"{source_id}:{fragment}")
        claims.append(
            {
                "id": claim_id,
                "source_ids": [source_id],
                "text": fragment,
                "state": "inferred",
                "confidence": "medium",
                "platforms": [item["platform"] for item in find_platforms(fragment)],
                "evidence": [],
                "applicable_scope": "unresolved",
                "review_after": None,
                "supersedes": [],
                "superseded_by": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )

    return {
        "source_id": source_id,
        "analyzed_at": utc_now(),
        "fragments": fragments,
        "claims": claims,
        "marketing_language": find_marketing_terms(text),
        "platforms": platforms,
        "conflicts": conflicts,
        "uncertainty": uncertainty,
        "recommended_next_step": "explore" if uncertainty == "high" else "decide",
        "formal_spec_ready": False,
    }


def direction_candidates(analysis: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    platforms = {item["platform"] for item in analysis.get("platforms", [])}
    fragments = list(analysis.get("fragments", []))
    candidates: list[dict[str, Any]] = []

    def add(
        key: str,
        title: str,
        outcome: str,
        required: list[str],
        kept: list[str],
        deferred: list[str],
        risks: list[str],
        experiments: list[str],
        level: str,
        reasons: list[str],
        objections: list[str],
    ) -> None:
        candidates.append(
            {
                "id": stable_id("DIR", f"{analysis['source_id']}:{key}"),
                "source_ids": [analysis["source_id"]],
                "title": title,
                "status": "candidate",
                "user_outcome": outcome,
                "required_platforms": required,
                "kept_capabilities": kept,
                "deferred_capabilities": deferred,
                "new_risks": risks,
                "critical_unknowns": ["目标用户是否真的需要该结果", "核心体验是否优于现有做法"],
                "next_experiments": experiments,
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "recommendation": {"level": level, "reasons": reasons, "objections": objections},
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )

    web_capabilities = [f for f in fragments if any(x in f.lower() for x in ("web", "网页", "浏览器", "在线"))]
    desktop_capabilities = [f for f in fragments if any(x in f.lower() for x in ("windows", "桌面", "托盘", "驱动", "流量", "抓包"))]
    cloud_capabilities = [f for f in fragments if any(x in f.lower() for x in ("协作", "同步", "云端", "账号", "团队"))]

    if platforms & {"web", "browser_extension"}:
        add(
            "web",
            "Web / 浏览器优先",
            "以低安装成本验证浏览器内或在线场景中的核心体验",
            sorted(platforms & {"web", "browser_extension", "cloud"}) or ["web"],
            web_capabilities or fragments[:2],
            desktop_capabilities,
            ["浏览器沙箱无法提供系统级权限", "离线和本地数据能力可能受限"],
            ["制作可点击原型验证使用路径", "验证浏览器 API 是否覆盖关键动作"],
            "viable",
            ["验证速度快", "普通用户使用门槛低"],
            ["如果系统级能力不可妥协，则该方向不成立"],
        )

    if platforms & {"windows_desktop", "system_network", "local_ai"}:
        add(
            "local",
            "本地桌面客户端优先",
            "优先保留本地数据、桌面集成或系统级能力",
            sorted(platforms & {"windows_desktop", "system_network", "local_ai"}) or ["windows_desktop"],
            desktop_capabilities or fragments[:2],
            cloud_capabilities,
            ["需要安装、授权或管理员权限", "系统兼容和分发成本较高"],
            ["用工程 Spike 验证最高权限能力", "制作最小桌面交互原型"],
            "recommended" if "system_network" in platforms else "viable",
            ["与系统级能力的运行位置一致", "可优先保护本地隐私"],
            ["如果无需安装是硬约束，则不应选择该方向"],
        )

    if "cloud" in platforms and platforms & {"windows_desktop", "system_network", "local_ai"}:
        add(
            "hybrid",
            "本地客户端 + 云端协作",
            "由本地端执行受权限限制的能力，由云端提供账户、同步和协作",
            ["windows_desktop", "cloud"],
            list(dict.fromkeys(desktop_capabilities + cloud_capabilities)),
            [],
            ["至少包含客户端、服务端和同步协议", "隐私、冲突合并和运营成本显著增加"],
            ["先验证本地核心能力", "再用最小同步原型验证数据边界和冲突策略"],
            "high-risk",
            ["能保留最多功能组合"],
            ["第一版成本最高，不适合在核心价值未验证时直接建设"],
        )

    if not candidates and fragments:
        add(
            "minimal",
            "单一核心场景最小版本",
            "先从一个用户结果和一条端到端链路验证价值",
            [],
            fragments[:1],
            fragments[1:],
            ["目标用户和平台仍不明确"],
            ["制作低保真产品原型", "访谈或观察至少一个目标用户场景"],
            "needs-evidence",
            ["避免在信息不足时过早选择架构"],
            ["当前证据不足，不能直接进入正式 PRD"],
        )

    return candidates[:3]


def render_direction_card(source_text: str, analysis: dict[str, Any], directions: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# 产品方向决策卡",
        "",
        "## 原始素材",
        "",
        source_text,
        "",
        "## 当前判断",
        "",
        f"- 不确定性：{analysis.get('uncertainty', 'unknown')}",
        f"- 检测平台：{', '.join(item['platform'] for item in analysis.get('platforms', [])) or '尚未明确'}",
        f"- 主要冲突：{len(analysis.get('conflicts', []))} 项",
        "",
    ]
    if not directions:
        lines.extend(["## 结论", "", "当前素材不足以形成可信产品方向，应继续澄清或先运行低成本实验。", ""])
        return "\n".join(lines)
    for index, direction in enumerate(directions, start=1):
        lines.extend(
            [
                f"## 方向 {index}：{direction['title']}",
                "",
                f"- 用户结果：{direction['user_outcome']}",
                f"- 保留：{'；'.join(direction['kept_capabilities']) or '待确认'}",
                f"- 暂缓：{'；'.join(direction['deferred_capabilities']) or '无'}",
                f"- 风险：{'；'.join(direction['new_risks'])}",
                f"- 下一实验：{'；'.join(direction['next_experiments'])}",
                f"- 判断：{direction['recommendation']['level']}",
                f"- 推荐理由：{'；'.join(direction['recommendation']['reasons'])}",
                f"- 反对理由：{'；'.join(direction['recommendation']['objections'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## 需要用户决定",
            "",
            "请选择更重要的用户结果和可接受的牺牲。选择只会把方向标记为 `selected`；验证通过后才能晋升为产品事实。",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_directions(project_dir: Path, source_id: str) -> int:
    out_dir = product_dir(project_dir)
    sources_doc = read_json(out_dir / "sources.json", {"sources": []})
    sources = sources_doc.get("sources", [])
    if not sources:
        raise ValueError("no source material; run idea capture first")
    source = next((item for item in sources if item.get("id") == source_id), None) if source_id else sources[-1]
    if not source:
        raise ValueError(f"unknown source: {source_id}")
    analysis_path = out_dir / "analysis" / f"{source['id']}.json"
    analysis = read_json(analysis_path, analyze_text(str(source.get("text", "")), str(source["id"])))
    directions = direction_candidates(analysis, str(source.get("text", "")))
    doc = read_json(out_dir / "directions.json", {"schema_version": 1, "directions": []})
    existing = {item.get("id"): item for item in doc.get("directions", [])}
    for direction in directions:
        existing[direction["id"]] = direction
    doc["directions"] = list(existing.values())
    write_json(out_dir / "directions.json", doc)
    card = render_direction_card(str(source.get("text", "")), analysis, directions)
    card_path = out_dir / "decision-cards" / f"{source['id']}.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(card, encoding="utf-8")
    print(json.dumps({"source_id": source["id"], "directions": directions, "decision_card": str(card_path.relative_to(project_dir))}, ensure_ascii=False, indent=2))
    return 0


def cmd_select_direction(project_dir: Path, direction_id: str, reason: str) -> int:
    out_dir = product_dir(project_dir)
    path = out_dir / "directions.json"
    doc = read_json(path, {"directions": []})
    direction = next((item for item in doc.get("directions", []) if item.get("id") == direction_id), None)
    if not direction:
        raise ValueError(f"unknown direction: {direction_id}")
    for item in doc["directions"]:
        if item.get("status") == "selected":
            item["status"] = "candidate"
    direction["status"] = "selected"
    direction["selection_reason"] = reason
    direction["updated_at"] = utc_now()
    write_json(path, doc)
    claims_path = out_dir / "claims.json"
    claims_doc = read_json(claims_path, {"schema_version": 1, "claims": []})
    claim = {
        "id": stable_id("CLM", f"direction:{direction_id}"),
        "source_ids": direction.get("source_ids", []),
        "text": f"选择产品方向：{direction['title']} — {direction['user_outcome']}",
        "state": "selected",
        "confidence": "medium",
        "platforms": direction.get("required_platforms", []),
        "evidence": [f"direction://{direction_id}"],
        "applicable_scope": "product-direction",
        "review_after": None,
        "supersedes": [],
        "superseded_by": None,
        "decision_reason": reason,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    claims_doc["claims"] = append_unique(claims_doc.get("claims", []), [claim])
    write_json(claims_path, claims_doc)
    print(json.dumps(direction, ensure_ascii=False, indent=2))
    return 0


def append_unique(existing: list[dict[str, Any]], additions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): item for item in existing if item.get("id")}
    for item in additions:
        by_id.setdefault(str(item["id"]), item)
    return list(by_id.values())


def cmd_capture(project_dir: Path, text: str, source_type: str) -> int:
    text = text.strip()
    if not text:
        raise ValueError("idea text must not be empty")
    out_dir = product_dir(project_dir)
    sources_path = out_dir / "sources.json"
    source_id = stable_id("SRC", f"{utc_now()}:{text}")
    sources = read_json(sources_path, {"schema_version": 1, "sources": []})
    source = {
        "id": source_id,
        "text": text,
        "source_type": source_type,
        "captured_at": utc_now(),
        "immutable": True,
    }
    sources["sources"].append(source)
    write_json(sources_path, sources)

    analysis = analyze_text(text, source_id)
    write_json(out_dir / "analysis" / f"{source_id}.json", analysis)
    claims_path = out_dir / "claims.json"
    claims_doc = read_json(claims_path, {"schema_version": 1, "claims": []})
    claims_doc["claims"] = append_unique(claims_doc["claims"], analysis["claims"])
    write_json(claims_path, claims_doc)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0


def find_claim(claims: list[dict[str, Any]], claim_id: str) -> dict[str, Any]:
    for claim in claims:
        if claim.get("id") == claim_id:
            return claim
    raise ValueError(f"unknown claim: {claim_id}")


def cmd_transition(
    project_dir: Path,
    claim_id: str,
    target_state: str,
    reason: str,
    evidence: Sequence[str],
) -> int:
    if target_state not in CLAIM_STATES:
        raise ValueError(f"invalid state: {target_state}")
    claims_path = product_dir(project_dir) / "claims.json"
    claims_doc = read_json(claims_path, {"schema_version": 1, "claims": []})
    claim = find_claim(claims_doc["claims"], claim_id)
    current = str(claim.get("state", "inferred"))
    allowed = {
        "inferred": {"hypothesis", "selected", "rejected"},
        "hypothesis": {"selected", "validated", "rejected", "expired"},
        "selected": {"validated", "fact", "rejected", "expired", "superseded"},
        "validated": {"fact", "rejected", "expired", "superseded"},
        "fact": {"expired", "superseded"},
        "rejected": {"hypothesis"},
        "expired": {"hypothesis"},
        "superseded": set(),
    }
    if target_state not in allowed.get(current, set()):
        raise ValueError(f"transition not allowed: {current} -> {target_state}")
    if target_state in {"validated", "fact"} and not evidence:
        raise ValueError(f"{target_state} requires at least one evidence reference")
    claim["state"] = target_state
    claim["decision_reason"] = reason
    claim["evidence"] = sorted(set([*claim.get("evidence", []), *evidence]))
    claim["updated_at"] = utc_now()
    write_json(claims_path, claims_doc)
    rebuild_current_facts(project_dir, claims_doc["claims"])
    print(json.dumps(claim, ensure_ascii=False, indent=2))
    return 0


def rebuild_current_facts(project_dir: Path, claims: Sequence[dict[str, Any]]) -> None:
    facts = [claim for claim in claims if claim.get("state") == "fact"]
    write_json(
        product_dir(project_dir) / "current_facts.json",
        {"schema_version": 1, "generated_at": utc_now(), "facts": facts},
    )


def cmd_status(project_dir: Path, require_source: bool = False) -> int:
    out_dir = product_dir(project_dir)
    claims = read_json(out_dir / "claims.json", {"claims": []})["claims"]
    counts = {state: 0 for state in sorted(CLAIM_STATES)}
    for claim in claims:
        counts[str(claim.get("state", "inferred"))] = counts.get(str(claim.get("state")), 0) + 1
    payload = {
        "sources": len(read_json(out_dir / "sources.json", {"sources": []})["sources"]),
        "claims": len(claims),
        "claim_states": counts,
        "formal_spec_ready": counts.get("fact", 0) > 0 and counts.get("selected", 0) == 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if require_source and payload["sources"] == 0:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI product intelligence")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("text")
    capture.add_argument("--source-type", default="unknown")
    transition = sub.add_parser("transition")
    transition.add_argument("claim_id")
    transition.add_argument("state", choices=sorted(CLAIM_STATES))
    transition.add_argument("--reason", required=True)
    transition.add_argument("--evidence", action="append", default=[])
    status = sub.add_parser("status")
    status.add_argument("--require-source", action="store_true")
    directions = sub.add_parser("directions")
    directions.add_argument("--source-id", default="")
    select = sub.add_parser("select")
    select.add_argument("direction_id")
    select.add_argument("--reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    try:
        if args.command == "capture":
            return cmd_capture(project_dir, args.text, args.source_type)
        if args.command == "transition":
            return cmd_transition(project_dir, args.claim_id, args.state, args.reason, args.evidence)
        if args.command == "status":
            return cmd_status(project_dir, args.require_source)
        if args.command == "directions":
            return cmd_directions(project_dir, args.source_id)
        if args.command == "select":
            return cmd_select_direction(project_dir, args.direction_id, args.reason)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
