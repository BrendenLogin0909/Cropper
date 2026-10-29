"""Per-user Cropper settings. The API key is never stored in this file."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

SERVICE_NAME = "Cropper"
KEY_USERNAME = "openai_api_key"
DEFAULT_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_QUALITY = "medium"
VALID_QUALITIES = ("low", "medium", "high")
SETTINGS_NAME = "settings.json"

_data_dir: Path | None = None


def default_data_dir() -> Path:
    override = os.environ.get("CROPPER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata).expanduser().resolve() / "Cropper"
    # Never fall back to the system temp directory.
    return (Path.home() / ".cropper").resolve()


def set_data_dir(path: str | Path | None) -> Path:
    global _data_dir
    _data_dir = Path(path).expanduser().resolve() if path else default_data_dir()
    return _data_dir


def data_dir() -> Path:
    if _data_dir is None:
        set_data_dir(None)
    assert _data_dir is not None
    return _data_dir


def settings_path() -> Path:
    return data_dir() / SETTINGS_NAME


def _defaults() -> dict:
    return {
        "model": DEFAULT_MODEL,
        "quality": DEFAULT_QUALITY,
        "guide_seen": False,
        "filename_template": "{name}{suffix}{ext}",
        "conflict_policy": "skip",
        "replace_original": False,
        "backup_originals": True,
        "output_folder": "",
    }


def load_settings() -> dict:
    path = settings_path()
    data = _defaults()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            data.update({key: loaded[key] for key in data if key in loaded})
    data["model"] = str(data.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    quality = str(data.get("quality") or DEFAULT_QUALITY).strip().lower()
    data["quality"] = quality if quality in VALID_QUALITIES else DEFAULT_QUALITY
    data["guide_seen"] = bool(data.get("guide_seen"))
    data["filename_template"] = str(data.get("filename_template") or "{name}{suffix}{ext}")
    policy = str(data.get("conflict_policy") or "skip").strip().lower()
    data["conflict_policy"] = policy if policy in {"skip", "overwrite", "auto-suffix"} else "skip"
    data["replace_original"] = bool(data.get("replace_original"))
    data["backup_originals"] = bool(data.get("backup_originals", True))
    if data["replace_original"] and not data["backup_originals"]:
        data["replace_original"] = False
    data["output_folder"] = str(data.get("output_folder") or "")
    return data


def save_settings(updates: dict) -> dict:
    data = load_settings()
    if "model" in updates:
        model = str(updates["model"] or "").strip()
        if not model or len(model) > 80 or not re.fullmatch(r"[A-Za-z0-9._:-]+", model):
            raise ValueError("Enter a valid image model name.")
        data["model"] = model
    if "quality" in updates:
        quality = str(updates["quality"] or "").strip().lower()
        if quality not in VALID_QUALITIES:
            raise ValueError("Choose low, medium, or high quality.")
        data["quality"] = quality
    if "guide_seen" in updates:
        data["guide_seen"] = bool(updates["guide_seen"])
    if "filename_template" in updates:
        from stage_files import validate_template
        data["filename_template"] = validate_template(str(updates["filename_template"] or "").strip())
    if "conflict_policy" in updates:
        policy = str(updates["conflict_policy"] or "").strip().lower()
        if policy not in {"skip", "overwrite", "auto-suffix"}:
            raise ValueError("Choose Skip, Overwrite, or Auto-suffix.")
        data["conflict_policy"] = policy
    if "replace_original" in updates:
        data["replace_original"] = bool(updates["replace_original"])
    if "backup_originals" in updates:
        data["backup_originals"] = bool(updates["backup_originals"])
    if data.get("replace_original") and not data.get("backup_originals", True):
        raise ValueError("Replace original needs backups on. Cropper will not modify the original.")
    if "output_folder" in updates:
        data["output_folder"] = str(updates["output_folder"] or "").strip()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    return data


def image_model() -> str:
    return load_settings()["model"]


def image_quality(preview: bool = False) -> str:
    if preview:
        return "low"
    return load_settings()["quality"]


def mask_key(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


def env_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise ValueError("Install the keyring package from requirements.txt first.") from exc
    return keyring


def install_memory_keyring():
    """Test helper: keep keys in process memory, never on disk."""
    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1
        _passwords: dict

        def __init__(self):
            self._passwords = {}

        def get_password(self, service, username):
            return self._passwords.get((service, username))

        def set_password(self, service, username, password):
            self._passwords[(service, username)] = password

        def delete_password(self, service, username):
            self._passwords.pop((service, username), None)

    keyring.set_keyring(MemoryKeyring())
    return keyring.get_keyring()


def _read_stored_api_key() -> tuple[str, str]:
    """Return (password, error). Error is set when the credential store is unavailable."""
    try:
        value = _keyring().get_password(SERVICE_NAME, KEY_USERNAME)
    except Exception:
        return "", "Cropper could not read the API key from the system credential store."
    return (value or "").strip(), ""


def stored_api_key() -> str:
    value, _error = _read_stored_api_key()
    return value


def set_stored_api_key(value: str) -> None:
    key = (value or "").strip()
    if not key:
        raise ValueError("Paste an API key first.")
    if any(char in key for char in "\r\n\t") or len(key) > 500:
        raise ValueError("That API key looks invalid.")
    try:
        _keyring().set_password(SERVICE_NAME, KEY_USERNAME, key)
    except Exception as exc:
        raise ValueError("Cropper could not save the API key in the system credential store.") from exc


def clear_stored_api_key() -> None:
    try:
        _keyring().delete_password(SERVICE_NAME, KEY_USERNAME)
    except Exception as exc:
        name = type(exc).__name__
        if "PasswordDeleteError" in name or "PasswordDelete" in name:
            return
        raise ValueError("Cropper could not remove the saved API key from the system credential store.") from exc


def resolved_api_key() -> str:
    return env_api_key() or stored_api_key()


def key_source() -> str:
    if env_api_key():
        return "env"
    if stored_api_key():
        return "stored"
    return "none"


def public_settings() -> dict:
    data = load_settings()
    env_key = env_api_key()
    stored, keyring_error = _read_stored_api_key()
    if env_key:
        source = "env"
        key = env_key
    elif stored:
        source = "stored"
        key = stored
    else:
        source = "none"
        key = ""
    return {
        "model": data["model"],
        "quality": data["quality"],
        "guide_seen": data["guide_seen"],
        "filename_template": data.get("filename_template", "{name}{suffix}{ext}"),
        "conflict_policy": data.get("conflict_policy", "skip"),
        "replace_original": bool(data.get("replace_original")),
        "backup_originals": bool(data.get("backup_originals", True)),
        "output_folder": data.get("output_folder") or "",
        "has_key": bool(key),
        "key_masked": mask_key(key),
        "key_source": source,
        "keyring_error": keyring_error,
        "default_model": DEFAULT_MODEL,
    }


def configure_runtime(data_directory: str | Path | None = None) -> Path:
    root = set_data_dir(data_directory)
    root.mkdir(parents=True, exist_ok=True)
    photo_pairs = sys.modules.get("photo_pairs")
    if photo_pairs is not None:
        photo_pairs.ROOT = root
    pending = sys.modules.get("chatgpt_pending")
    if pending is not None:
        pending.PENDING_ROOT = root / "pending"
    return root
