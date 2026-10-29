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


def _settings():
    try:
        from app_settings import load_settings
        return load_settings()
    except Exception:
        return {'filename_template': '{name}{suffix}{ext}', 'conflict_policy': 'skip',
                'replace_original': False, 'backup_originals': True, 'output_folder': ''}


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
    name = name.strip()
    if not name or INVALID_NAME.search(name) or name in {'.', '..'} or '/' in name or '\\' in name:
        raise ValueError('Filename template produces invalid characters.')
    return name


def unique_path(path: Path, taken: set[Path]) -> Path:
    resolved = {item.resolve() for item in taken}
    candidate = path
    n = 1
    while candidate.resolve() in resolved or candidate.exists():
        n += 1
        if n > 10000:
            raise ValueError('Could not find a free output name.')
        candidate = path.with_name(f'{path.stem}-{n}{path.suffix}')
    return candidate


def apply_conflict(path: Path, taken: set[Path], policy: str) -> tuple[Path, bool]:
    policy = (policy or 'skip').lower()
    exists = path.resolve() in {item.resolve() for item in taken} or path.exists()
    if not exists:
        return path, False
    if policy == 'overwrite':
        return path, True
    if policy == 'auto-suffix':
        return unique_path(path, taken), False
    raise ValueError(f'Output already exists: {path}. Nothing was overwritten.')


@contextmanager
def interprocess_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            locked = False
            while not locked:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == 'nt':
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


def reserved_paths() -> set[Path]:
    path = reservation_file()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    return {Path(item).resolve() for item in data if item}


def _write_reservations(paths: set[Path]) -> None:
    path = reservation_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix='.cropper-', suffix='.json', dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as target:
            json.dump(sorted(str(item) for item in paths), target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


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
    lock_path = reservation_file().with_suffix('.lock')
    with interprocess_lock(lock_path), LOCK:
        current = reserved_paths()
        for path in paths:
            current.discard(Path(path).resolve())
        _write_reservations(current)


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
    """Write target. Returns True if this call created a new file to roll back."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        handle, temp_name = tempfile.mkstemp(prefix='.cropper-', suffix=target.suffix, dir=target.parent)
        os.close(handle)
        temp = Path(temp_name)
        try:
            temp.write_bytes(data)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return False
    with target.open('xb') as handle:
        handle.write(data)
    return True


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
                    source.unlink()
        except Exception:
            restored_all = True
            for source in removed:
                try:
                    if source.exists() and source.read_bytes() == originals[source]:
                        continue
                    with source.open('xb') as handle:
                        handle.write(originals[source])
                except Exception:
                    restored_all = False
                    recovered = source.with_name(source.name + '.cropper-recovered')
                    try:
                        recovered.write_bytes(originals[source])
                    except Exception:
                        pass
            if restored_all:
                for target in reversed(created):
                    target.unlink(missing_ok=True)
                for target, previous in overwrites.items():
                    target.write_bytes(previous)
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
