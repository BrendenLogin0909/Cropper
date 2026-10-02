"""Conservative, local repair steps for digitized photographs.

Each operation analyses its own input and either makes a bounded adjustment or
returns an unchanged copy. None of these operations creates missing detail.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


OPERATIONS = {
    "dust": ("Dust Repaired", "Before Dust Repair"),
    "noise": ("Noise Reduced", "Before Noise Reduction"),
    "sharpen": ("Sharpened", "Before Sharpening"),
}


def _rgb(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"))


def _gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _monochrome(rgb: np.ndarray) -> bool:
    sample = rgb[:: max(1, rgb.shape[0] // 600), :: max(1, rgb.shape[1] // 600)].astype(np.int16)
    return float(np.percentile(np.max(sample, axis=2) - np.min(sample, axis=2), 95)) <= 3


def _noise_level(gray: np.ndarray) -> float:
    # Work at native pixel scale: resizing first would average away scan noise.
    height, width = gray.shape
    patch = gray[max(0, (height - 900) // 2): min(height, (height + 900) // 2),
                 max(0, (width - 900) // 2): min(width, (width + 900) // 2)]
    if min(patch.shape) < 5:
        return 0.0
    background = cv2.GaussianBlur(patch, (0, 0), 2)
    gradient_x = cv2.Sobel(background, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(background, cv2.CV_32F, 0, 1, ksize=3)
    smooth = (np.abs(gradient_x) + np.abs(gradient_y) < 28) & (patch > 20) & (patch < 240)
    if smooth.mean() < 0.08:
        return 0.0
    residual = np.abs(patch.astype(np.int16) - cv2.medianBlur(patch, 3).astype(np.int16))
    return float(np.percentile(residual[smooth], 75))


def _dust_mask(rgb: np.ndarray) -> np.ndarray:
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[:, :, 0]
    if min(lightness.shape) < 24:
        return np.zeros(lightness.shape, dtype=np.uint8)
    baseline = cv2.medianBlur(lightness, 7)
    residual = np.abs(lightness.astype(np.int16) - baseline.astype(np.int16))
    threshold = max(42, round(_noise_level(_gray(rgb)) * 7))
    candidate = (residual >= threshold).astype(np.uint8)

    # Genuine edges and textured areas are not safe to infer from neighbours.
    surrounding = cv2.morphologyEx(baseline, cv2.MORPH_GRADIENT, np.ones((7, 7), np.uint8))
    candidate[surrounding > 35] = 0
    candidate[:4] = 0; candidate[-4:] = 0; candidate[:, :4] = 0; candidate[:, -4:] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    maximum_area = max(24, min(240, round(lightness.size / 25000)))
    mask = np.zeros_like(candidate)
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if 2 <= area <= maximum_area and max(width, height) <= max(25, min(lightness.shape) // 25):
            region = mask[y:y + height, x:x + width]
            region[labels[y:y + height, x:x + width] == label] = 255
    if np.count_nonzero(mask) > lightness.size * 0.006:
        return np.zeros_like(mask)
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)


def _marked_scratch_mask(shape: tuple[int, int], strokes: list | None) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    if strokes is None:
        return mask
    if not isinstance(strokes, list) or len(strokes) > 100:
        raise ValueError("Too many marked scratches.")
    if sum(len(stroke) if isinstance(stroke, list) else 4001 for stroke in strokes) > 4000:
        raise ValueError("Too many marked scratch points.")
    height, width = shape
    thickness = max(3, round(max(height, width) * 0.004))
    for stroke in strokes:
        if not isinstance(stroke, list) or not stroke:
            raise ValueError("A marked scratch is invalid.")
        points = []
        for point in stroke:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("A marked scratch point is invalid.")
            x, y = float(point[0]), float(point[1])
            if not np.isfinite([x, y]).all() or not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("A marked scratch point lies outside the image.")
            points.append((round(x * (width - 1)), round(y * (height - 1))))
        cv2.circle(mask, points[0], thickness // 2, 255, -1)
        for start, end in zip(points, points[1:]):
            cv2.line(mask, start, end, 255, thickness)
    return mask


def _combined_dust_mask(rgb: np.ndarray, strokes: list | None) -> np.ndarray:
    return cv2.bitwise_or(_dust_mask(rgb), _marked_scratch_mask(rgb.shape[:2], strokes))


def preview_dust_marks(image: Image.Image, strokes: list | None = None) -> tuple[Image.Image, list[str]]:
    rgb = _rgb(image)
    mask = _combined_dust_mask(rgb, strokes)
    if not np.any(mask):
        return image.copy(), ["No repair marks detected"]
    marker = cv2.dilate(mask, np.ones((max(5, round(max(rgb.shape[:2]) / 300)),) * 2, np.uint8))
    overlay = rgb.copy()
    overlay[marker > 0] = (235, 48, 53)
    return Image.fromarray(overlay), ["Red marks show where repair will be applied"]


def repair_dust(image: Image.Image, strokes: list | None = None) -> tuple[Image.Image, list[str]]:
    rgb = _rgb(image)
    mask = _combined_dust_mask(rgb, strokes)
    if not np.any(mask):
        return image.copy(), ["No high-confidence dust or short scratches detected"]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    repaired = cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)
    label = "Detected dust and marked scratches repaired" if strokes else "Isolated dust and short scratches repaired"
    return Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)), [label]


def reduce_noise(image: Image.Image) -> tuple[Image.Image, list[str]]:
    rgb = _rgb(image)
    noise = _noise_level(_gray(rgb))
    if noise < 4.5:
        return image.copy(), ["No significant fine noise detected"]
    strength = float(np.clip(noise * 0.85 + 1.5, 4.0, 8.0))
    if _monochrome(rgb):
        gray = cv2.fastNlMeansDenoising(_gray(rgb), h=strength, templateWindowSize=7, searchWindowSize=21)
        return Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)), ["Fine monochrome grain reduced"]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    reduced = cv2.fastNlMeansDenoisingColored(bgr, h=strength, hColor=min(9.0, strength * 1.2), templateWindowSize=7, searchWindowSize=21)
    return Image.fromarray(cv2.cvtColor(reduced, cv2.COLOR_BGR2RGB)), ["Fine grain and color speckles reduced"]


def sharpen_detail(image: Image.Image) -> tuple[Image.Image, list[str]]:
    rgb = _rgb(image)
    gray = _gray(rgb)
    noise = _noise_level(gray)
    if noise >= 7.0:
        return image.copy(), ["Noise is too strong for safe automatic sharpening"]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lightness = lab[:, :, 0].astype(np.float32)
    blur = cv2.GaussianBlur(lightness, (0, 0), 1.2)
    detail = lightness - blur
    threshold = max(2.5, noise * 1.3)
    visible = np.abs(detail) > threshold
    if visible.mean() < 0.006:
        return image.copy(), ["No recoverable edge detail detected"]
    amount = 0.26 if visible.mean() > 0.12 else 0.43
    adjustment = np.clip(detail * amount, -9.0, 9.0)
    taper = np.minimum(lightness / 28.0, (255.0 - lightness) / 28.0)
    new_lightness = np.clip(lightness + adjustment * visible * np.clip(taper, 0, 1), 0, 255).astype(np.uint8)
    lab[:, :, 0] = new_lightness
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return Image.fromarray(result), ["Existing edges gently sharpened"]


def restore(image: Image.Image, operation: str, strokes: list | None = None) -> tuple[Image.Image, list[str]]:
    if operation == "dust":
        return repair_dust(image, strokes)
    if operation == "noise":
        return reduce_noise(image)
    if operation == "sharpen":
        return sharpen_detail(image)
    raise ValueError("Choose Dust repair, Noise reduction, or Sharpening.")
