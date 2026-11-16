"""Decoded-preview and prediction result caches for local corner detection."""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps

import crop_detection

PREVIEW_SIZE = 1400
MEMORY_CAP = 100 * 1024 * 1024
RESULT_CAP = 64


def file_identity(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return (str(resolved), int(stat.st_mtime_ns), int(stat.st_size))


def _image_bytes(image: Image.Image | None) -> int:
    if image is None:
        return 0
    width, height = image.size
    return width * height * len(image.getbands())


class ImagePreviewCache:
    def __init__(self, memory_cap: int = MEMORY_CAP):
        self.memory_cap = memory_cap
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, int, int], dict] = OrderedDict()
        self.bytes_used = 0

    def get(self, path: Path) -> dict | None:
        key = file_identity(path)
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            self._data.move_to_end(key)
            return item

    def put(self, path: Path, detect: Image.Image, preview: Image.Image | None, original_size: tuple[int, int]) -> None:
        key = file_identity(path)
        nbytes = _image_bytes(detect) + _image_bytes(preview)
        with self._lock:
            previous = self._data.pop(key, None)
            if previous:
                self.bytes_used -= previous['nbytes']
            while self._data and self.bytes_used + nbytes > self.memory_cap:
                _, old = self._data.popitem(last=False)
                self.bytes_used -= old['nbytes']
            self._data[key] = {
                'detect': detect,
                'preview': preview,
                'original_size': original_size,
                'nbytes': nbytes,
            }
            self.bytes_used += nbytes

    def evict_resolved(self, resolved: str) -> None:
        with self._lock:
            drop = [key for key in self._data if key[0] == resolved]
            for key in drop:
                old = self._data.pop(key)
                self.bytes_used = max(0, self.bytes_used - old['nbytes'])

    def contains_resolved(self, resolved: str) -> bool:
        with self._lock:
            return any(key[0] == resolved for key in self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.bytes_used = 0

    def __len__(self) -> int:
        return len(self._data)


class ResultCache:
    def __init__(self, cap: int = RESULT_CAP):
        self.cap = cap
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple, dict] = OrderedDict()

    def make_key(self, path: Path, target) -> tuple:
        identity = file_identity(path)
        packed = None if target is None else (float(target[0]), float(target[1]))
        return identity + (packed,)

    def get(self, path: Path, target) -> dict | None:
        key = self.make_key(path, target)
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            self._data.move_to_end(key)
            return item

    def put(self, path: Path, target, result: dict) -> None:
        key = self.make_key(path, target)
        payload = {'candidates': result.get('candidates', []), 'elapsed_ms': result.get('elapsed_ms', 0)}
        resolved = str(path.resolve())
        with self._lock:
            stale = [item for item in self._data if item[0] == resolved and item != key]
            for item in stale:
                del self._data[item]
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = payload
                return
            while len(self._data) >= self.cap:
                self._data.popitem(last=False)
            self._data[key] = payload

    def evict_resolved(self, resolved: str) -> None:
        with self._lock:
            drop = [key for key in self._data if key[0] == resolved]
            for key in drop:
                del self._data[key]

    def contains_resolved(self, resolved: str) -> bool:
        with self._lock:
            return any(key[0] == resolved for key in self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


IMAGE_CACHE = ImagePreviewCache()
RESULT_CACHE = ResultCache()

_prefetch_cv = threading.Condition()
_prefetch_queue: list[Path] = []
_prefetch_started = False
_foreground = 0
_foreground_lock = threading.Lock()
_abort = threading.Event()


def reset() -> None:
    IMAGE_CACHE.clear()
    RESULT_CACHE.clear()
    with _prefetch_cv:
        _prefetch_queue.clear()
        _prefetch_cv.notify_all()
    with _foreground_lock:
        global _foreground
        _foreground = 0
    _abort.set()


def evict_path(path: Path) -> None:
    """Drop both LRUs for a resolved path after our own write."""
    resolved = str(Path(path).resolve())
    IMAGE_CACHE.evict_resolved(resolved)
    RESULT_CACHE.evict_resolved(resolved)
    _abort.set()


def begin_foreground() -> None:
    global _foreground
    with _foreground_lock:
        _foreground += 1
    _abort.set()


def end_foreground() -> None:
    global _foreground
    with _foreground_lock:
        _foreground = max(0, _foreground - 1)


def _foreground_busy() -> bool:
    return _foreground > 0


def prefetch_aborted() -> bool:
    return _abort.is_set() or _foreground_busy()


def fill_from_full_image(path: Path, image: Image.Image) -> None:
    detect = crop_detection.detection_preview(image)
    preview = image.copy()
    preview.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
    IMAGE_CACHE.put(path, detect, preview, image.size)


def decode_detection_image(path: Path, allow_draft: bool = True) -> tuple[Image.Image, tuple[int, int], bool]:
    cached = IMAGE_CACHE.get(path)
    if cached is not None:
        return cached['detect'], cached['original_size'], False
    with Image.open(path) as source:
        original_size = source.size
        orientation = source.getexif().get(274)
        if orientation in {5, 6, 7, 8}:
            original_size = (original_size[1], original_size[0])
        used_draft = False
        if allow_draft and source.format == 'JPEG' and max(source.size) > 2000:
            source.draft('RGB', (crop_detection.DETECTION_SIZE, crop_detection.DETECTION_SIZE))
            used_draft = True
        image = ImageOps.exif_transpose(source).convert('RGB')
    detect = crop_detection.detection_preview(image)
    if used_draft:
        IMAGE_CACHE.put(path, detect, None, original_size)
    else:
        preview = image.copy()
        preview.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
        IMAGE_CACHE.put(path, detect, preview, original_size)
    return detect, original_size, used_draft


def preview_image(path: Path, full_image: Image.Image | None = None) -> tuple[Image.Image, tuple[int, int]]:
    cached = IMAGE_CACHE.get(path)
    if cached is not None and cached['preview'] is not None:
        return cached['preview'], cached['original_size']
    if full_image is None:
        raise FileNotFoundError(path)
    fill_from_full_image(path, full_image)
    cached = IMAGE_CACHE.get(path)
    return cached['preview'], cached['original_size']


def predict_cached(path: Path, target=None, foreground: bool = True) -> dict:
    if foreground:
        begin_foreground()
    try:
        cached = RESULT_CACHE.get(path, target)
        if cached is not None:
            return cached
        try:
            identity = file_identity(path)
        except OSError:
            identity = None
        detect, original_size, _ = decode_detection_image(path, allow_draft=True)
        abort = prefetch_aborted if not foreground else None
        result = crop_detection.predict_corners(detect, target, original_size=original_size, abort=abort)
        if result.get('aborted') or (not foreground and prefetch_aborted()):
            result = dict(result)
            result['aborted'] = True
            return result
        try:
            if identity is not None and file_identity(path) != identity:
                return result
        except OSError:
            return result
        RESULT_CACHE.put(path, target, result)
        return result
    finally:
        if foreground:
            end_foreground()


def queue_prefetch(paths: list[Path]) -> None:
    _ensure_worker()
    _abort.set()
    with _prefetch_cv:
        _prefetch_queue.clear()
        _prefetch_queue.extend(paths)
        _prefetch_cv.notify()


def _ensure_worker() -> None:
    global _prefetch_started
    with _foreground_lock:
        if _prefetch_started:
            return
        _prefetch_started = True
    thread = threading.Thread(target=_prefetch_worker, name='crop-prefetch', daemon=True)
    thread.start()


def _prefetch_worker() -> None:
    while True:
        with _prefetch_cv:
            while not _prefetch_queue:
                _prefetch_cv.wait()
            path = _prefetch_queue.pop(0)
        with _foreground_lock:
            if _foreground:
                continue
            _abort.clear()
        try:
            if RESULT_CACHE.get(path, None) is not None:
                continue
            predict_cached(path, None, foreground=False)
        except Exception:
            continue
