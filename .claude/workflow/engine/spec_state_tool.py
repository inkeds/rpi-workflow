#!/usr/bin/env python3
"""RPI spec state engine.

Subcommands:
  build       Build .rpi-outfile/state/spec/state.json
  verify      Validate built state for discovery/scope guard constraints
  sync-source Force refresh .rpi-outfile/specs/l0/spec-source.json from current state

The engine supports JSON source-of-truth:
  .rpi-outfile/specs/l0/spec-source.json

Selection strategy:
  - If source JSON exists and is newer/equal to markdown sources, use JSON.
  - Otherwise parse markdown files and refresh source JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STATE_BUILD_REV = "4"

DEFAULT_FIELD_ALIASES: Dict[str, List[str]] = {
    "goal": ["目标", "Goal", "Objective"],
    "target_user": ["目标用户", "Target User", "User Persona"],
    "high_freq_scenario": ["高频使用场景", "High-Frequency Scenario", "Primary Scenario"],
    "time_window": ["时间窗口", "Time Window"],
    "direction": ["选择方向", "Direction"],
    "coverage_target": ["覆盖率目标", "Coverage Target", "Coverage"],
    "weighted_coverage_target": ["加权覆盖率目标", "Weighted Coverage Target", "Weighted Coverage"],
    "m0_must": ["M0 Must（1-3）", "M0 Must (1-3)", "M0 Must"],
    "m0_wont": ["M0 Won't（>=3）", "M0 Won't (>=3)", "M0 Wont (>=3)", "M0 Won't"],
    "ubiquitous_language": ["统一语言（Ubiquitous Language）", "统一语言", "Ubiquitous Language"],
    "bounded_contexts": ["限界上下文（Bounded Context）", "限界上下文", "Bounded Context"],
    "domain_invariants": ["业务不变量（Domain Invariants）", "业务不变量", "Domain Invariants"],
    "m0_contexts": ["已选上下文（M0）", "已选上下文", "M0 Contexts", "Selected Contexts (M0)"],
    "priority_overrides": ["优先级调权", "优先级调权（可选）", "Priority Overrides", "Feature Weight Overrides"],
    "success_metrics": ["成功指标（2-4）", "Success Metrics (2-4)", "Success Metrics"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_project_dir(start: Path) -> Path:
    cur = start.resolve()
    for cand in [cur] + list(cur.parents):
        if (cand / ".claude" / "workflow").is_dir():
            return cand
    return cur


def file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


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


def read_json_obj(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_key(raw: str) -> str:
    text = raw.strip()
    text = text.replace("**", "")
    text = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", text)
    text = re.sub(r"[:：]", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.lower()
    return text


def normalize_item(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^\s*[-*]\s+\[[xX ]\]\s+", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    return text.strip()


def extract_field_value(lines: List[str], aliases: Iterable[str]) -> str:
    alias_keys = {normalize_key(a) for a in aliases}
    field_pattern = re.compile(r"^\s*[-*]\s*(.+?)\s*[:：]\s*(.*)\s*$")
    heading_pattern = re.compile(r"^\s*#{1,6}\s+")

    for i, line in enumerate(lines):
        m = field_pattern.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if normalize_key(key) not in alias_keys:
            continue
        if value:
            return value

        buf: List[str] = []
        base_indent = len(line) - len(line.lstrip(" "))
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if heading_pattern.match(nxt):
                break
            next_field = field_pattern.match(nxt)
            if next_field:
                next_indent = len(nxt) - len(nxt.lstrip(" "))
                # A nested bullet under current field (indent deeper than base) should be captured.
                if next_indent <= base_indent:
                    break
            cleaned = normalize_item(nxt)
            if cleaned:
                buf.append(cleaned)
            j += 1
        return "\n".join(buf).strip()
    return ""


def parse_list(raw: str) -> List[str]:
    if not raw:
        return []
    rows = [normalize_item(x) for x in raw.splitlines()]
    rows = [x for x in rows if x]
    if not rows:
        return []
    if len(rows) == 1:
        parts = re.split(r"[，、,;；/|]+", rows[0])
        rows = [x.strip() for x in parts if x.strip()]
    return rows


def count_numbered_section(lines: List[str], heading: str) -> int:
    heading_norm = heading.strip().lower()
    count = 0
    in_section = False
    for line in lines:
        cleaned = line.replace("*", "").strip()
        if re.match(r"^###\s+", cleaned):
            this_heading = re.sub(r"^###\s+", "", cleaned).strip().lower()
            if this_heading == heading_norm:
                in_section = True
                continue
            if in_section:
                in_section = False
        if in_section and (
            re.match(r"^\d+[.)]\s*\S", cleaned)
            or re.match(r"^[-*]\s*(\[[ xX]\]\s*)?\S", cleaned)
        ):
            count += 1
    return count


def extract_out_of_scope_items(lines: List[str]) -> List[str]:
    in_scope_section = False
    capture_following = False
    items: List[str] = []

    out_labels = [
        "Out of Scope",
        "Out-of-Scope",
        "不做范围",
        "Won't",
        "Wont",
        "不做",
    ]
    in_labels = ["In Scope", "范围", "做范围"]

    def line_starts_with_any_label(line: str, labels: Iterable[str]) -> bool:
        for label in labels:
            if re.match(rf"^-?\s*{re.escape(label)}\s*[:：]", line, flags=re.IGNORECASE):
                return True
        return False

    for raw in lines:
        line = raw.replace("*", "").strip()
        if re.match(r"^##\s+架构边界\s*$", line) or re.match(
            r"^##\s+Architecture Boundar(y|ies)\s*$", line, flags=re.IGNORECASE
        ):
            in_scope_section = True
            capture_following = False
            continue
        if in_scope_section and re.match(r"^##\s+", line):
            in_scope_section = False
            capture_following = False
        if not in_scope_section or not line:
            continue

        if line_starts_with_any_label(line, out_labels):
            direct = re.sub(
                r"^-?\s*(Out of Scope|Out-of-Scope|不做范围|Won'?t|Wont|不做)\s*[:：]\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            if direct:
                items.append(direct)
                capture_following = False
            else:
                capture_following = True
            continue

        if line_starts_with_any_label(line, in_labels):
            capture_following = False
            continue

        if capture_following:
            cleaned = normalize_item(line)
            if cleaned:
                items.append(cleaned)

    uniq: List[str] = []
    seen = set()
    for it in items:
        if it not in seen:
            seen.add(it)
            uniq.append(it)
    return uniq


def extract_task_ids(text: str) -> List[str]:
    matches = re.findall(r"task[ -]?0*([0-9]{1,4})", text, flags=re.IGNORECASE)
    vals = {f"TASK-{int(m):03d}" for m in matches if int(m) > 0}
    return sorted(vals)


def count_m0_tasks(lines: List[str]) -> int:
    count = 0
    in_m0 = False
    for raw in lines:
        line = raw.replace("*", "")
        if re.match(r"^#{2,6}\s+M0([ :：]|$)", line, flags=re.IGNORECASE):
            in_m0 = True
            continue
        if in_m0 and re.match(r"^#{2,6}\s+M[0-2]([ :：]|$)", line, flags=re.IGNORECASE):
            in_m0 = False
        if not in_m0:
            continue
        if re.match(r"^#{3,6}\s+(Task|TASK|任务)[- #]*\d+", line):
            count += 1
            continue
        if re.match(r"^\d+[.)]\s*(Task|TASK|任务)[- #]*\d+", line):
            count += 1
            continue
        if re.match(r"^[-*]\s*(Task|TASK|任务)[- #]*\d+", line):
            count += 1
            continue
    return count


@dataclass
class Paths:
    project: Path
    runtime_file: Path
    aliases_file: Path
    state_spec_dir: Path
    state_file: Path
    meta_file: Path
    verify_file: Path
    source_file: Path
    discovery_file: Path
    spec_file: Path
    tasks_file: Path


def load_paths_from_project(project: Path) -> Paths:
    state_spec_dir = project / ".rpi-outfile" / "state" / "spec"
    return Paths(
        project=project,
        runtime_file=project / ".claude" / "workflow" / "config" / "runtime.json",
        aliases_file=project / ".claude" / "workflow" / "config" / "spec_aliases.json",
        state_spec_dir=state_spec_dir,
        state_file=state_spec_dir / "state.json",
        meta_file=state_spec_dir / "state.meta",
        verify_file=state_spec_dir / "verification.json",
        source_file=project / ".rpi-outfile" / "specs" / "l0" / "spec-source.json",
        discovery_file=project / ".rpi-outfile" / "specs" / "l0" / "discovery.md",
        spec_file=project / ".rpi-outfile" / "specs" / "l0" / "spec.md",
        tasks_file=project / ".rpi-outfile" / "specs" / "l0" / "tasks.md",
    )


def load_paths(script_file: Path) -> Paths:
    project = resolve_project_dir(script_file.parent)
    return load_paths_from_project(project)


def load_source_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("discovery"), dict):
        return None
    if not isinstance(data.get("spec"), dict):
        return None
    if not isinstance(data.get("tasks"), dict):
        return None
    return data


def load_field_aliases(paths: Paths) -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {k: list(v) for k, v in DEFAULT_FIELD_ALIASES.items()}
    if not paths.aliases_file.exists():
        return aliases
    try:
        raw = json.loads(paths.aliases_file.read_text(encoding="utf-8"))
    except Exception:
        return aliases
    if not isinstance(raw, dict):
        return aliases
    fields = raw.get("fields", {})
    if not isinstance(fields, dict):
        return aliases
    for key, value in fields.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, list):
            rows = [str(x).strip() for x in value if str(x).strip()]
            if rows:
                aliases[key] = rows
        elif isinstance(value, str):
            text = value.strip()
            if text:
                aliases[key] = [text]
    return aliases


def aliases_for(alias_map: Dict[str, List[str]], key: str, fallback: Sequence[str]) -> List[str]:
    vals = alias_map.get(key, [])
    if isinstance(vals, list):
        rows = [str(x).strip() for x in vals if str(x).strip()]
        if rows:
            return rows
    return [str(x).strip() for x in fallback if str(x).strip()]


def newest_mtime(paths: Iterable[Path]) -> int:
    vals = [file_mtime(p) for p in paths]
    return max(vals) if vals else 0


def build_signature(paths: Paths, mode: str) -> str:
    prefix = f"rev:{STATE_BUILD_REV}"
    if mode == "json":
        return f"{prefix}\njson:{paths.source_file}:{file_mtime(paths.source_file)}"
    parts = []
    for p in (paths.discovery_file, paths.spec_file, paths.tasks_file):
        if p.exists():
            parts.append(f"{p}:{file_mtime(p)}")
        else:
            parts.append(f"{p}:MISSING")
    return prefix + "\n" + "\n".join(parts)


def choose_input_mode(paths: Paths) -> str:
    source = load_source_json(paths.source_file)
    if source is None:
        return "markdown"
    json_mtime = file_mtime(paths.source_file)
    md_mtime = newest_mtime([paths.discovery_file, paths.spec_file, paths.tasks_file])
    if json_mtime >= md_mtime:
        return "json"
    return "markdown"


def state_from_source_json(paths: Paths, src: Dict[str, Any]) -> Dict[str, Any]:
    field_aliases = load_field_aliases(paths)
    d = src.get("discovery", {})
    s = src.get("spec", {})
    t = src.get("tasks", {})

    m0_must = [str(x).strip() for x in d.get("m0_must", []) if str(x).strip()]
    m0_wont = [str(x).strip() for x in d.get("m0_wont", []) if str(x).strip()]
    success = [str(x).strip() for x in d.get("success_metrics", []) if str(x).strip()]
    out_items = [str(x).strip() for x in s.get("out_of_scope_items", []) if str(x).strip()]
    task_ids = [str(x).strip().upper() for x in t.get("task_ids", []) if str(x).strip()]
    task_ids = sorted(set(task_ids))

    direction = str(d.get("direction", "")).strip()
    coverage_target = str(d.get("coverage_target", "")).strip()
    weighted_coverage_target = str(d.get("weighted_coverage_target", "")).strip()
    ubiquitous_language = [str(x).strip() for x in d.get("ubiquitous_language", []) if str(x).strip()]
    bounded_contexts = [str(x).strip() for x in d.get("bounded_contexts", []) if str(x).strip()]
    domain_invariants = [str(x).strip() for x in d.get("domain_invariants", []) if str(x).strip()]
    m0_contexts = [str(x).strip() for x in d.get("m0_contexts", []) if str(x).strip()]
    priority_overrides = [str(x).strip() for x in d.get("priority_overrides", []) if str(x).strip()]
    d_lines = read_text(paths.discovery_file).splitlines()
    if not coverage_target:
        coverage_target = extract_field_value(
            d_lines,
            aliases_for(field_aliases, "coverage_target", ["覆盖率目标", "Coverage Target", "Coverage"]),
        )
    if not weighted_coverage_target:
        weighted_coverage_target = extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "weighted_coverage_target",
                ["加权覆盖率目标", "Weighted Coverage Target", "Weighted Coverage"],
            ),
        )
    if not ubiquitous_language:
        ubiquitous_language = parse_list(
            extract_field_value(
                d_lines,
                aliases_for(
                    field_aliases,
                    "ubiquitous_language",
                    ["统一语言（Ubiquitous Language）", "统一语言", "Ubiquitous Language"],
                ),
            )
        )
    if not bounded_contexts:
        bounded_contexts = parse_list(
            extract_field_value(
                d_lines,
                aliases_for(
                    field_aliases,
                    "bounded_contexts",
                    ["限界上下文（Bounded Context）", "限界上下文", "Bounded Context"],
                ),
            )
        )
    if not domain_invariants:
        domain_invariants = parse_list(
            extract_field_value(
                d_lines,
                aliases_for(
                    field_aliases,
                    "domain_invariants",
                    ["业务不变量（Domain Invariants）", "业务不变量", "Domain Invariants"],
                ),
            )
        )
    if not m0_contexts:
        m0_contexts = parse_list(
            extract_field_value(
                d_lines,
                aliases_for(
                    field_aliases,
                    "m0_contexts",
                    ["已选上下文（M0）", "已选上下文", "M0 Contexts", "Selected Contexts (M0)"],
                ),
            )
        )
    if not priority_overrides:
        priority_overrides = parse_list(
            extract_field_value(
                d_lines,
                aliases_for(
                    field_aliases,
                    "priority_overrides",
                    ["优先级调权", "优先级调权（可选）", "Priority Overrides", "Feature Weight Overrides"],
                ),
            )
        )
    m = re.search(r"[ABC]", direction.upper())
    direction_choice = m.group(0) if m else ""

    ts = utc_now()
    state = {
        "schema_version": str(src.get("schema_version", "1.0.0")),
        "generated_at": ts,
        "source": {
            "discovery": {"file": ".rpi-outfile/specs/l0/discovery.md", "exists": paths.discovery_file.exists()},
            "spec": {"file": ".rpi-outfile/specs/l0/spec.md", "exists": paths.spec_file.exists()},
            "tasks": {"file": ".rpi-outfile/specs/l0/tasks.md", "exists": paths.tasks_file.exists()},
            "structured_source": {
                "file": ".rpi-outfile/specs/l0/spec-source.json",
                "exists": True,
                "mode": "json",
            },
        },
        "discovery": {
            "fields": {
                "goal": str(d.get("goal", "")).strip(),
                "target_user": str(d.get("target_user", "")).strip(),
                "high_freq_scenario": str(d.get("high_freq_scenario", "")).strip(),
                "time_window": str(d.get("time_window", "")).strip(),
                "direction": direction,
                "direction_choice": direction_choice,
                "coverage_target": coverage_target,
                "weighted_coverage_target": weighted_coverage_target,
                "m0_must": m0_must,
                "m0_wont": m0_wont,
                "success_metrics": success,
                "ubiquitous_language": ubiquitous_language,
                "bounded_contexts": bounded_contexts,
                "domain_invariants": domain_invariants,
                "m0_contexts": m0_contexts,
                "priority_overrides": priority_overrides,
            },
            "sections": {
                "facts_count": int(d.get("facts_count", 0) or 0),
                "assumptions_count": int(d.get("assumptions_count", 0) or 0),
                "open_questions_count": int(d.get("open_questions_count", 0) or 0),
            },
        },
        "spec": {
            "out_of_scope_items": out_items,
            "out_of_scope_count": len(out_items),
        },
        "tasks": {
            "task_ids": task_ids,
            "m0_task_count": int(t.get("m0_task_count", 0) or 0),
        },
    }
    return state


def source_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "updated_at": utc_now(),
        "discovery": {
            "goal": state["discovery"]["fields"]["goal"],
            "target_user": state["discovery"]["fields"]["target_user"],
            "high_freq_scenario": state["discovery"]["fields"]["high_freq_scenario"],
            "time_window": state["discovery"]["fields"]["time_window"],
            "direction": state["discovery"]["fields"]["direction"],
            "coverage_target": state["discovery"]["fields"].get("coverage_target", ""),
            "weighted_coverage_target": state["discovery"]["fields"].get("weighted_coverage_target", ""),
            "m0_must": state["discovery"]["fields"]["m0_must"],
            "m0_wont": state["discovery"]["fields"]["m0_wont"],
            "success_metrics": state["discovery"]["fields"]["success_metrics"],
            "ubiquitous_language": state["discovery"]["fields"].get("ubiquitous_language", []),
            "bounded_contexts": state["discovery"]["fields"].get("bounded_contexts", []),
            "domain_invariants": state["discovery"]["fields"].get("domain_invariants", []),
            "m0_contexts": state["discovery"]["fields"].get("m0_contexts", []),
            "priority_overrides": state["discovery"]["fields"].get("priority_overrides", []),
            "facts_count": state["discovery"]["sections"]["facts_count"],
            "assumptions_count": state["discovery"]["sections"]["assumptions_count"],
            "open_questions_count": state["discovery"]["sections"]["open_questions_count"],
        },
        "spec": {
            "out_of_scope_items": state["spec"]["out_of_scope_items"],
        },
        "tasks": {
            "task_ids": state["tasks"]["task_ids"],
            "m0_task_count": state["tasks"]["m0_task_count"],
        },
    }


def state_from_markdown(paths: Paths) -> Dict[str, Any]:
    field_aliases = load_field_aliases(paths)
    d_lines = read_text(paths.discovery_file).splitlines()
    s_lines = read_text(paths.spec_file).splitlines()
    t_text = read_text(paths.tasks_file)
    t_lines = t_text.splitlines()

    goal = extract_field_value(d_lines, aliases_for(field_aliases, "goal", ["目标", "Goal", "Objective"]))
    target_user = extract_field_value(
        d_lines,
        aliases_for(field_aliases, "target_user", ["目标用户", "Target User", "User Persona"]),
    )
    high = extract_field_value(
        d_lines,
        aliases_for(field_aliases, "high_freq_scenario", ["高频使用场景", "High-Frequency Scenario", "Primary Scenario"]),
    )
    time_window = extract_field_value(d_lines, aliases_for(field_aliases, "time_window", ["时间窗口", "Time Window"]))
    direction = extract_field_value(d_lines, aliases_for(field_aliases, "direction", ["选择方向", "Direction"]))
    coverage_target = extract_field_value(
        d_lines,
        aliases_for(field_aliases, "coverage_target", ["覆盖率目标", "Coverage Target", "Coverage"]),
    )
    weighted_coverage_target = extract_field_value(
        d_lines,
        aliases_for(
            field_aliases,
            "weighted_coverage_target",
            ["加权覆盖率目标", "Weighted Coverage Target", "Weighted Coverage"],
        ),
    )
    m = re.search(r"[ABC]", direction.upper())
    direction_choice = m.group(0) if m else ""

    m0_must = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(field_aliases, "m0_must", ["M0 Must（1-3）", "M0 Must (1-3)", "M0 Must"]),
        )
    )
    m0_wont = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "m0_wont",
                ["M0 Won't（>=3）", "M0 Won't (>=3)", "M0 Wont (>=3)", "M0 Won't"],
            ),
        )
    )
    ubiquitous_language = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "ubiquitous_language",
                ["统一语言（Ubiquitous Language）", "统一语言", "Ubiquitous Language"],
            ),
        )
    )
    bounded_contexts = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "bounded_contexts",
                ["限界上下文（Bounded Context）", "限界上下文", "Bounded Context"],
            ),
        )
    )
    domain_invariants = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "domain_invariants",
                ["业务不变量（Domain Invariants）", "业务不变量", "Domain Invariants"],
            ),
        )
    )
    m0_contexts = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "m0_contexts",
                ["已选上下文（M0）", "已选上下文", "M0 Contexts", "Selected Contexts (M0)"],
            ),
        )
    )
    priority_overrides = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "priority_overrides",
                ["优先级调权", "优先级调权（可选）", "Priority Overrides", "Feature Weight Overrides"],
            ),
        )
    )
    success = parse_list(
        extract_field_value(
            d_lines,
            aliases_for(
                field_aliases,
                "success_metrics",
                ["成功指标（2-4）", "Success Metrics (2-4)", "Success Metrics"],
            ),
        )
    )

    facts_count = count_numbered_section(d_lines, "Facts")
    assumptions_count = count_numbered_section(d_lines, "Assumptions")
    open_questions_count = count_numbered_section(d_lines, "Open Questions")

    out_items = extract_out_of_scope_items(s_lines)
    task_ids = extract_task_ids(t_text)
    m0_task_count = count_m0_tasks(t_lines)

    ts = utc_now()
    return {
        "schema_version": "1.0.0",
        "generated_at": ts,
        "source": {
            "discovery": {"file": ".rpi-outfile/specs/l0/discovery.md", "exists": paths.discovery_file.exists()},
            "spec": {"file": ".rpi-outfile/specs/l0/spec.md", "exists": paths.spec_file.exists()},
            "tasks": {"file": ".rpi-outfile/specs/l0/tasks.md", "exists": paths.tasks_file.exists()},
            "structured_source": {
                "file": ".rpi-outfile/specs/l0/spec-source.json",
                "exists": paths.source_file.exists(),
                "mode": "markdown",
            },
        },
        "discovery": {
            "fields": {
                "goal": goal,
                "target_user": target_user,
                "high_freq_scenario": high,
                "time_window": time_window,
                "direction": direction,
                "direction_choice": direction_choice,
                "coverage_target": coverage_target,
                "weighted_coverage_target": weighted_coverage_target,
                "m0_must": m0_must,
                "m0_wont": m0_wont,
                "success_metrics": success,
                "ubiquitous_language": ubiquitous_language,
                "bounded_contexts": bounded_contexts,
                "domain_invariants": domain_invariants,
                "m0_contexts": m0_contexts,
                "priority_overrides": priority_overrides,
            },
            "sections": {
                "facts_count": facts_count,
                "assumptions_count": assumptions_count,
                "open_questions_count": open_questions_count,
            },
        },
        "spec": {
            "out_of_scope_items": out_items,
            "out_of_scope_count": len(out_items),
        },
        "tasks": {
            "task_ids": task_ids,
            "m0_task_count": m0_task_count,
        },
    }


def build_state(paths: Paths, quiet: bool, print_path: bool, force: bool) -> int:
    paths.state_spec_dir.mkdir(parents=True, exist_ok=True)

    mode = choose_input_mode(paths)
    signature = build_signature(paths, mode)
    source_missing = not paths.source_file.exists()

    if (
        not force
        and paths.state_file.exists()
        and paths.meta_file.exists()
        and not source_missing
        and paths.meta_file.read_text(encoding="utf-8").strip() == signature.strip()
    ):
        if print_path:
            print(str(paths.state_file))
        if not quiet:
            print(f"Spec state up-to-date: {paths.state_file}")
        return 0

    if mode == "json":
        src = load_source_json(paths.source_file)
        if src is None:
            mode = "markdown"
            state = state_from_markdown(paths)
        else:
            state = state_from_source_json(paths, src)
    else:
        state = state_from_markdown(paths)

    # Refresh structured source whenever markdown path is authoritative.
    if mode == "markdown":
        write_json(paths.source_file, source_from_state(state))

    write_json(paths.state_file, state)
    write_text_atomic(paths.meta_file, signature + "\n")

    if print_path:
        print(str(paths.state_file))
    if not quiet:
        print(f"Built spec state: {paths.state_file}")
    return 0


def is_empty_or_placeholder(value: str) -> bool:
    return value.strip() in {"", "待输入", "待确认", "A/B/C", "A / B / C", "TBD", "N/A", "-"}


def count_chain_refs(items: Sequence[str]) -> int:
    count = 0
    for item in items:
        if re.search(r"(^|[^A-Za-z0-9])L[0-9]+([^A-Za-z0-9]|$)", str(item), flags=re.IGNORECASE):
            count += 1
    return count


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


def load_coverage_policy(paths: Paths) -> Tuple[int, int, int]:
    runtime = read_json_obj(paths.runtime_file) if paths.runtime_file.exists() else {}
    a = clamp_percent(runtime.get("mvp_coverage_threshold_a", 40), 40)
    b = clamp_percent(runtime.get("mvp_coverage_threshold_b", 80), 80)
    c = clamp_percent(runtime.get("mvp_coverage_threshold_c", 100), 100)
    if b < a:
        b = a
    if c < b:
        c = b
    return a, b, c


def parse_percent_value(raw: str) -> Optional[int]:
    text = str(raw or "").strip()
    if not text:
        return None
    m = re.search(r"([0-9]{1,3})\s*%", text)
    if m:
        return clamp_percent(m.group(1), 0)
    m = re.search(r"([0-9]{1,3})", text)
    if m:
        return clamp_percent(m.group(1), 0)
    return None


def parse_nonnegative_int(raw: Any, default: int) -> int:
    if isinstance(raw, bool):
        value = int(raw)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        value = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r"-?[0-9]+", raw.strip()):
        value = int(raw.strip())
    else:
        value = default
    return 0 if value < 0 else value


def load_ddd_policy(paths: Paths) -> Tuple[str, int, int, int]:
    runtime = read_json_obj(paths.runtime_file) if paths.runtime_file.exists() else {}
    mode = str(runtime.get("ddd_lite_mode", "warn")).strip().lower()
    if mode not in {"off", "warn", "enforce"}:
        mode = "warn"
    min_glossary = parse_nonnegative_int(runtime.get("ddd_min_glossary_terms", 6), 6)
    min_contexts = parse_nonnegative_int(runtime.get("ddd_min_bounded_contexts", 2), 2)
    min_invariants = parse_nonnegative_int(runtime.get("ddd_min_invariants", 3), 3)
    return mode, min_glossary, min_contexts, min_invariants


def load_override_policy(paths: Paths) -> Tuple[str, int, int]:
    runtime = read_json_obj(paths.runtime_file) if paths.runtime_file.exists() else {}
    mode = str(runtime.get("mvp_priority_override_mode", "warn")).strip().lower()
    if mode not in {"off", "warn", "enforce"}:
        mode = "warn"
    tolerance = clamp_percent(runtime.get("mvp_weighted_coverage_tolerance", 10), 10)
    max_promote = parse_nonnegative_int(runtime.get("mvp_max_promote_non_core", 1), 1)
    return mode, tolerance, max_promote


def count_context_refs(items: Sequence[str]) -> int:
    count = 0
    for item in items:
        if re.search(r"(^|[^A-Za-z0-9])(C|BC)[0-9]+([^A-Za-z0-9]|$)", str(item), flags=re.IGNORECASE):
            count += 1
    return count


def contains_context_tag(items: Sequence[str], keywords: Sequence[str]) -> bool:
    for item in items:
        text = str(item).lower()
        for kw in keywords:
            if kw.lower() in text:
                return True
    return False


def count_keyword_hits(items: Sequence[str], keywords: Sequence[str]) -> int:
    count = 0
    for item in items:
        text = str(item).lower()
        if any(kw.lower() in text for kw in keywords):
            count += 1
    return count


def verify_state(paths: Paths, scope: str, quiet: bool, json_output: bool) -> int:
    if not paths.state_file.exists():
        rc = build_state(paths, quiet=True, print_path=False, force=False)
        if rc != 0:
            if not quiet and not json_output:
                print("spec state verify failed: cannot build state", file=sys.stderr)
            return 1

    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    except Exception:
        if not quiet and not json_output:
            print(f"Missing or invalid state file: {paths.state_file}", file=sys.stderr)
        return 1

    errors: List[str] = []
    warnings: List[str] = []

    def check_discovery() -> None:
        src = state.get("source", {})
        discovery_exists = bool(src.get("discovery", {}).get("exists", False))
        fields = state.get("discovery", {}).get("fields", {})
        sections = state.get("discovery", {}).get("sections", {})

        goal = str(fields.get("goal", ""))
        target_user = str(fields.get("target_user", ""))
        high_freq = str(fields.get("high_freq_scenario", ""))
        time_window = str(fields.get("time_window", ""))
        direction = str(fields.get("direction", ""))
        direction_choice = str(fields.get("direction_choice", ""))
        coverage_target = str(fields.get("coverage_target", ""))
        weighted_coverage_target = str(fields.get("weighted_coverage_target", ""))
        cov_a, cov_b, cov_c = load_coverage_policy(paths)
        must_items = fields.get("m0_must", []) or []
        wont_items = fields.get("m0_wont", []) or []
        must_count = len(must_items)
        wont_count = len(wont_items)
        must_chain_count = count_chain_refs(must_items)
        wont_chain_count = count_chain_refs(wont_items)
        metric_count = len(fields.get("success_metrics", []) or [])
        ubiquitous_language = fields.get("ubiquitous_language", []) or []
        bounded_contexts = fields.get("bounded_contexts", []) or []
        domain_invariants = fields.get("domain_invariants", []) or []
        m0_contexts = fields.get("m0_contexts", []) or []
        glossary_count = len(ubiquitous_language)
        context_count = len(bounded_contexts)
        invariant_count = len(domain_invariants)
        selected_context_count = len(m0_contexts)
        selected_context_ref_count = count_context_refs(m0_contexts)
        has_core_context = contains_context_tag(m0_contexts, ["[core]", "核心"])
        has_supporting_context = contains_context_tag(m0_contexts, ["[supporting]", "支撑", "支援"])
        has_governance_context = contains_context_tag(m0_contexts, ["[governance]", "治理", "审计", "合规"])
        ddd_mode, ddd_min_glossary, ddd_min_contexts, ddd_min_invariants = load_ddd_policy(paths)
        override_mode, override_tolerance, override_max_promote = load_override_policy(paths)
        priority_overrides = fields.get("priority_overrides", []) or []
        promote_count = count_keyword_hits(priority_overrides, ["提升", "promote", "boost", "upgrade"])
        demote_count = count_keyword_hits(priority_overrides, ["降权", "demote", "downgrade", "deprioritize"])
        weighted_coverage_percent = parse_percent_value(weighted_coverage_target)
        facts_count = int(sections.get("facts_count", 0) or 0)
        assumptions_count = int(sections.get("assumptions_count", 0) or 0)
        open_questions_count = int(sections.get("open_questions_count", 0) or 0)

        def add_ddd_issue(message: str) -> None:
            if ddd_mode == "enforce":
                errors.append(message)
            elif ddd_mode == "warn":
                warnings.append(message)

        def add_override_issue(message: str) -> None:
            if override_mode == "enforce":
                errors.append(message)
            elif override_mode == "warn":
                warnings.append(message)

        if not discovery_exists:
            errors.append("缺少文件 .rpi-outfile/specs/l0/discovery.md")
            return

        if is_empty_or_placeholder(goal):
            errors.append("目标 未填写")
        if is_empty_or_placeholder(target_user):
            errors.append("目标用户 未填写")
        if is_empty_or_placeholder(high_freq):
            errors.append("高频使用场景 未填写")
        if is_empty_or_placeholder(time_window):
            errors.append("时间窗口 未填写")
        if is_empty_or_placeholder(direction):
            errors.append("方向选择 未填写")
        if direction_choice not in {"A", "B", "C"}:
            errors.append(f"方向选择必须包含 A/B/C（当前：{direction}）")
        if is_empty_or_placeholder(coverage_target):
            errors.append("覆盖率目标 未填写")
        else:
            coverage_percent = parse_percent_value(coverage_target)
            if coverage_percent is None:
                errors.append(f"覆盖率目标 无法解析百分比（当前：{coverage_target}）")
            else:
                required = cov_a
                if direction_choice == "B":
                    required = cov_b
                elif direction_choice == "C":
                    required = cov_c
                coverage_gap = required - coverage_percent
                allow_weighted_override = (
                    override_mode != "off"
                    and promote_count > 0
                    and coverage_gap > 0
                    and coverage_gap <= override_tolerance
                    and weighted_coverage_percent is not None
                    and weighted_coverage_percent >= required
                )
                if coverage_percent < required and not allow_weighted_override:
                    errors.append(f"覆盖率目标不足：方向 {direction_choice or '?'} 需要 >= {required}%（当前：{coverage_percent}%）")
                elif allow_weighted_override:
                    warnings.append(
                        f"覆盖率通过调权策略豁免：方向 {direction_choice or '?'} 原始 {coverage_percent}% < {required}%，"
                        f"采用加权覆盖率 {weighted_coverage_percent}%（容差 {override_tolerance}%）"
                    )
        if must_count < 1:
            errors.append("M0 Must 至少需要 1 项")
        if must_count > 3:
            errors.append(f"M0 Must 不能超过 3 项（当前：{must_count}）")
        if must_chain_count < 1:
            errors.append("M0 Must 需要至少包含 1 个链路 ID（示例：L1）")
        if direction_choice in {"B", "C"} and must_chain_count < 2:
            errors.append(f"方向 {direction_choice} 需要至少 2 条链路 ID（当前：{must_chain_count}）")
        if wont_count < 3:
            errors.append(f"M0 Won't 至少需要 3 项（当前：{wont_count}）")
        if wont_chain_count < 1:
            errors.append("M0 Won't 需要至少包含 1 个未入选链路 ID（示例：L3）")
        if metric_count < 2 or metric_count > 4:
            errors.append(f"成功指标 需要 2-4 项（当前：{metric_count}）")
        if facts_count < 1:
            errors.append("Facts 至少需要 1 条有效条目")
        if assumptions_count < 1:
            errors.append("Assumptions 至少需要 1 条有效条目")
        if open_questions_count < 1:
            errors.append("Open Questions 至少需要 1 条有效条目")
        if override_mode != "off":
            if promote_count > 0:
                if promote_count > override_max_promote:
                    add_override_issue(
                        f"优先级调权提升项过多：最多允许 {override_max_promote} 项（当前：{promote_count}）"
                    )
                if demote_count < 1:
                    add_override_issue("优先级调权包含提升项时，至少需要 1 条降权项说明取舍")
                if weighted_coverage_percent is None:
                    add_override_issue("优先级调权包含提升项时，需填写可解析的加权覆盖率目标")
                for item in priority_overrides:
                    txt = str(item).strip()
                    if not txt:
                        continue
                    if ":" not in txt and "：" not in txt:
                        add_override_issue("优先级调权每条需包含“项:理由”格式，保证可审计")
                        break
        if ddd_mode != "off":
            if glossary_count < ddd_min_glossary:
                add_ddd_issue(f"DDD-Lite 统一语言词汇不足：至少 {ddd_min_glossary} 条（当前：{glossary_count}）")
            if context_count < ddd_min_contexts:
                add_ddd_issue(f"DDD-Lite 限界上下文不足：至少 {ddd_min_contexts} 条（当前：{context_count}）")
            if invariant_count < ddd_min_invariants:
                add_ddd_issue(f"DDD-Lite 业务不变量不足：至少 {ddd_min_invariants} 条（当前：{invariant_count}）")
            if selected_context_count < 1:
                add_ddd_issue("DDD-Lite 已选上下文（M0）至少需要 1 条")
            if selected_context_ref_count < 1:
                add_ddd_issue("DDD-Lite 已选上下文（M0）至少需要 1 个上下文 ID（示例：C1）")
            if direction_choice == "A":
                if not has_core_context:
                    add_ddd_issue("方向 A 需要至少 1 个 Core 上下文（示例：C1 [Core]）")
            elif direction_choice == "B":
                if selected_context_ref_count < 2:
                    add_ddd_issue("方向 B 需要至少 2 个已选上下文（Core + Supporting）")
                if not has_core_context or not has_supporting_context:
                    add_ddd_issue("方向 B 需要同时包含 Core 与 Supporting 上下文")
            elif direction_choice == "C":
                if selected_context_ref_count < 3:
                    add_ddd_issue("方向 C 需要至少 3 个已选上下文（含治理上下文）")
                if not has_core_context or not has_supporting_context:
                    add_ddd_issue("方向 C 需要同时包含 Core 与 Supporting 上下文")
                if not has_governance_context:
                    add_ddd_issue("方向 C 需要包含治理上下文（Governance/治理/审计）")

    def check_scope_guard() -> None:
        src = state.get("source", {})
        spec_exists = bool(src.get("spec", {}).get("exists", False))
        tasks_exists = bool(src.get("tasks", {}).get("exists", False))
        fields = state.get("discovery", {}).get("fields", {})
        spec = state.get("spec", {})
        tasks = state.get("tasks", {})

        must_count = len(fields.get("m0_must", []) or [])
        wont_count = len(fields.get("m0_wont", []) or [])
        out_count = int(spec.get("out_of_scope_count", 0) or 0)
        m0_task_count = int(tasks.get("m0_task_count", 0) or 0)

        if must_count < 1 or must_count > 3:
            errors.append(f"M0 Must 需要 1-3 项（当前：{must_count}）")
        if wont_count < 3:
            errors.append(f"M0 Won't 至少需要 3 项（当前：{wont_count}）")
        if not tasks_exists:
            errors.append("缺少文件 .rpi-outfile/specs/l0/tasks.md")
        else:
            if m0_task_count < 1:
                errors.append("M0 任务为空（至少需要 1 条）")
            if m0_task_count > 6:
                errors.append(f"M0 任务过多（当前：{m0_task_count}，建议 <= 6）")
        if not spec_exists:
            errors.append("缺少文件 .rpi-outfile/specs/l0/spec.md")
        elif out_count < 1:
            errors.append("spec.md 缺少可执行 Out-of-Scope 边界")

    if scope == "all":
        check_discovery()
        check_scope_guard()
    elif scope == "discovery":
        check_discovery()
    elif scope == "scope_guard":
        check_scope_guard()
    else:
        print(f"Invalid scope: {scope}", file=sys.stderr)
        return 1

    result = {
        "verified_at": utc_now(),
        "scope": scope,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
    }
    write_json(paths.verify_file, result)

    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    elif not quiet:
        if not errors:
            print(f"spec state verify passed (scope={scope})")
            for w in warnings:
                print(f"- WARNING: {w}")
        else:
            print(f"spec state verify failed (scope={scope}):", file=sys.stderr)
            for e in errors:
                print(f"- {e}", file=sys.stderr)

    return 0 if not errors else 1


def sync_source(paths: Paths, quiet: bool) -> int:
    rc = build_state(paths, quiet=True, print_path=False, force=False)
    if rc != 0:
        return rc
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    except Exception:
        print("Cannot read state.json to sync source", file=sys.stderr)
        return 1
    write_json(paths.source_file, source_from_state(state))
    if not quiet:
        print(f"Synced structured source: {paths.source_file}")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project-dir", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", add_help=False)
    p_build.add_argument("--quiet", action="store_true")
    p_build.add_argument("--print-path", action="store_true")
    p_build.add_argument("--force", action="store_true")
    p_build.add_argument("--help", "-h", action="store_true")

    p_verify = sub.add_parser("verify", add_help=False)
    p_verify.add_argument("--quiet", action="store_true")
    p_verify.add_argument("--json", action="store_true", dest="json_output")
    p_verify.add_argument("--scope", default="all")
    p_verify.add_argument("--help", "-h", action="store_true")

    p_sync = sub.add_parser("sync-source", add_help=False)
    p_sync.add_argument("--quiet", action="store_true")
    p_sync.add_argument("--help", "-h", action="store_true")

    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    ns = parse_args(argv)
    if ns.project_dir:
        project = Path(ns.project_dir).resolve()
        paths = load_paths_from_project(project)
    else:
        script_file = Path(__file__).resolve()
        paths = load_paths(script_file)

    if ns.cmd == "build":
        if ns.help:
            print(
                "Usage: python .claude/workflow/engine/spec_state_tool.py build "
                "[--quiet] [--print-path] [--force]"
            )
            return 0
        return build_state(paths, quiet=ns.quiet, print_path=ns.print_path, force=ns.force)

    if ns.cmd == "verify":
        if ns.help:
            print(
                "Usage: python .claude/workflow/engine/spec_state_tool.py verify "
                "[--scope all|discovery|scope_guard] [--quiet] [--json]"
            )
            return 0
        return verify_state(
            paths,
            scope=ns.scope,
            quiet=ns.quiet,
            json_output=ns.json_output,
        )

    if ns.cmd == "sync-source":
        if ns.help:
            print("Usage: python .claude/workflow/engine/spec_state_tool.py sync-source [--quiet]")
            return 0
        return sync_source(paths, quiet=ns.quiet)

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
