#!/usr/bin/env python3
"""Portable RPI Eval Suite templates and multidimensional regression comparison."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


TEMPLATE_NAMES = ("structured-extraction", "grounded-generation", "agent-tool-use")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read eval file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"eval file must contain a JSON object: {path}")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def templates_dir(project_dir: Path) -> Path:
    return project_dir / ".rpi" / "evals" / "templates"


def cmd_list(project_dir: Path) -> int:
    rows = []
    for name in TEMPLATE_NAMES:
        data = read_json(templates_dir(project_dir) / f"{name}.json")
        rows.append({"name": name, "description": data.get("description", "")})
    print(json.dumps({"templates": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_init(project_dir: Path, template: str, suite_name: str) -> int:
    source = templates_dir(project_dir) / f"{template}.json"
    if not source.exists():
        raise ValueError(f"unknown eval template: {template}")
    destination = project_dir / ".rpi-outfile" / "evals" / "suites" / f"{suite_name}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"eval suite already exists: {destination}")
    data = read_json(source)
    data["suite_id"] = suite_name
    data["created_at"] = utc_now()
    data["dataset"]["version"] = "0.1.0"
    write_json(destination, data)
    print(json.dumps({"suite": str(destination.relative_to(project_dir)), "template": template}, ensure_ascii=False, indent=2))
    return 0


def metric_value(item: Any) -> float:
    if isinstance(item, (int, float)):
        return float(item)
    if isinstance(item, dict) and isinstance(item.get("value"), (int, float)):
        return float(item["value"])
    raise ValueError("metric value must be numeric")


def cmd_compare(project_dir: Path, baseline_path: Path, candidate_path: Path, output: Path | None) -> int:
    baseline = read_json(baseline_path)
    candidate = read_json(candidate_path)
    base_metrics = baseline.get("metrics", {})
    candidate_metrics = candidate.get("metrics", {})
    if not isinstance(base_metrics, dict) or not isinstance(candidate_metrics, dict):
        raise ValueError("both run files require a metrics object")
    rows = []
    critical_regressions = []
    for name in sorted(set(base_metrics) | set(candidate_metrics)):
        if name not in base_metrics or name not in candidate_metrics:
            present = base_metrics.get(name) if name in base_metrics else candidate_metrics.get(name)
            critical = bool(present.get("critical", False)) if isinstance(present, dict) else False
            rows.append({"metric": name, "status": "missing", "baseline": base_metrics.get(name), "candidate": candidate_metrics.get(name), "critical": critical})
            if critical:
                critical_regressions.append(name)
            continue
        base_item = base_metrics[name]
        candidate_item = candidate_metrics[name]
        base_value = metric_value(base_item)
        candidate_value = metric_value(candidate_item)
        metadata = candidate_item if isinstance(candidate_item, dict) else base_item if isinstance(base_item, dict) else {}
        higher = bool(metadata.get("higher_is_better", True))
        critical = bool(metadata.get("critical", False))
        delta = candidate_value - base_value
        improved = delta > 0 if higher else delta < 0
        regressed = delta < 0 if higher else delta > 0
        status = "improved" if improved else "regressed" if regressed else "unchanged"
        row = {
            "metric": name,
            "baseline": base_value,
            "candidate": candidate_value,
            "delta": delta,
            "higher_is_better": higher,
            "critical": critical,
            "status": status,
        }
        rows.append(row)
        if critical and regressed:
            critical_regressions.append(name)
    report = {
        "schema_version": 1,
        "compared_at": utc_now(),
        "baseline": {"path": str(baseline_path), "model": baseline.get("model"), "prompt_version": baseline.get("prompt_version"), "tool_version": baseline.get("tool_version")},
        "candidate": {"path": str(candidate_path), "model": candidate.get("model"), "prompt_version": candidate.get("prompt_version"), "tool_version": candidate.get("tool_version")},
        "metrics": rows,
        "decision": "manual_review_required" if critical_regressions else "eligible",
        "critical_regressions": critical_regressions,
        "note": "eligible does not auto-upgrade the model; cost, latency, privacy, and product tradeoffs still require review",
    }
    if output:
        resolved = output if output.is_absolute() else project_dir / output
        write_json(resolved, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if critical_regressions else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPI Eval Suite tool")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    init = sub.add_parser("init")
    init.add_argument("template", choices=TEMPLATE_NAMES)
    init.add_argument("suite_name")
    compare = sub.add_parser("compare")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    try:
        if args.command == "list":
            return cmd_list(project_dir)
        if args.command == "init":
            return cmd_init(project_dir, args.template, args.suite_name)
        if args.command == "compare":
            return cmd_compare(project_dir, args.baseline, args.candidate, args.output)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
