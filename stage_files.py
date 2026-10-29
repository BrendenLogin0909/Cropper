"""The single mandatory flat batch workflow; no image processing or API logic."""
from pathlib import Path
import threading

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
STAGES = {CROP_QUEUE: 1, ENHANCE_QUEUE: 2, AI_QUEUE: 3}
FOLDERS = {ORIGINAL, CROPPED, ENHANCED, SKIP_ENHANCE, AI, COLOR,
           SKIP_AI, '3.1.1. Local AI Enhancement', *STAGES}
LOCK = threading.RLock()


def plan(source: Path, operation: str) -> dict:
    source = source.resolve()
    parent = source.parent
    if any(ancestor.name in FOLDERS for ancestor in parent.parents):
        raise ValueError('Nested stage folders are not supported. Open the batch root or its stage folder directly.')
    root = parent.parent if parent.name in FOLDERS else parent
    stage = STAGES.get(parent.name)
    if operation == 'crop':
        if parent.name in FOLDERS and parent.name != ORIGINAL:
            raise ValueError('Crop from the batch root or 1. Original, not a later stage folder.')
        snapshots = [root / ORIGINAL / source.name]
        output = root / CROP_QUEUE / f'{source.stem}_c{source.suffix}'
    else:
        if stage is None:
            raise ValueError('Open a 1.5, 1.6 or 1.7 TBP stage folder for flat batch routing.')
        snapshots = [root / {1: CROPPED, 2: ENHANCED, 3: AI}[stage] / source.name]
        if operation == 'enhance':
            if stage != 1:
                raise ValueError('Auto-improve must start from 1.5. TBP Cropped.')
            output = root / ENHANCE_QUEUE / f'{source.stem}_e{source.suffix}'
        elif operation in {'restore', 'colorize', 'finish'}:
            if operation == 'restore' and stage == 3:
                raise ValueError('This photo already completed AI enhancement. Colourise or finish it instead.')
            if stage == 1:
                snapshots.append(root / SKIP_ENHANCE / source.name)
            if operation in {'colorize', 'finish'} and stage < 3:
                snapshots.append(root / SKIP_AI / source.name)
            output = None if operation == 'finish' else root / (
                COLOR if operation == 'colorize' else AI_QUEUE
            ) / f'{source.stem}{"_col" if operation == "colorize" else "_ai"}.png'
        else:
            raise ValueError('Invalid batch stage operation.')
    return {'source': source, 'root': root, 'snapshots': snapshots, 'output': output}


def commit(plans: list[dict], payloads: list[bytes | None]) -> list[Path]:
    """Save all snapshots/results before removing queue sources; rollback on failure.

    Identical existing snapshots can be reused; conflicting snapshots and any
    existing output are refused. Paired photos share one transaction.
    """
    if len(plans) != len(payloads):
        raise ValueError('Missing output data.')
    with LOCK:
        sources = [item['source'] for item in plans]
        if len(set(sources)) != len(sources):
            raise ValueError('The same source was selected more than once.')
        # Archive snapshots are unchanged originals, never user-authored names.
        # Validate the whole transaction before creating files or removing sources.
        for item in plans:
            for target in item['snapshots']:
                if target.name != item['source'].name:
                    raise ValueError('Archive filename must match the original source filename. Nothing was moved.')
        originals = {source: source.read_bytes() for source in sources}
        writes = {}
        outputs = []
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
                if output in writes or output.exists() or output in sources:
                    raise ValueError(f'Output already exists: {output}. Nothing was overwritten.')
                writes[output] = payload
                outputs.append(output)
        created, removed = [], []
        try:
            for target, data in writes.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as handle:
                    created.append(target)
                    handle.write(data)
            # Do not remove a queue source until every saved version is verified.
            for target, data in writes.items():
                if target.read_bytes() != data:
                    raise ValueError(f'Saved image verification failed: {target.name}')
            for item in plans:
                source = item['source']
                if source not in item['snapshots']:
                    # Detect a source changed while rendering; never archive stale bytes.
                    if source.read_bytes() != originals[source]:
                        raise ValueError(f'Source changed while saving: {source.name}')
                    source.unlink()
                    removed.append(source)
        except Exception:
            # If restoring a removed source fails, keep all saved copies recoverable.
            for source in removed:
                with source.open('xb') as handle:
                    handle.write(originals[source])
            for target in reversed(created):
                target.unlink(missing_ok=True)
            raise
        return outputs


def file_results(sources: list[Path], payloads: list[bytes], operation: str) -> list[Path]:
    plans = [plan(source, operation) for source in sources]
    if len({item['root'] for item in plans}) != 1:
        raise ValueError('Linked photos must belong to the same batch root.')
    # Distinguish same-stem standalone/composite files without losing identifiers.
    if len(plans) == 2 and plans[0]['output'] == plans[1]['output']:
        output = plans[1]['output']
        plans[1]['output'] = output.with_name(f'{sources[1].stem}_front-back{"_col" if operation == "colorize" else "_ai"}.png')
    return commit(plans, payloads)
