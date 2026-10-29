"""Durable local review queue for generated images; originals remain untouched."""

from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import time

# Resolved at call time so --data-dir is honoured. Never the system temp directory.
PENDING_ROOT = None
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
PENDING_TTL_SECONDS = 7 * 24 * 60 * 60


def default_pending_root() -> Path:
    if PENDING_ROOT is not None:
        return PENDING_ROOT
    from app_settings import data_dir
    return data_dir() / "pending"


def _resolve_root(root: Path | None) -> Path:
    return root if root is not None else default_pending_root()


def _paths(stage_id: str, root: Path) -> tuple[Path, Path]:
    if not ID_PATTERN.fullmatch(stage_id):
        raise ValueError("Invalid pending image ID.")
    return root / f"{stage_id}.json", root / f"{stage_id}.png"


def save_pending(source: Path, png: bytes, root: Path | None = None, stage_routing=None, revision=None) -> dict:
    root = _resolve_root(root)
    cleanup_expired(root)
    root.mkdir(parents=True, exist_ok=True)
    stage_id = secrets.token_urlsafe(24)
    metadata, image = _paths(stage_id, root)
    record = {"id": stage_id, "front_path": str(source.resolve()), "created": time.time()}
    if stage_routing is not None:
        record['stage_routing'] = bool(stage_routing)
    if revision is not None:
        record.update(revision)
    try:
        with image.open("xb") as target:
            target.write(png)
        with metadata.open("x", encoding="utf-8") as target:
            json.dump(record, target)
    except Exception:
        image.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        raise
    return record


def get_pending(stage_id: str, root: Path | None = None) -> tuple[dict, Path]:
    root = _resolve_root(root)
    metadata, image = _paths(stage_id, root)
    if not metadata.is_file() or not image.is_file():
        raise ValueError("Pending image is unavailable.")
    record = json.loads(metadata.read_text(encoding="utf-8"))
    if record.get("id") != stage_id:
        raise ValueError("Pending image metadata is invalid.")
    return record, image


def attach_link(stage_id: str, link: dict, root: Path | None = None) -> dict:
    root = _resolve_root(root)
    record, _ = get_pending(stage_id, root)
    record['link'] = link
    metadata, _ = _paths(stage_id, root)
    temporary = metadata.with_suffix('.tmp')
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(metadata)
    return record

def set_operation(stage_id: str, operation: str, root: Path | None = None) -> dict:
    root = _resolve_root(root)
    record, _ = get_pending(stage_id, root)
    record['operation'] = operation
    metadata, _ = _paths(stage_id, root)
    temporary = metadata.with_suffix('.tmp')
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(metadata)
    return record


def list_pending(folder: Path, root: Path | None = None) -> list[dict]:
    root = _resolve_root(root)
    cleanup_expired(root)
    if not root.is_dir():
        return []
    items = []
    for metadata in root.glob("*.json"):
        try:
            record, _ = get_pending(metadata.stem, root)
            if Path(record["front_path"]).parent == folder.resolve():
                items.append(record)
        except (ValueError, OSError, KeyError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: item["created"])


def remove_pending(stage_id: str, root: Path | None = None) -> None:
    root = _resolve_root(root)
    metadata, image = _paths(stage_id, root)
    metadata.unlink(missing_ok=True)
    image.unlink(missing_ok=True)


def cleanup_expired(root: Path | None = None, now: float | None = None, ttl: int = PENDING_TTL_SECONDS) -> list[str]:
    """Delete pending AI images older than ttl. Never stores them in system temp."""
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    stamp = time.time() if now is None else now
    removed = []
    for metadata in list(root.glob("*.json")):
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
            created = float(record.get("created") or 0)
            if created and stamp - created < ttl:
                continue
            ident = record.get("id") or metadata.stem
            remove_pending(ident, root)
            removed.append(ident)
        except (ValueError, OSError, KeyError, json.JSONDecodeError, TypeError):
            continue
    return removed
