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


def _nearly_monochrome(rgb: np.ndarray) -> bool:
    sample = rgb[:: max(1, rgb.shape[0] // 600), :: max(1, rgb.shape[1] // 600)].astype(np.int16)
    return float(np.percentile(np.max(sample, axis=2) - np.min(sample, axis=2), 95)) <= 12


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

    # Smooth, faded monochrome prints can hold many pale flecks missed by a
    # small median filter. Limit this wider search to low-colour, low-contrast
    # prints; on other scenes it catches real facial and fabric detail.
    height, width = lightness.shape
    interior = lightness[height // 20: height - height // 20,
                         width // 20: width - width // 20]
    dark, bright = np.percentile(interior[::4, ::4], (5, 95))
    if _nearly_monochrome(rgb) and dark > 55 and bright - dark < 150:
        gray = _gray(rgb)
        opened = cv2.morphologyEx(gray, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        pale_marks = cv2.subtract(gray, opened)
        smooth = cv2.GaussianBlur(gray, (0, 0), 5)
        gradient_x = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
        candidates = ((pale_marks > 24) & (np.hypot(gradient_x, gradient_y) < 6)).astype(np.uint8)
        candidates[:4] = 0; candidates[-4:] = 0
        candidates[:, :4] = 0; candidates[:, -4:] = 0
        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidates, 8)
        for label in range(1, count):
            x, y, mark_width, mark_height, area = stats[label]
            if 3 <= area <= 600 and min(mark_width, mark_height) <= 7 and max(mark_width, mark_height) <= 130:
                region = mask[y:y + mark_height, x:x + mark_width]
                region[labels[y:y + mark_height, x:x + mark_width] == label] = 255
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
    amount = int(np.count_nonzero(mask))
    if amount < max(8, round(mask.size * 0.00015)) and not strokes:
        return image.copy(), ["No useful automatic dust repair found; mark a visible scratch if needed"]
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
    if min(rgb.shape[:2]) < 16:
        return image.copy(), ["No useful change: image is too small for safe detail correction"]
    gray = _gray(rgb)
    noise = _noise_level(gray)
    if noise >= 7.0:
        return image.copy(), ["No safe detail correction: this photo has strong grain"]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lightness = lab[:, :, 0].astype(np.float32)
    height, width = lightness.shape
    interior = lightness[height // 20: height - height // 20,
                         width // 20: width - width // 20]
    dark, middle, bright = np.percentile(interior[::4, ::4], (5, 50, 95))
    tonal_width = bright - dark
    fade_strength = float(np.clip((180 - tonal_width) / 80, 0, 1)
                          * np.clip((dark - 45) / 80, 0, 1) * 0.65)
    if bright >= 245:
        fade_strength = 0.0

    scale = min(1.0, 1200 / max(width, height))
    sample = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))))
    softened_sample = cv2.GaussianBlur(sample, (0, 0), 1)
    edge_variance = float(cv2.Laplacian(softened_sample, cv2.CV_32F).var())
    gradient_x = cv2.Sobel(softened_sample, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(softened_sample, cv2.CV_32F, 0, 1, ksize=3)
    strong_edge = float(np.percentile(np.hypot(gradient_x, gradient_y), 99))
    if fade_strength < 0.15 and (edge_variance >= 25 or strong_edge >= 180):
        return image.copy(), ["No useful change: this photo already has clear tones and edges"]

    changes = []
    if fade_strength >= 0.15 and dark + 5 < middle < bright - 5:
        anchors = np.array([0, dark, middle, bright, 255], dtype=np.float32)
        targets = np.array([0, 35, 140, 230, 255], dtype=np.float32)
        curve = np.interp(np.arange(256), anchors, targets)
        curve = np.clip((1 - fade_strength) * np.arange(256) + fade_strength * curve, 0, 255).astype(np.uint8)
        lightness = cv2.LUT(lightness.astype(np.uint8), curve).astype(np.float32)
        changes.append("Faded tonal range recovered")

    blur = cv2.GaussianBlur(lightness, (0, 0), 1.2)
    detail = lightness - blur
    threshold = max(2.0, noise * 1.3)
    visible = np.abs(detail) > threshold
    if visible.mean() < 0.006 and not changes:
        return image.copy(), ["No useful change: too little recoverable edge detail"]
    amount = 0.35 if visible.mean() > 0.12 else 0.75
    adjustment = np.clip(detail * amount, -13.0, 13.0)
    taper = np.minimum(lightness / 28.0, (255.0 - lightness) / 28.0)
    new_lightness = np.clip(lightness + adjustment * visible * np.clip(taper, 0, 1), 0, 255).astype(np.uint8)
    lab[:, :, 0] = new_lightness
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    if visible.mean() >= 0.006:
        changes.append("Existing edges clarified")
    return Image.fromarray(result), changes


def useful_change(changes: list[str]) -> bool:
    return bool(changes) and not changes[0].startswith("No ")


def restore(image: Image.Image, operation: str, strokes: list | None = None) -> tuple[Image.Image, list[str]]:
    if operation == "dust":
        return repair_dust(image, strokes)
    if operation == "noise":
        return reduce_noise(image)
    if operation == "sharpen":
        return sharpen_detail(image)
    raise ValueError("Choose Dust repair, Noise reduction, or Sharpening.")
