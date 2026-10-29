"""Cropper: a local-only image browser and perspective crop service."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import tempfile
import threading
import webbrowser
import warnings
import app_settings
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
from PIL import Image, ImageOps
from enhance import auto_enhance
from chatgpt_restore import edit_front, file_accepted
from image_api_errors import image_api_error
from chatgpt_pending import (
    attach_link, discard_recovered, get_pending, keep_recovered, list_pending,
    list_recovered, remove_pending, reserved_from_record, save_pending, set_operation,
)
from colorize import candidates
from photo_pairs import read_links, approve_link, suggestions, accept_linked
from restore import OPERATIONS, preview_dust_marks, restore, useful_change
import stage_files
from crop_detection import predict_corners


HERE = Path(__file__).resolve().parent
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
TOKEN = secrets.token_urlsafe(32)
RESTORE_LOCK = threading.Lock()
REVIEW_LOCKS = {}
REVIEW_LOCKS_GUARD = threading.Lock()
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def hostname_of(host_header: str) -> str:
    value = (host_header or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def origin_is_local(origin: str) -> bool:
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in LOCAL_HOSTS


def request_is_local(handler: BaseHTTPRequestHandler) -> bool:
    if hostname_of(handler.headers.get("Host", "")) not in LOCAL_HOSTS:
        return False
    origin = handler.headers.get("Origin")
    if origin:
        return origin_is_local(origin)
    return True


@contextmanager
def review_lock(front):
    # Different photos remain independent; competing actions on one source fail before payment.
    with REVIEW_LOCKS_GUARD:
        lock = REVIEW_LOCKS.setdefault(str(front), threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('This photo has a review or resubmission in progress. Please wait.')
    try:
        yield
    finally:
        lock.release()


def clean_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose a folder or image first.")
    return Path(value).expanduser().resolve()


def folder_listing(value: str) -> dict:
    folder = clean_path(value)
    if not folder.is_dir():
        raise ValueError("Folder not found.")
    folders = sorted(
        ({"name": child.name, "path": str(child)} for child in folder.iterdir() if child.is_dir()),
        key=lambda item: item["name"].lower(),
    )
    return {"path": str(folder), "parent": str(folder.parent) if folder.parent != folder else None, "folders": folders}


def image_path(value: str) -> Path:
    path = clean_path(value)
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError("That image is unavailable or its format is unsupported.")
    return path


def order_corners(points: list[list[float]]) -> np.ndarray:
    """Accept four clicks in any order; return TL, TR, BR, BL."""
    array = np.asarray(points, dtype=np.float32)
    if array.shape != (4, 2) or not np.isfinite(array).all():
        raise ValueError("Select four valid corners.")
    hull = cv2.convexHull(array).reshape(-1, 2)
    if len(hull) != 4 or abs(cv2.contourArea(hull)) < 25:
        raise ValueError("Corners must form a four-sided area.")
    centre = array.mean(axis=0)
    angles = np.arctan2(array[:, 1] - centre[1], array[:, 0] - centre[0])
    ordered = array[np.argsort(angles)]
    ordered = np.roll(ordered, -int(np.argmin(ordered.sum(axis=1))), axis=0)
    # The angular order begins at top-left and proceeds clockwise in image space.
    return ordered.astype(np.float32)


def load_image(path: Path) -> tuple[Image.Image, dict]:
    with Image.open(path) as source:
        original_info = {key: source.info.get(key) for key in ("icc_profile", "exif")}
        image = ImageOps.exif_transpose(source).convert("RGB")
    return image, original_info


def warp(image: Image.Image, points: list[list[float]], rotation: int = 0, aspect_ratio: float | None = None) -> Image.Image:
    corners = order_corners(points)
    width, height = image.size
    if np.any(corners[:, 0] < 0) or np.any(corners[:, 0] >= width) or np.any(corners[:, 1] < 0) or np.any(corners[:, 1] >= height):
        raise ValueError("A corner lies outside the image.")
    top, right, bottom, left = [np.linalg.norm(corners[i] - corners[(i + 1) % 4]) for i in range(4)]
    out_width = max(1, int(round(max(top, bottom))))
    out_height = max(1, int(round(max(left, right))))
    if aspect_ratio is not None:
        aspect_ratio = float(aspect_ratio)
        if not np.isfinite(aspect_ratio) or not 0.2 <= aspect_ratio <= 5:
            raise ValueError("Choose a valid width-to-height proportion.")
        # Preserve the longer measured dimension and expand the other one.
        # The four image-space edges alone do not establish the print's true ratio.
        if out_width / out_height < aspect_ratio:
            out_width = max(out_width, int(round(out_height * aspect_ratio)))
        else:
            out_height = max(out_height, int(round(out_width / aspect_ratio)))
    if out_width * out_height > 200_000_000:
        raise ValueError("The crop is too large to process safely.")
    target = np.float32([[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]])
    matrix = cv2.getPerspectiveTransform(corners, target)
    result = cv2.warpPerspective(np.asarray(image), matrix, (out_width, out_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    output = Image.fromarray(result)
    if rotation == 90:
        output = output.transpose(Image.Transpose.ROTATE_270)
    elif rotation == -90:
        output = output.transpose(Image.Transpose.ROTATE_90)
    elif rotation == 180:
        output = output.transpose(Image.Transpose.ROTATE_180)
    elif rotation != 0:
        raise ValueError("Rotation must be 0, 90, -90, or 180 degrees.")
    return output





def save_image(source: Path, points: list[list[float]], settings: dict, rotation: int, aspect_ratio: float | None = None) -> tuple[Path, tuple[int, int]]:
    image, info = load_image(source)
    cropped = warp(image, points, rotation, aspect_ratio)
    destination = stage_files.file_results([source], [rendered_bytes(cropped, source.suffix, info)], 'crop')[0]
    return destination, cropped.size


def rendered_bytes(image: Image.Image, extension: str, info: dict) -> bytes:
    # Reuse the existing format/metadata policy, not a new processing pipeline.
    with tempfile.TemporaryDirectory(prefix='cropper-render-') as temporary:
        rendered = Path(temporary) / ('image' + extension)
        save_rendered(image, rendered, info)
        return rendered.read_bytes()


def save_rendered(image: Image.Image, destination: Path, info: dict) -> None:
    extension = destination.suffix.lower()
    format_name = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP"}[extension]
    options = {}
    if format_name == "JPEG":
        options.update(quality=95, subsampling=0, optimize=True)
    elif format_name == "WEBP":
        options.update(quality=95, method=6)
    if info.get("icc_profile"):
        options["icc_profile"] = info["icc_profile"]
    # EXIF orientation is reset after the pixels are transposed; keep other metadata.
    if info.get("exif"):
        try:
            exif = Image.Exif()
            exif.load(info["exif"])
            exif[274] = 1
            options["exif"] = exif.tobytes()
        except (ValueError, TypeError, OSError):
            pass
    handle, temp_name = tempfile.mkstemp(prefix=".cropper-", suffix=destination.suffix, dir=destination.parent)
    os.close(handle)
    temp = Path(temp_name)
    try:
        image.save(temp, format=format_name, **options)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def rotate_image(image: Image.Image, rotation: int) -> Image.Image:
    if rotation == 0:
        return image
    if rotation == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if rotation == -90:
        return image.transpose(Image.Transpose.ROTATE_90)
    if rotation == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    raise ValueError("Rotation must be 0, 90, -90, or 180 degrees.")


def merge_front_and_back(front: Image.Image, back: Image.Image, back_rotation: int = 0) -> Image.Image:
    """Place the back to the right of portrait fronts and below landscape fronts."""
    back = rotate_image(back, back_rotation)
    front_width, front_height = front.size
    if front_width < front_height:
        new_width = max(1, round(back.width * front_height / back.height))
        back = back.resize((new_width, front_height), Image.Resampling.LANCZOS)
        seam = max(4, min(20, round(front_height * 0.003)))
        size = (front_width + seam + back.width, front_height)
        if size[0] * size[1] > 200_000_000:
            raise ValueError("The merged image is too large to process safely.")
        output = Image.new("RGB", size, "white")
        output.paste(front, (0, 0)); output.paste(back, (front_width + seam, 0))
        return output
    new_height = max(1, round(back.height * front_width / back.width))
    back = back.resize((front_width, new_height), Image.Resampling.LANCZOS)
    seam = max(4, min(20, round(front_width * 0.003)))
    size = (front_width, front_height + seam + back.height)
    if size[0] * size[1] > 200_000_000:
        raise ValueError("The merged image is too large to process safely.")
    output = Image.new("RGB", size, "white")
    output.paste(front, (0, 0)); output.paste(back, (0, front_height + seam))
    return output


def save_merged_image(front_path: Path, back_path: Path, settings: dict, name_source: str, back_rotation: int) -> tuple[Path, tuple[int, int]]:
    if front_path.resolve() == back_path.resolve():
        raise ValueError("Choose two different images: the front first, then its back.")
    front, front_info = load_image(front_path)
    back, _ = load_image(back_path)
    merged = merge_front_and_back(front, back, back_rotation)
    source_for_name = back_path if name_source == "back" else front_path if name_source == "front" else None
    if source_for_name is None:
        raise ValueError("Choose whether to name the pair from the first or second image.")
    if front_path.parent != back_path.parent or front_path.parent.name != stage_files.CROP_QUEUE:
        raise ValueError('Merge flat-batch pairs from the same 1.5. TBP Cropped folder.')
    stem = source_for_name.stem
    if stem.endswith('_c'): stem = stem[:-2]
    destination = front_path.parent / f'{stem}_front-back_c{source_for_name.suffix}'
    # Keep the standalone cropped front/back: only add the merged version.
    stage_files.commit([{'source': source_for_name.resolve(), 'snapshots':[source_for_name.resolve()], 'output':destination}],
                       [rendered_bytes(merged, source_for_name.suffix, front_info)])
    return destination, merged.size





def save_enhanced_image(source: Path, settings: dict) -> tuple[Path, tuple[int, int], list[str], Path]:
    stage_files.plan(source, 'enhance')
    link = next((item for item in read_links(source.parent) if str(source) in {item['front_path'], item['composite_path']}), None)
    if link:
        source = image_path(link['front_path'])
    image, info = load_image(source)
    enhanced, changes = auto_enhance(image)
    planned = stage_files.plan(source, 'enhance')
    sources, payloads = [source], [rendered_bytes(enhanced, source.suffix, info)]
    if link:
        from chatgpt_restore import reassemble
        composite = image_path(link['composite_path'])
        stage_files.plan(composite, 'enhance')
        _, composite_info = load_image(composite)
        combined = reassemble(rendered_bytes(enhanced, '.png', info), composite, link['split'], link['layout'])
        with Image.open(io.BytesIO(combined)) as merged:
            payloads.append(rendered_bytes(merged, composite.suffix, composite_info))
        sources.append(composite)
    outputs = stage_files.file_results(sources, payloads, 'enhance')
    if link:
        try:
            approve_link(outputs[0], outputs[1], link['split'], link['layout'])
        except (OSError, ValueError) as error:
            warnings.warn(f'Images saved, but output pair link could not be carried forward: {error}')
    return outputs[0], enhanced.size, changes, planned['snapshots'][0]


def save_restored_image(source: Path, operation: str, strokes: list | None = None) -> tuple[Path | None, tuple[int, int], list[str], Path | None]:
    if operation not in OPERATIONS:
        raise ValueError("Choose a valid restoration tab.")
    planned = stage_files.plan(source, 'enhance')
    with RESTORE_LOCK:
        image, info = load_image(source)
        restored, changes = restore(image, operation, strokes)
        if not useful_change(changes):
            return None, restored.size, changes, None
        destination = stage_files.file_results([source], [rendered_bytes(restored, source.suffix, info)], 'enhance')[0]
    return destination, restored.size, changes, planned['snapshots'][0]


class Handler(BaseHTTPRequestHandler):
    server_version = "Cropper/1.0"

    def log_message(self, format: str, *args) -> None:
        text = format % args
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", text)
        print("%s - %s" % (self.address_string(), text))

    def reply(self, status: int, body: bytes, content_type: str, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, payload: dict) -> None:
        self.reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def authorized(self, query: dict | None = None) -> bool:
        given = self.headers.get("X-Cropper-Token", "") or (query or {}).get("token", [""])[0]
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        origin_ok = not origin or origin == f"http://{host}" or origin_is_local(origin)
        return secrets.compare_digest(given, TOKEN) and request_is_local(self) and origin_ok

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/session":
                if not request_is_local(self):
                    self.json(403, {"error": "Session is only available on localhost."})
                    return
                info = app_settings.public_settings()
                self.json(200, {
                    "app": "Cropper",
                    "token": TOKEN,
                    "home": str(Path.home()),
                    "stage_routing_version": 2,
                    "has_key": info["has_key"],
                    "key_source": info["key_source"],
                    "model": info["model"],
                    "quality": info["quality"],
                })
                return
            if parsed.path in {"/", "/index.html", "/app.css", "/chatgpt.css", "/stage-routing.css", "/app.js"}:
                name = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                file = HERE / name
                self.reply(200, file.read_bytes(), MIME[file.suffix] + "; charset=utf-8")
                return
            if not self.authorized(query):
                self.json(403, {"error": "Unauthorized request."})
                return
            if parsed.path == "/api/settings":
                self.json(200, app_settings.public_settings())
                return
            if parsed.path == "/api/locations":
                home = Path.home()
                shortcuts = [{"name": label, "path": str(path)} for label, path in (
                    ("Home", home), ("Desktop", home / "Desktop"),
                    ("Pictures", home / "Pictures"), ("Documents", home / "Documents"),
                ) if path.is_dir()]
                drives = [{"name": drive, "path": drive} for drive in os.listdrives()] if hasattr(os, "listdrives") else []
                self.json(200, {"shortcuts": shortcuts, "drives": drives})
                return
            if parsed.path == "/api/chatgpt-preview":
                stage_id = query.get("id", [""])[0]
                record, image = get_pending(stage_id)
                png = image.read_bytes()
                if query.get('part', [''])[0] == 'combined' and record.get('link'):
                    from chatgpt_restore import reassemble
                    link = record['link']
                    png = reassemble(png, Path(link['composite_path']), link['split'], link['layout'])
                self.reply(200, png, "image/png")
                return
            if parsed.path == '/api/photo-links':
                folder = clean_path(query.get('path', [''])[0])
                self.json(200, {'links': read_links(folder)})
                return
            if parsed.path == "/api/chatgpt-pending":
                folder = clean_path(query.get("path", [""])[0])
                if not folder.is_dir():
                    raise ValueError("Folder not found.")
                self.json(200, {"items": list_pending(folder), "recovered": list_recovered()})
                return
            if parsed.path == "/api/folders":
                self.json(200, folder_listing(query.get("path", [""])[0]))
                return
            if parsed.path == "/api/list":
                base = folder_listing(query.get("path", [""])[0])
                folder = Path(base["path"])
                entries = list(folder.iterdir())
                images = sorted(({"name": p.name, "path": str(p), "bytes": p.stat().st_size, "modified": p.stat().st_mtime} for p in entries if p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda x: x["name"].lower())
                self.json(200, {**base, "images": images})
                return
            if parsed.path in {"/api/image", "/api/thumb", "/api/enhance-original"}:
                path = image_path(query.get("path", [""])[0])
                limit = 440 if parsed.path == "/api/thumb" else 1800 if parsed.path == "/api/enhance-original" else 7000
                image, _ = load_image(path)
                image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                data = io.BytesIO()
                image.save(data, format="JPEG", quality=88, optimize=True)
                self.reply(200, data.getvalue(), "image/jpeg")
                return
            if parsed.path == "/api/enhance-preview":
                path = image_path(query.get("path", [""])[0])
                image, _ = load_image(path)
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                enhanced, changes = auto_enhance(image)
                data = io.BytesIO()
                enhanced.save(data, format="JPEG", quality=89, optimize=True)
                self.reply(200, data.getvalue(), "image/jpeg", {"X-Cropper-Adjustments": ", ".join(changes)})
                return
            if parsed.path == "/api/restore-preview":
                path = image_path(query.get("path", [""])[0])
                operation = query.get("operation", [""])[0]
                image, _ = load_image(path)
                restored, changes = restore(image, operation)
                restored.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                data = io.BytesIO()
                restored.save(data, format="JPEG", quality=89, optimize=True)
                self.reply(200, data.getvalue(), "image/jpeg", {"X-Cropper-Adjustments": ", ".join(changes)})
                return
            if parsed.path == "/api/info":
                path = image_path(query.get("path", [""])[0])
                with Image.open(path) as image:
                    width, height = image.size
                    if image.getexif().get(274) in {5, 6, 7, 8}:
                        width, height = height, width
                self.json(200, {"width": width, "height": height})
                return
            self.json(404, {"error": "Not found."})
        except (ValueError, OSError, cv2.error) as exc:
            self.json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        if not self.authorized():
            self.json(403, {"error": "Unauthorized request."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_000_000:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            if self.path == "/api/settings":
                if not isinstance(data, dict):
                    raise ValueError("Invalid settings.")
                key = data.pop("api_key", None)
                if key is not None and str(key).strip():
                    app_settings.set_stored_api_key(str(key))
                app_settings.save_settings(data)
                self.json(200, app_settings.public_settings())
                return
            if self.path == "/api/settings/clear-key":
                app_settings.clear_stored_api_key()
                self.json(200, app_settings.public_settings())
                return
            if self.path == "/api/settings/test-key":
                if not isinstance(data, dict):
                    raise ValueError("Invalid settings.")
                key = str(data.get("api_key") or app_settings.resolved_api_key())
                from chatgpt_restore import test_key
                result = test_key(key)
                self.json(200, result)
                return
            if self.path == '/api/crop-corners':
                path = image_path(data.get('path', ''))
                image, _ = load_image(path)
                target = data.get('target')
                if target is not None and (not isinstance(target, list) or len(target) != 2 or
                                           not all(isinstance(v, (int, float)) and np.isfinite(v) for v in target)):
                    raise ValueError('Invalid photo selection point.')
                self.json(200, predict_corners(image, target))
                return
            if self.path == "/api/crop":
                path = image_path(data.get("path", ""))
                destination, size = save_image(path, data.get("points", []), data.get("settings", {}), int(data.get("rotation", 0)), data.get("aspect_ratio"))
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1], "source_moved": not path.exists()})
                return
            if self.path == "/api/crop-preview":
                path = image_path(data.get("path", ""))
                image, _ = load_image(path)
                original_width, original_height = image.size
                image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
                scaled = [[float(x) * image.width / original_width, float(y) * image.height / original_height] for x, y in data.get("points", [])]
                preview = warp(image, scaled, int(data.get("rotation", 0)), data.get("aspect_ratio"))
                payload = io.BytesIO()
                preview.save(payload, format="JPEG", quality=89, optimize=True)
                self.reply(200, payload.getvalue(), "image/jpeg")
                return
            if self.path == "/api/merge":
                front_path = image_path(data.get("front_path", ""))
                back_path = image_path(data.get("back_path", ""))
                destination, size = save_merged_image(front_path, back_path, data.get("settings", {}), data.get("name_source", "back"), int(data.get("back_rotation", 0)))
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1]})
                return
            if self.path == "/api/enhance":
                path = image_path(data.get("path", ""))
                requested = data.get("settings", {})
                source_paths = [str(path)]
                link = next((item for item in read_links(path.parent) if str(path) in {item['front_path'],item['composite_path']}), None)
                if link: source_paths = [link['front_path'],link['composite_path']]
                destination, size, changes, archived_source = save_enhanced_image(path, requested)
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1], "changes": changes, "archived_path": str(archived_source), "source_paths":source_paths})
                return
            if self.path == "/api/restore-preview":
                path = image_path(data.get("path", ""))
                operation = data.get("operation", "")
                image, _ = load_image(path)
                if data.get("show_marks") and operation == "dust":
                    preview, changes = preview_dust_marks(image, data.get("strokes"))
                else:
                    preview, changes = restore(image, operation, data.get("strokes"))
                preview.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                payload = io.BytesIO()
                preview.save(payload, format="JPEG", quality=89, optimize=True)
                self.reply(200, payload.getvalue(), "image/jpeg", {"X-Cropper-Adjustments": ", ".join(changes),
                                                                   "X-Cropper-Useful": str(useful_change(changes)).lower()})
                return
            if self.path == "/api/restore":
                path = image_path(data.get("path", ""))
                destination, size, changes, archived = save_restored_image(path, data.get("operation", ""), data.get("strokes"))
                self.json(200, {"path": str(destination or path), "name": (destination or path).name,
                                "width": size[0], "height": size[1], "changes": changes,
                                "archived_path": str(archived) if archived else None, "skipped": destination is None})
                return
            if self.path == '/api/colorization-candidates':
                folder=clean_path(data.get('path',''))
                self.json(200, {'items':candidates(folder,read_links(folder))})
                return
            if self.path == '/api/stage-finish':
                requested = data.get('paths', [])
                if not isinstance(requested, list) or not 1 <= len(requested) <= 1000:
                    raise ValueError('Select between 1 and 1000 photos to finish.')
                paths = {image_path(value) for value in requested}
                # Resolve either selected member to its complete approved pair.
                links = []
                for folder in {path.parent for path in paths}:
                    for link in read_links(folder):
                        members = {Path(link['front_path']), Path(link['composite_path'])}
                        if paths & members:
                            paths.update(image_path(str(member)) for member in members)
                            links.append(link)
                pending = [item for folder in {path.parent for path in paths} for item in list_pending(folder)]
                if any(Path(item['front_path']) in paths or Path(item.get('link', {}).get('composite_path', '')) in paths for item in pending):
                    raise ValueError('Review or reject pending results before finishing these photos.')
                planned = [stage_files.plan(path, 'finish') for path in sorted(paths)]
                if data.get('dry_run', True):
                    self.json(200, {'items': [{'source': str(item['source']), 'snapshots': [str(p) for p in item['snapshots']]} for item in planned]})
                    return
                # Each pair is atomic; report partial batch success explicitly.
                remaining = set(paths)
                finished, errors = [], []
                groups = [[Path(link['front_path']), Path(link['composite_path'])] for link in links]
                paired = {path for group in groups for path in group}
                groups += [[path] for path in sorted(paths - paired)]
                for group in groups:
                    if not set(group) <= remaining:
                        continue
                    try:
                        stage_files.commit([stage_files.plan(path, 'finish') for path in group], [None] * len(group))
                        finished.extend(str(path) for path in group)
                    except (ValueError, OSError) as error:
                        errors.append(str(error))
                    remaining.difference_update(group)
                self.json(200, {'finished': finished, 'errors': errors})
                return
            if self.path == '/api/photo-pair-suggestions':
                self.json(200, {'items': suggestions(clean_path(data.get('path', '')))})
                return
            if self.path == '/api/photo-link':
                front = image_path(data.get('front_path', ''))
                composite = image_path(data.get('composite_path', ''))
                link = approve_link(front, composite, data.get('split', 50), data.get('layout', 'right'))
                for pending in list_pending(front.parent):
                    if pending['front_path'] == str(front): attach_link(pending['id'], link)
                self.json(200, link)
                return
            if self.path == "/api/chatgpt-generate":
                operation=data.get('operation','restore')
                if operation not in {'restore','colorize'}: raise ValueError('Invalid image operation.')
                front = image_path(data.get("front_path", ""))
                stage_files.plan(front, operation)  # Fail before paying for a request.
                links = read_links(front.parent)
                if any(item['composite_path'] == str(front) for item in links):
                    raise ValueError('This is a linked combined image. Select its standalone front or use Generate selected pairs.')
                link = next((item for item in links if item['front_path'] == str(front)), None)
                composite = image_path(link['composite_path']) if link else None
                if composite is not None:
                    stage_files.plan(composite, operation)
                if any(item["front_path"] == str(front) for item in list_pending(front.parent)):
                    raise ValueError("This photo already has a pending AI result. Review it before generating another.")
                plans = stage_files.prepare_ai_plans(front, operation, composite)
                reserved = [item['output'] for item in plans if item.get('output') is not None]
                try:
                    front_png = edit_front(front, operation=operation)
                except Exception as exc:
                    stage_files.release_outputs(reserved)
                    self.json(*image_api_error(exc))
                    return
                try:
                    staged = save_pending(front, front_png, stage_routing=True, operation=operation,
                                          output_paths=reserved)
                    if link:
                        staged = attach_link(staged['id'], link)
                except Exception:
                    stage_files.release_outputs(reserved)
                    raise
                self.json(200, staged)
                return
            if self.path == '/api/chatgpt-resubmit':
                comment = data.get('comment')
                if not isinstance(comment, str) or not comment.strip() or len(comment) > 2000:
                    raise ValueError('Enter a correction of 1 to 2000 characters.')
                include_previous = data.get('include_previous', False)
                if not isinstance(include_previous, bool):
                    raise ValueError('Invalid previous-image option.')
                record, _ = get_pending(data.get('id', ''))
                front = image_path(record['front_path'])
                with review_lock(front):
                    record, previous = get_pending(record['id'])
                    operation = record.get('operation', 'restore')
                    if operation not in {'restore', 'colorize'}:
                        raise ValueError('Invalid image operation.')
                    stage_files.plan(front, operation)
                    if record.get('link'):
                        composite = image_path(record['link']['composite_path'])
                        stage_files.plan(composite, operation)
                    comments = [*record.get('comments', []), comment.strip()]
                    if len(comments) > 20 or sum(map(len, comments)) > 12000:
                        raise ValueError('Correction history is too long. Reject these attempts and start again.')
                    try:
                        result = edit_front(front, operation=operation, comments=comments,
                                            previous=previous if include_previous else None)
                    except Exception as exc:
                        self.json(*image_api_error(exc))
                        return
                    revision = {'operation': operation, 'parent_id': record['id'],
                                'attempt': record.get('attempt', 1) + 1, 'comments': comments,
                                'included_previous': include_previous}
                    if record.get('link'): revision['link'] = record['link']
                    if record.get('output_path'): revision['output_path'] = record['output_path']
                    if record.get('output_paths'): revision['output_paths'] = record['output_paths']
                    staged = save_pending(front, result, stage_routing=True, operation=operation, revision=revision)
                    self.json(200, staged)
                return
            if self.path in {"/api/chatgpt-accept", "/api/chatgpt-reject"}:
                stage_id = data.get("id", "")
                record, image = get_pending(stage_id)
                with review_lock(Path(record['front_path'])):
                    record, image = get_pending(stage_id)
                    outputs = accept_linked(record, image.read_bytes()) if self.path.endswith("accept") else []
                    removed = [stage_id]
                    if outputs:
                        # Only one attempt can be filed for a source; discard unchosen variants after success.
                        removed = [item['id'] for item in list_pending(Path(record['front_path']).parent)
                                   if item['front_path'] == record['front_path']]
                    held = []
                    for ident in removed:
                        try:
                            held.extend(reserved_from_record(get_pending(ident)[0]))
                        except (ValueError, OSError, json.JSONDecodeError):
                            pass
                        remove_pending(ident)
                    stage_files.release_outputs(held)
                    self.json(200, {"paths": [str(path) for path in outputs], 'removed_ids': removed})
                return
            if self.path == "/api/chatgpt-recovered-keep":
                self.json(200, keep_recovered(data.get("id", "")))
                return
            if self.path == "/api/chatgpt-recovered-discard":
                discard_recovered(data.get("id", ""))
                self.json(200, {"removed": True})
                return
            self.json(404, {"error": "Not found."})
        except PermissionError as exc:
            self.json(403, {"code": "permission_denied", "error": "Cropper cannot write to this location. Choose another output folder or check this folder's permissions.", "path": exc.filename})
        except (ValueError, OSError, json.JSONDecodeError, cv2.error) as exc:
            self.json(400, {"error": str(exc)})


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can permit two processes to listen on the same port.
    allow_reuse_address = False


def resolve_port(cli_port: int | None) -> int:
    """CLI --port wins; otherwise CROPPER_PORT; otherwise 8765."""
    if cli_port is not None:
        return int(cli_port)
    raw = os.environ.get("CROPPER_PORT", "").strip()
    if not raw:
        return 8765
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("CROPPER_PORT must be an integer port.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cropper locally")
    parser.add_argument("--port", type=int, default=None, help="Port (default: CROPPER_PORT or 8765)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", default=None, help="Per-user data folder (default: %%APPDATA%%/Cropper)")
    args = parser.parse_args()
    app_settings.configure_runtime(args.data_dir)
    port = resolve_port(args.port)
    try:
        server = LocalServer(("127.0.0.1", port), Handler)
    except OSError:
        address = f"http://127.0.0.1:{port}"
        try:
            with urlopen(address + "/api/session", timeout=1) as response:
                active = json.load(response).get("app") == "Cropper"
        except (OSError, ValueError):
            active = False
        if active:
            print(f"Cropper is already running at {address}", flush=True)
            if not args.no_browser:
                webbrowser.open(address)
            return
        raise
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"Cropper is running at {address}", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
