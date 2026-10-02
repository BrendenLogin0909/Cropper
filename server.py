"""Cropper: a local-only image browser and perspective crop service."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
from PIL import Image, ImageOps
from enhance import auto_enhance
from restore import OPERATIONS, preview_dust_marks, restore


HERE = Path(__file__).resolve().parent
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
TOKEN = secrets.token_urlsafe(32)
RESTORE_LOCK = threading.Lock()


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


def choose_output(source: Path, settings: dict) -> Path:
    mode = settings.get("mode")
    if mode == "replace":
        return source
    prefix = str(settings.get("prefix", ""))
    suffix = str(settings.get("suffix", ""))
    if mode == "beside":
        if not prefix and not suffix:
            raise ValueError("Add a prefix or suffix for copies saved beside the original.")
        folder = source.parent
    elif mode == "subfolder":
        name = str(settings.get("subfolder", "Cropped")).strip()
        if not name or name in {".", ".."} or any(ch in name for ch in '\\/:*?"<>|'):
            raise ValueError("Enter a valid subfolder name.")
        folder = source.parent / name
    elif mode == "folder":
        folder = clean_path(settings.get("folder", ""))
        if not folder.is_dir():
            raise ValueError("Choose an existing output folder.")
    else:
        raise ValueError("Choose an output setting before saving.")
    filename = f"{prefix}{source.stem}{suffix}{source.suffix}"
    if not filename or any(ch in filename for ch in '\\/:*?"<>|'):
        raise ValueError("The output filename contains an invalid character.")
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / filename
    if candidate.resolve() == source.resolve() or candidate.exists():
        for index in range(2, 10000):
            candidate = folder / f"{prefix}{source.stem}{suffix}_{index}{source.suffix}"
            if not candidate.exists() and candidate.resolve() != source.resolve():
                break
        else:
            raise ValueError("Could not find a free output filename.")
    return candidate


def save_image(source: Path, points: list[list[float]], settings: dict, rotation: int, aspect_ratio: float | None = None) -> tuple[Path, tuple[int, int]]:
    image, info = load_image(source)
    cropped = warp(image, points, rotation, aspect_ratio)
    destination = choose_output(source, settings)
    save_rendered(cropped, destination, info)
    return destination, cropped.size


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
    destination = choose_output(source_for_name, settings)
    save_rendered(merged, destination, front_info)
    return destination, merged.size


def archive_enhancement_source(source: Path) -> Path:
    """Move a successfully enhanced source aside, keeping all originals recoverable."""
    if source.parent.name.casefold() == "pre-enhancement":
        return source
    archive_folder = source.parent / "Pre-Enhancement"
    archive_folder.mkdir(parents=True, exist_ok=True)
    destination = archive_folder / source.name
    if destination.exists():
        for index in range(2, 10000):
            candidate = archive_folder / f"{source.stem}_{index}{source.suffix}"
            if not candidate.exists():
                destination = candidate
                break
        else:
            raise OSError("Could not find a free name in Pre-Enhancement.")
    os.replace(source, destination)
    return destination


def save_enhanced_image(source: Path, settings: dict) -> tuple[Path, tuple[int, int], list[str], Path]:
    image, info = load_image(source)
    enhanced, changes = auto_enhance(image)
    destination = choose_output(source, settings)
    save_rendered(enhanced, destination, info)
    archived_source = archive_enhancement_source(source)
    return destination, enhanced.size, changes, archived_source


def save_restored_image(source: Path, operation: str, strokes: list | None = None) -> tuple[Path, tuple[int, int], list[str], Path]:
    if operation not in OPERATIONS:
        raise ValueError("Choose a valid restoration tab.")
    output_name, archive_name = OPERATIONS[operation]
    with RESTORE_LOCK:
        image, info = load_image(source)
        restored, changes = restore(image, operation, strokes)
        destination = choose_output(source, {"mode": "subfolder", "subfolder": output_name})
        save_rendered(restored, destination, info)
        try:
            archive_folder = source.parent / archive_name
            archive_folder.mkdir(parents=True, exist_ok=True)
            archived = archive_folder / source.name
            if archived.exists():
                for index in range(2, 10000):
                    candidate = archive_folder / f"{source.stem}_{index}{source.suffix}"
                    if not candidate.exists():
                        archived = candidate
                        break
                else:
                    raise OSError(f"Could not find a free name in {archive_name}.")
            os.replace(source, archived)
        except OSError:
            # A failed archive must leave the source in place and no apparent
            # completed result in the output folder.
            destination.unlink(missing_ok=True)
            raise
    return destination, restored.size, changes, archived


class Handler(BaseHTTPRequestHandler):
    server_version = "Cropper/1.0"

    def log_message(self, format: str, *args) -> None:
        print("%s - %s" % (self.address_string(), format % args))

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
        return secrets.compare_digest(given, TOKEN) and (not origin or origin == f"http://{host}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/session":
                self.json(200, {"app": "Cropper", "token": TOKEN, "home": str(Path.home())})
                return
            if parsed.path in {"/", "/index.html", "/app.css", "/app.js"}:
                name = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                file = HERE / name
                self.reply(200, file.read_bytes(), MIME[file.suffix] + "; charset=utf-8")
                return
            if not self.authorized(query):
                self.json(403, {"error": "Unauthorized request."})
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
            if self.path == "/api/crop":
                path = image_path(data.get("path", ""))
                destination, size = save_image(path, data.get("points", []), data.get("settings", {}), int(data.get("rotation", 0)), data.get("aspect_ratio"))
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1]})
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
                if requested.get("mode") == "replace":
                    raise ValueError("Auto improve saves a copy so you can compare it with the original.")
                destination, size, changes, archived_source = save_enhanced_image(path, requested)
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1], "changes": changes, "archived_path": str(archived_source)})
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
                self.reply(200, payload.getvalue(), "image/jpeg", {"X-Cropper-Adjustments": ", ".join(changes)})
                return
            if self.path == "/api/restore":
                path = image_path(data.get("path", ""))
                destination, size, changes, archived = save_restored_image(path, data.get("operation", ""), data.get("strokes"))
                self.json(200, {"path": str(destination), "name": destination.name, "width": size[0], "height": size[1], "changes": changes, "archived_path": str(archived)})
                return
            self.json(404, {"error": "Not found."})
        except PermissionError as exc:
            self.json(403, {"code": "permission_denied", "error": "Cropper cannot write to this location. Choose another output folder or check this folder's permissions.", "path": exc.filename})
        except (ValueError, OSError, json.JSONDecodeError, cv2.error) as exc:
            self.json(400, {"error": str(exc)})


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can permit two processes to listen on the same port.
    allow_reuse_address = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cropper locally")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError:
        address = f"http://127.0.0.1:{args.port}"
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
