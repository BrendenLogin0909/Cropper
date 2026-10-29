"""The single mandatory flat batch workflow; no image processing or API logic."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import os
import re
import tempfile
import threading
import time

ORIGINAL = '1. Original'
CROP_QUEUE = '1.5. TBP Cropped'
ENHANCE_QUEUE = '1.6. TBP Auto-Improved'
AI_QUEUE = '1.7. TBP AI-Improved'
CROPPED = '2. Cropped'
ENHANCED = '3. Auto-Improved'
SKIP_ENHANCE = '3.2. Skipped Auto-Improved'
AI = '4. AI-Enhancement'
COLOR = '4.1.1. AI Colourised'
SKIP_AI = '4.2. Skipped AI-Enhancement'
BACKUPS = '0. Backups'
STAGES = {CROP_QUEUE: 1, ENHANCE_QUEUE: 2, AI_QUEUE: 3}
FOLDERS = {ORIGINAL, CROPPED, ENHANCED, SKIP_ENHANCE, AI, COLOR,
           SKIP_AI, BACKUPS, '3.1.1. Local AI Enhancement', *STAGES}
LOCK = threading.RLock()
OPERATION_SUFFIX = {'crop': '_c', 'enhance': '_e', 'restore': '_ai', 'colorize': '_col'}
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TEMPLATE_FIELDS = {'name', 'date', 'index', 'ext', 'suffix'}
LOCK_TIMEOUT_SECONDS = 30
WINDOWS_RESERVED_STEMS = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL'}
    | {f'COM{i}' for i in range(10)}
    | {f'LPT{i}' for i in range(10)}
)
FILE_LOCKED_SUFFIXES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp', '.bmp', '.gif', '.json'}


def _settings():
    try:
        from app_settings import load_settings
        return load_settings()
    except Exception:
        return {'filename_template': '{name}{suffix}{ext}', 'conflict_policy': 'skip',
                'replace_original': False, 'backup_originals': True, 'output_folder': ''}


def path_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _is_windows() -> bool:
    return os.name == 'nt'


def windows_long_path(path: Path) -> Path:
    """Prefix a Windows long-path marker so paths over 260 characters can be opened."""
    text = os.fspath(path)
    if not _is_windows() or text.startswith('\\\\?\\'):
        return Path(path)
    absolute = str(Path(path).absolute())
    if absolute.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + absolute[2:])
    return Path('\\\\?\\' + absolute)


def windows_safe_filename(name: str) -> str:
    """Reject invalid characters and rewrite Windows reserved device names."""
    name = (name or '').strip().rstrip(' .')
    if not name or name in {'.', '..'} or INVALID_NAME.search(name) or '/' in name or '\\' in name:
        raise ValueError('Filename template produces invalid characters.')
    stem, suffix = Path(name).stem, Path(name).suffix
    if stem.upper() in WINDOWS_RESERVED_STEMS:
        name = f'_{stem}{suffix}'
    return name


def recovered_path(source: Path) -> Path:
    base = source.with_name(source.name + '.cropper-recovered')
    if not base.exists():
        return base
    n = 2
    while n <= 10000:
        candidate = source.with_name(f'{source.name}.cropper-recovered-{n}')
        if not candidate.exists():
            return candidate
        n += 1
    raise ValueError('Could not find a free recovered name.')


def permission_error_response(exc: PermissionError) -> tuple[int, dict]:
    name = getattr(exc, 'filename', None)
    path = Path(name) if name else None
    locked_file = False
    if path is not None:
        try:
            locked_file = path.is_file()
        except OSError:
            locked_file = False
        if not locked_file:
            locked_file = path.suffix.lower() in FILE_LOCKED_SUFFIXES
    if locked_file:
        return 403, {
            'code': 'file_locked',
            'error': f'Cropper cannot write {path.name}. Close the file in other programs and retry.',
            'path': str(path),
        }
    return 403, {
        'code': 'permission_denied',
        'error': "Cropper cannot write to this location. Choose another output folder or check this folder's permissions.",
        'path': str(path) if path is not None else None,
    }


def validate_template(template: str) -> str:
    text = (template or '').strip() or '{name}{suffix}{ext}'
    try:
        text.format(name='photo', date='20261029', index='1', ext='.png', suffix='_c')
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError('Filename template supports {name}, {date}, {index} and {ext}.') from exc
    sample = render_filename(Path('photo.jpg'), 'crop', template=text, index=1, date='20261029')
    if INVALID_NAME.search(sample) or '/' in sample or '\\' in sample:
        raise ValueError('Filename template produces invalid characters.')
    return text


def render_filename(source: Path, operation: str, index: int = 1, template: str | None = None,
                    date: str | None = None) -> str:
    settings = _settings()
    template = template or settings.get('filename_template') or '{name}{suffix}{ext}'
    suffix = OPERATION_SUFFIX.get(operation, '')
    ext = source.suffix if operation in {'crop', 'enhance'} else (source.suffix if operation == 'finish' else '.png')
    try:
        name = template.format(name=source.stem, date=date or time.strftime('%Y%m%d'),
                               index=str(index), ext=ext, suffix=suffix)
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError('Filename template supports {name}, {date}, {index} and {ext}.') from exc
    return windows_safe_filename(name)


def unique_path(path: Path, taken: set[Path]) -> Path:
    resolved = {path_key(item) for item in taken}
    candidate = path
    n = 1
    while path_key(candidate) in resolved or candidate.exists():
        n += 1
        if n > 10000:
            raise ValueError('Could not find a free output name.')
        candidate = path.with_name(windows_safe_filename(f'{path.stem}-{n}{path.suffix}'))
    return candidate


def apply_conflict(path: Path, taken: set[Path], policy: str) -> tuple[Path, bool]:
    policy = (policy or 'skip').lower()
    exists = path_key(path) in {path_key(item) for item in taken} or path.exists()
    if not exists:
        return path, False
    if policy == 'overwrite':
        return path, True
    if policy == 'auto-suffix':
        return unique_path(path, taken), False
    raise ValueError(f'Output already exists: {path}. Nothing was overwritten.')


@contextmanager
def interprocess_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
    path = windows_long_path(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+b')
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        deadline = time.monotonic() + max(0.05, float(timeout))
        if _is_windows():
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            'Cropper could not lock files because another Cropper is busy. Try again.'
                        )
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            'Cropper could not lock files because another Cropper is busy. Try again.'
                        )
                    time.sleep(0.05)
        yield
    finally:
        try:
            if _is_windows():
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def reservation_file() -> Path:
    from app_settings import data_dir
    return data_dir() / 'output-reservations.json'


def _load_reservation_entries() -> list[dict]:
    path = reservation_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    entries = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item:
                entries.append({'path': item, 'id': None, 'created': 0})
            elif isinstance(item, dict) and item.get('path'):
                entries.append({
                    'path': str(item['path']),
                    'id': item.get('id') or None,
                    'created': float(item.get('created') or 0),
                })
    return entries


def reserved_paths() -> set[Path]:
    return {Path(item['path']).resolve() for item in _load_reservation_entries() if item.get('path')}


def _write_reservation_entries(entries: list[dict]) -> None:
    path = reservation_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{'path': item['path'], 'id': item.get('id'), 'created': item.get('created') or 0}
               for item in entries if item.get('path')]
    handle, temp_name = tempfile.mkstemp(prefix='.cropper-', suffix='.json', dir=str(windows_long_path(path.parent)))
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as target:
            json.dump(payload, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, windows_long_path(path))
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _write_reservations(paths: set[Path]) -> None:
    now = time.time()
    previous = {path_key(Path(item['path'])): item for item in _load_reservation_entries() if item.get('path')}
    entries = []
    for item in paths:
        key = path_key(item)
        existing = previous.get(key, {})
        entries.append({
            'path': str(Path(item).resolve()),
            'id': existing.get('id'),
            'created': existing.get('created') or now,
        })
    _write_reservation_entries(entries)


def pending_output_paths() -> set[Path]:
    from chatgpt_pending import default_pending_root
    root = default_pending_root()
    taken = set()
    if not root.is_dir():
        return taken
    for metadata in root.glob('*.json'):
        try:
            record = json.loads(metadata.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        for value in [record.get('output_path'), *(record.get('output_paths') or [])]:
            if value:
                taken.add(Path(value).resolve())
    return taken


def taken_outputs() -> set[Path]:
    return reserved_paths() | pending_output_paths()


def reserve_plan_outputs(plans: list[dict], policy: str | None = None) -> list[dict]:
    """Assign saveable output names before a paid call. Holds names until accept/reject."""
    policy = (policy or _settings().get('conflict_policy') or 'skip').lower()
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        taken = taken_outputs()
        for item in plans:
            output = item.get('output')
            if output is None:
                continue
            chosen, overwrite = apply_conflict(output, taken, policy)
            item['output'] = chosen
            item['overwrite'] = overwrite
            taken.add(chosen.resolve())
        current = reserved_paths()
        current.update(item['output'].resolve() for item in plans if item.get('output') is not None)
        _write_reservations(current)
        return plans


def release_outputs(paths: list[Path | str]) -> None:
    if not paths or not reservation_file().is_file():
        return
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        drop = {path_key(Path(path)) for path in paths}
        entries = [item for item in _load_reservation_entries()
                   if item.get('path') and path_key(Path(item['path'])) not in drop]
        _write_reservation_entries(entries)


def bind_reservation_ids(paths: list[Path | str], stage_id: str) -> None:
    if not paths or not stage_id or not reservation_file().is_file():
        return
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        keys = {path_key(Path(path)) for path in paths}
        now = time.time()
        entries = _load_reservation_entries()
        for item in entries:
            if item.get('path') and path_key(Path(item['path'])) in keys:
                item['id'] = stage_id
                item['created'] = item.get('created') or now
        _write_reservation_entries(entries)


def release_pending_reservations(stage_id: str, extra_paths: list[Path | str] | None = None) -> None:
    if not reservation_file().is_file():
        return
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        extra = {path_key(Path(path)) for path in (extra_paths or [])}
        entries = []
        for item in _load_reservation_entries():
            if item.get('id') and item.get('id') == stage_id:
                continue
            if item.get('path') and path_key(Path(item['path'])) in extra:
                continue
            entries.append(item)
        _write_reservation_entries(entries)


def sweep_stale_reservations() -> None:
    """Drop reservations whose pending id is gone."""
    if not reservation_file().is_file():
        return
    from chatgpt_pending import default_pending_root
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        root = default_pending_root()
        live = set()
        if root.is_dir():
            live = {path.stem for path in root.glob('*.json') if not path.name.startswith('.cropper-')}
            live |= {path.stem for path in root.glob('*.png') if not path.name.startswith('.cropper-')}
        entries = []
        changed = False
        for item in _load_reservation_entries():
            ident = item.get('id')
            if ident and ident not in live:
                changed = True
                continue
            entries.append(item)
        if changed:
            _write_reservation_entries(entries)


def prepare_ai_plans(front: Path, operation: str, composite: Path | None = None) -> list[dict]:
    """Reserve saveable output names before a paid AI call."""
    sources = [front] + ([composite] if composite is not None else [])
    plans = [plan(source, operation) for source in sources]
    if len({item['root'] for item in plans}) != 1:
        raise ValueError('Linked photos must belong to the same batch root.')
    if len(plans) == 2 and plans[0]['output'] == plans[1]['output']:
        output = plans[1]['output']
        suffix = '_col' if operation == 'colorize' else '_ai'
        plans[1]['output'] = output.with_name(f'{sources[1].stem}_front-back{suffix}.png')
    return reserve_plan_outputs(plans)


def plan_root(item: dict) -> Path:
    if item.get('root') is not None:
        return Path(item['root'])
    source = Path(item['source'])
    parent = source.parent
    return parent.parent if parent.name in FOLDERS else parent


def plan(source: Path, operation: str) -> dict:
    source = source.resolve()
    parent = source.parent
    if any(ancestor.name in FOLDERS for ancestor in parent.parents):
        raise ValueError('Nested stage folders are not supported. Open the batch root or its stage folder directly.')
    settings = _settings()
    custom_root = str(settings.get('output_folder') or '').strip()
    root = Path(custom_root).expanduser().resolve() if custom_root else (
        parent.parent if parent.name in FOLDERS else parent)
    stage = STAGES.get(parent.name)
    replace = bool(settings.get('replace_original')) and operation != 'finish'
    if operation == 'crop':
        if parent.name in FOLDERS and parent.name != ORIGINAL:
            raise ValueError('Crop from the batch root or 1. Original, not a later stage folder.')
        snapshots = [root / ORIGINAL / source.name]
        output = root / CROP_QUEUE / render_filename(source, 'crop')
    else:
        if stage is None:
            raise ValueError('Open a 1.5, 1.6 or 1.7 TBP stage folder for flat batch routing.')
        snapshots = [root / {1: CROPPED, 2: ENHANCED, 3: AI}[stage] / source.name]
        if operation == 'enhance':
            if stage != 1:
                raise ValueError('Auto-improve must start from 1.5. TBP Cropped.')
            output = root / ENHANCE_QUEUE / render_filename(source, 'enhance')
        elif operation in {'restore', 'colorize', 'finish'}:
            if operation == 'restore' and stage == 3:
                raise ValueError('This photo already completed AI enhancement. Colourise or finish it instead.')
            if stage == 1:
                snapshots.append(root / SKIP_ENHANCE / source.name)
            if operation in {'colorize', 'finish'} and stage < 3:
                snapshots.append(root / SKIP_AI / source.name)
            output = None if operation == 'finish' else root / (
                COLOR if operation == 'colorize' else AI_QUEUE
            ) / render_filename(source, operation)
        else:
            raise ValueError('Invalid batch stage operation.')
    if replace:
        if not settings.get('backup_originals', True):
            raise ValueError('Replace original needs backups on. Cropper will not modify the original.')
        backup = root / BACKUPS / source.name
        if backup.exists() and backup.read_bytes() != source.read_bytes():
            backup = unique_path(backup, {backup})
        snapshots = [backup]
        output = source
        return {'source': source, 'root': root, 'snapshots': snapshots, 'output': output, 'overwrite': True}
    return {'source': source, 'root': root, 'snapshots': snapshots, 'output': output, 'overwrite': False}


def _atomic_create(target: Path, data: bytes, overwrite: bool = False) -> bool:
    """Write target via temp + os.replace. Returns True if this call created a new file to roll back."""
    target = Path(target)
    parent = windows_long_path(target.parent)
    parent.mkdir(parents=True, exist_ok=True)
    destination = windows_long_path(target)
    if not overwrite and destination.exists():
        raise FileExistsError(str(target))
    handle, temp_name = tempfile.mkstemp(prefix='.cropper-', suffix=target.suffix or '.tmp', dir=str(parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, 'wb') as writer:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return not overwrite


def commit(plans: list[dict], payloads: list[bytes | None]) -> list[Path]:
    """Save all snapshots/results before removing queue sources; rollback on failure.

    Identical existing snapshots can be reused; conflicting snapshots and any
    existing output are refused unless Overwrite or Auto-suffix is chosen.
    Paired photos share one transaction.
    """
    if len(plans) != len(payloads):
        raise ValueError('Missing output data.')
    roots = {plan_root(item) for item in plans}
    lock_path = next(iter(roots)) / '.cropper.lock'
    with interprocess_lock(lock_path), LOCK:
        sources = [item['source'] for item in plans]
        if len(set(sources)) != len(sources):
            raise ValueError('The same source was selected more than once.')
        for item in plans:
            for target in item['snapshots']:
                if target.name != item['source'].name and BACKUPS not in Path(target).parts:
                    raise ValueError('Archive filename must match the original source filename. Nothing was moved.')
        originals = {source: source.read_bytes() for source in sources}
        writes = {}
        overwrites = {}
        outputs = []
        policy = _settings().get('conflict_policy') or 'skip'
        for item, payload in zip(plans, payloads):
            source = item['source']
            for target in item['snapshots']:
                data = originals[source]
                if target in writes and writes[target] != data:
                    raise ValueError(f'Conflicting snapshot names: {target.name}')
                if target.exists():
                    if target.read_bytes() != data:
                        raise ValueError(f'A different snapshot already exists: {target}. Nothing was overwritten.')
                else:
                    writes[target] = data
            output = item['output']
            if output is not None:
                if payload is None:
                    raise ValueError('Missing processed image.')
                overwrite = bool(item.get('overwrite'))
                if not overwrite and (output in writes or output.exists() or output in sources):
                    chosen, overwrite = apply_conflict(output, set(writes) | set(sources) | taken_outputs(), policy)
                    output = chosen
                    item['output'] = output
                    item['overwrite'] = overwrite
                    if not overwrite and (output in writes or output.exists() or output in sources):
                        raise ValueError(f'Output already exists: {output}. Nothing was overwritten.')
                if overwrite and output.exists():
                    overwrites[output] = output.read_bytes()
                writes[output] = payload
                outputs.append(output)
        created, removed = [], []
        try:
            for target, data in writes.items():
                overwrite = target in overwrites
                new_file = _atomic_create(target, data, overwrite=overwrite)
                if new_file:
                    created.append(target)
            for target, data in writes.items():
                if target.read_bytes() != data:
                    raise ValueError(f'Saved image verification failed: {target.name}')
            for item in plans:
                source = item['source']
                if source not in item['snapshots'] and source != item.get('output'):
                    if source.read_bytes() != originals[source]:
                        raise ValueError(f'Source changed while saving: {source.name}')
                    removed.append(source)
                    windows_long_path(source).unlink()
        except Exception:
            restored_all = True
            for source in removed:
                try:
                    if source.exists() and source.read_bytes() == originals[source]:
                        continue
                    _atomic_create(source, originals[source], overwrite=False)
                except Exception:
                    restored_all = False
                    recovered = recovered_path(source)
                    try:
                        _atomic_create(recovered, originals[source], overwrite=False)
                    except Exception:
                        pass
            if restored_all:
                for target in reversed(created):
                    windows_long_path(target).unlink(missing_ok=True)
                for target, previous in overwrites.items():
                    _atomic_create(target, previous, overwrite=True)
            raise
        return outputs


def file_results(sources: list[Path], payloads: list[bytes], operation: str,
                 output_paths: list[Path] | None = None) -> list[Path]:
    plans = [plan(source, operation) for source in sources]
    if len({item['root'] for item in plans}) != 1:
        raise ValueError('Linked photos must belong to the same batch root.')
    if len(plans) == 2 and plans[0]['output'] == plans[1]['output']:
        output = plans[1]['output']
        plans[1]['output'] = output.with_name(f'{sources[1].stem}_front-back{"_col" if operation == "colorize" else "_ai"}.png')
    if output_paths:
        if len(output_paths) != len(plans):
            raise ValueError('Missing output data.')
        for item, dest in zip(plans, output_paths):
            if dest is not None:
                item['output'] = Path(dest)
                item['overwrite'] = Path(dest).exists() and (_settings().get('conflict_policy') == 'overwrite')
    return commit(plans, payloads)
