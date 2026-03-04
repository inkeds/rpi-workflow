#!/usr/bin/env python3
"""Snapshot and restore helpers for generated workflow artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import file_lock


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_compact_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def normalize_rel_path(project_dir: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(project_dir.resolve())
    except Exception:
        return ""
    text = str(rel).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def normalize_rel_input(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def canonical_target_key(raw: str) -> str:
    text = normalize_rel_input(raw)
    if text.startswith(".rpi-outfile/"):
        return text[1:]
    return text


def recovery_root(project_dir: Path) -> Path:
    return project_dir / ".rpi-outfile" / "state" / "recovery"


def recovery_index_path(project_dir: Path) -> Path:
    return recovery_root(project_dir) / "index.jsonl"


def ensure_layout(project_dir: Path) -> None:
    root = recovery_root(project_dir)
    (root / "snapshots").mkdir(parents=True, exist_ok=True)
    index_file = recovery_index_path(project_dir)
    if not index_file.exists():
        index_file.touch()


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=False)
    file_lock.append_line_locked(path, line)


def _read_index_rows(index_file: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not index_file.exists():
        return rows
    for line in index_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{dst.name}.tmp.", dir=str(dst.parent))
    try:
        with src.open("rb") as src_h, os.fdopen(fd, "wb") as dst_h:
            shutil.copyfileobj(src_h, dst_h)
        os.replace(tmp_path, dst)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def snapshot_files(
    project_dir: Path,
    targets: Sequence[Path],
    reason: str,
    actor: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    ensure_layout(project_dir)
    index_file = recovery_index_path(project_dir)
    stamp = utc_compact_now()
    ts = utc_now()
    snapshot_root = recovery_root(project_dir) / "snapshots" / stamp
    rows: List[Dict[str, Any]] = []
    seq = 0

    for raw_target in targets:
        target = raw_target.resolve()
        if not target.is_file():
            continue
        rel_target = normalize_rel_path(project_dir, target)
        if not rel_target:
            continue

        seq += 1
        snapshot_path = snapshot_root / rel_target
        if snapshot_path.exists():
            snapshot_path = snapshot_root / f"{rel_target}.{seq:03d}.bak"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, snapshot_path)

        row: Dict[str, Any] = {
            "kind": "snapshot",
            "id": f"{stamp}-{seq:03d}",
            "ts": ts,
            "reason": reason,
            "actor": actor,
            "target": rel_target,
            "snapshot": normalize_rel_path(project_dir, snapshot_path),
            "size_bytes": int(snapshot_path.stat().st_size),
            "sha256": _sha256_file(snapshot_path),
        }
        if isinstance(extra, dict):
            row["extra"] = dict(extra)

        _append_jsonl(index_file, row)
        rows.append(row)

    return rows


def list_snapshot_rows(project_dir: Path, target: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    ensure_layout(project_dir)
    rows = _read_index_rows(recovery_index_path(project_dir))
    out: List[Dict[str, Any]] = []
    target_norm = normalize_rel_input(target)
    target_key = canonical_target_key(target_norm)
    for row in rows:
        if str(row.get("kind", "")) != "snapshot":
            continue
        row_target = normalize_rel_input(str(row.get("target", "")))
        if target_norm and canonical_target_key(row_target) != target_key:
            continue
        out.append(row)
    if limit > 0:
        return out[-limit:]
    return out


def find_snapshot_row(project_dir: Path, target: str, snapshot_ref: str = "") -> Optional[Dict[str, Any]]:
    target_norm = normalize_rel_input(target)
    rows = list_snapshot_rows(project_dir, target=target_norm, limit=0)
    if not rows:
        return None
    ref = snapshot_ref.strip()
    if not ref:
        return rows[-1]
    for row in reversed(rows):
        row_id = str(row.get("id", ""))
        snap = str(row.get("snapshot", ""))
        if ref == row_id or ref == snap or snap.endswith(ref):
            return row
    return None


def restore_snapshot(
    project_dir: Path,
    target: str,
    snapshot_ref: str = "",
    reason: str = "manual_restore",
    actor: str = "",
) -> Dict[str, Any]:
    ensure_layout(project_dir)
    row = find_snapshot_row(project_dir, target=target, snapshot_ref=snapshot_ref)
    if not row:
        raise FileNotFoundError(f"no snapshot found for target: {target}")

    target_rel = normalize_rel_input(str(row.get("target", "")))
    snapshot_rel = normalize_rel_input(str(row.get("snapshot", "")))
    if not target_rel or not snapshot_rel:
        raise FileNotFoundError("invalid snapshot record")

    target_abs = (project_dir / target_rel).resolve()
    snapshot_abs = (project_dir / snapshot_rel).resolve()
    if not target_abs.exists() and target_rel.startswith("rpi-outfile/"):
        target_abs = (project_dir / ("." + target_rel)).resolve()
    if not snapshot_abs.is_file() and snapshot_rel.startswith("rpi-outfile/"):
        snapshot_abs = (project_dir / ("." + snapshot_rel)).resolve()
    if not snapshot_abs.is_file():
        raise FileNotFoundError(f"snapshot file missing: {snapshot_rel}")

    pre_restore = snapshot_files(
        project_dir,
        [target_abs],
        reason=f"pre_restore:{reason}",
        actor=actor,
        extra={"source_snapshot": snapshot_rel, "target": target_rel},
    )
    _copy_atomic(snapshot_abs, target_abs)

    result: Dict[str, Any] = {
        "kind": "restore",
        "ts": utc_now(),
        "reason": reason,
        "actor": actor,
        "target": target_rel,
        "snapshot": snapshot_rel,
        "restored_sha256": _sha256_file(target_abs),
    }
    if pre_restore:
        result["pre_restore_snapshot"] = str(pre_restore[-1].get("snapshot", ""))

    _append_jsonl(recovery_index_path(project_dir), result)
    return result
