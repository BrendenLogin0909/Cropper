"""Durable local review queue for generated images; originals remain untouched."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from PIL import Image

# Resolved at call time so --data-dir is honoured. Never the system temp directory.
PENDING_ROOT = None
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
PENDING_TTL_SECONDS = 7 * 24 * 60 * 60
FRONT_PATH_PATTERN = re.compile(r'"front_path"\s*:\s*"((?:\\.|[^"\\])*)"')
STRING_FIELD_PATTERN = re.compile(
    r'"(front_path|photo_note|batch_context|notes|guidance|guidance_notes|comment|operation)"\s*:\s*"((?:\\.|[^"\\])*)"'
)
LIST_FIELD_PATTERN = re.compile(r'"(comments|output_paths)"\s*:\s*(\[[^\]]*\])')
LOG = logging.getLogger("cropper.pending")


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


def _atomic_write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".cropper-", suffix=".json", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as target:
            json.dump(record, target, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def image_is_readable(path: Path) -> bool:
    """Readable/unreadable is judged on the IMAGE, not the JSON."""
    if not path.is_file() or path.stat().st_size < 8:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _guidance(record: dict | None) -> list[str]:
    if not isinstance(record, dict):
        return []
    notes = []
    comments = record.get("comments")
    if isinstance(comments, list):
        notes.extend(str(item).strip() for item in comments if str(item).strip())
    for key in ("notes", "guidance", "guidance_notes", "comment", "photo_note", "batch_context"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(value.strip())
        elif isinstance(value, list):
            notes.extend(str(item).strip() for item in value if str(item).strip())
    seen = set()
    unique = []
    for item in notes:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _parse_json_text(text: str) -> tuple[dict | None, bool]:
    try:
        loaded = json.loads(text)
        return (loaded, True) if isinstance(loaded, dict) else (None, False)
    except json.JSONDecodeError:
        try:
            loaded, _ = json.JSONDecoder().raw_decode(text)
            return (loaded, False) if isinstance(loaded, dict) else (None, False)
        except json.JSONDecodeError:
            return None, False


def _salvage_record(metadata: Path) -> tuple[dict | None, bool]:
    if not metadata.is_file():
        return None, False
    try:
        text = metadata.read_text(encoding="utf-8")
    except OSError:
        return None, False
    record, complete = _parse_json_text(text)
    if record is not None:
        return record, complete
    salvaged: dict = {}
    for match in STRING_FIELD_PATTERN.finditer(text):
        try:
            salvaged[match.group(1)] = json.loads(f'"{match.group(2)}"')
        except json.JSONDecodeError:
            salvaged[match.group(1)] = match.group(2)
    for match in LIST_FIELD_PATTERN.finditer(text):
        try:
            loaded = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
        salvaged[match.group(1)] = loaded
    if not salvaged.get("front_path"):
        match = FRONT_PATH_PATTERN.search(text)
        if match:
            try:
                salvaged["front_path"] = json.loads(f'"{match.group(1)}"')
            except json.JSONDecodeError:
                salvaged["front_path"] = match.group(1)
    if salvaged:
        return salvaged, False
    return None, False


def _log_recovery(kind: str, ident: str) -> None:
    # Never log note/guidance/comment text.
    LOG.warning("Pending recovery %s id=%s", kind, ident)


def _complete_record(record: dict, image: Path) -> bool:
    if not isinstance(record, dict) or record.get("id") != image.stem:
        return False
    if not record.get("front_path"):
        return False
    return image_is_readable(image)


def save_pending(source: Path, png: bytes, root: Path | None = None, stage_routing=None, revision=None,
                 operation=None, output_path=None, output_paths=None) -> dict:
    root = _resolve_root(root)
    cleanup_expired(root)
    root.mkdir(parents=True, exist_ok=True)
    stage_id = secrets.token_urlsafe(24)
    metadata, image = _paths(stage_id, root)
    record = {
        "id": stage_id,
        "front_path": str(source.resolve()),
        "created": time.time(),
        "operation": operation or "restore",
    }
    if stage_routing is not None:
        record["stage_routing"] = bool(stage_routing)
    if output_path is not None:
        record["output_path"] = str(output_path)
    if output_paths:
        record["output_paths"] = [str(path) for path in output_paths]
    if revision:
        record.update(revision)
        record.setdefault("operation", operation or "restore")
        record["id"] = stage_id
        record["front_path"] = str(source.resolve())
    try:
        with image.open("xb") as target:
            target.write(png)
            target.flush()
            os.fsync(target.fileno())
        _atomic_write_json(metadata, record)
    except Exception:
        metadata.unlink(missing_ok=True)
        # Leave a written PNG for Recovered results; never hide a paid image.
        if not image.is_file():
            image.unlink(missing_ok=True)
        raise
    if output_paths:
        from stage_files import bind_reservation_ids
        bind_reservation_ids(output_paths, stage_id)
    elif output_path is not None:
        from stage_files import bind_reservation_ids
        bind_reservation_ids([output_path], stage_id)
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
    record["link"] = link
    metadata, _ = _paths(stage_id, root)
    _atomic_write_json(metadata, record)
    return record


def set_operation(stage_id: str, operation: str, root: Path | None = None) -> dict:
    root = _resolve_root(root)
    record, _ = get_pending(stage_id, root)
    record["operation"] = operation
    metadata, _ = _paths(stage_id, root)
    _atomic_write_json(metadata, record)
    return record


def list_pending(folder: Path, root: Path | None = None) -> list[dict]:
    root = _resolve_root(root)
    cleanup_expired(root)
    if not root.is_dir():
        return []
    folder = folder.resolve()
    items = []
    for metadata in root.glob("*.json"):
        if metadata.name.startswith(".cropper-"):
            continue
        try:
            record, image = get_pending(metadata.stem, root)
            if record.get("expired"):
                continue
            if not image_is_readable(image):
                continue
            from stage_files import same_path
            if same_path(Path(record["front_path"]).parent, folder):
                items.append(record)
        except (ValueError, OSError, KeyError, json.JSONDecodeError, TypeError):
            continue
    return sorted(items, key=lambda item: item["created"])


def list_recovered(root: Path | None = None) -> list[dict]:
    """Orphaned PNGs and unreadable JSON; never hidden or deleted silently."""
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    seen = set()
    items = []
    stems = {path.stem for path in root.glob("*.json") if not path.name.startswith(".cropper-")}
    stems |= {path.stem for path in root.glob("*.png") if not path.name.startswith(".cropper-")}
    for stem in sorted(stems):
        if not ID_PATTERN.fullmatch(stem) or stem in seen:
            continue
        seen.add(stem)
        metadata, image = root / f"{stem}.json", root / f"{stem}.png"
        salvaged, json_complete = _salvage_record(metadata) if metadata.is_file() else (None, False)
        readable = image_is_readable(image)
        expired = bool(salvaged and salvaged.get("expired"))
        if json_complete and salvaged and _complete_record(salvaged, image) and not expired:
            continue
        if readable and json_complete and salvaged and salvaged.get("id") == stem and salvaged.get("front_path") and not expired:
            continue
        if expired and readable:
            reason = "expired"
        elif readable:
            reason = "orphaned image" if not metadata.is_file() else (
                "truncated metadata" if not json_complete else "incomplete metadata")
        elif image.is_file():
            reason = "damaged image"
        elif metadata.is_file():
            reason = "missing image"
        else:
            continue
        _log_recovery(reason, stem)
        notes = _guidance(salvaged)
        items.append({
            "id": stem,
            "image_readable": readable,
            "can_keep": readable,
            "reason": reason,
            "front_path": (salvaged or {}).get("front_path") or "",
            "operation": (salvaged or {}).get("operation") or "restore",
            "notes": notes,
        })
    return items


def keep_recovered(stage_id: str, root: Path | None = None) -> dict:
    """Refile a readable recovered IMAGE as a complete pending record. Notes stay with Keep."""
    root = _resolve_root(root)
    metadata, image = _paths(stage_id, root)
    if not image_is_readable(image):
        raise ValueError("This recovered image is damaged. Discard it.")
    salvaged, _ = _salvage_record(metadata)
    salvaged = dict(salvaged or {})
    record = {
        "id": stage_id,
        "front_path": salvaged.get("front_path") or str(image.resolve()),
        "created": float(salvaged.get("created") or time.time()),
        "operation": salvaged.get("operation") or "restore",
        "recovered": True,
    }
    for key in ("stage_routing", "link", "parent_id", "attempt", "comments", "included_previous",
                "output_path", "output_paths", "notes", "guidance", "guidance_notes", "comment",
                "photo_note", "batch_context"):
        if key in salvaged:
            record[key] = salvaged[key]
    _atomic_write_json(metadata, record)
    return record


def discard_recovered(stage_id: str, root: Path | None = None) -> None:
    """Delete the recovered image, metadata, and any guidance notes."""
    remove_pending(stage_id, root)


def _release_record_reservations(stage_id: str, record: dict | None) -> None:
    from stage_files import release_pending_reservations
    extra = reserved_from_record(record) if isinstance(record, dict) else []
    release_pending_reservations(stage_id, extra)


def remove_pending(stage_id: str, root: Path | None = None) -> None:
    root = _resolve_root(root)
    metadata, image = _paths(stage_id, root)
    salvaged, _ = _salvage_record(metadata) if metadata.is_file() else (None, False)
    try:
        _release_record_reservations(stage_id, salvaged)
    except Exception:
        LOG.warning("Pending reservation release failed id=%s", stage_id)
    metadata.unlink(missing_ok=True)
    image.unlink(missing_ok=True)


def reserved_from_record(record: dict) -> list[Path]:
    paths = []
    if record.get("output_path"):
        paths.append(Path(record["output_path"]))
    for value in record.get("output_paths") or []:
        paths.append(Path(value))
    return paths


def cleanup_expired(root: Path | None = None, now: float | None = None, ttl: int = PENDING_TTL_SECONDS) -> list[str]:
    """Move pending AI images older than ttl into Recovered. Never delete them.

    Entries with no created stamp get a timestamp instead of being removed.
    Incomplete or unreadable recovered files are left for Keep/Discard.
    """
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    stamp = time.time() if now is None else now
    moved = []
    for metadata in list(root.glob("*.json")):
        if metadata.name.startswith(".cropper-"):
            continue
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
            image = metadata.with_suffix(".png")
            if not _complete_record(record, image):
                continue
            created = record.get("created")
            if not created:
                record["created"] = stamp
                _atomic_write_json(metadata, record)
                continue
            if stamp - float(created) < ttl:
                continue
            if record.get("expired"):
                continue
            ident = record.get("id") or metadata.stem
            record["expired"] = True
            _atomic_write_json(metadata, record)
            try:
                _release_record_reservations(ident, record)
            except Exception:
                LOG.warning("Pending reservation release failed id=%s", ident)
            moved.append(ident)
        except (ValueError, OSError, KeyError, json.JSONDecodeError, TypeError):
            continue
    try:
        from stage_files import sweep_stale_reservations
        sweep_stale_reservations()
    except Exception:
        LOG.warning("Pending reservation sweep failed")
    return moved
