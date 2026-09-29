"""Conservative, local automatic correction for faded print photographs.

The corrections are deterministic pixel adjustments. They do not infer or
invent missing scene content, so a preview remains important for difficult
casts and photographs with intentionally warm lighting.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _sample(rgb: np.ndarray, limit: int = 900) -> np.ndarray:
    height, width = rgb.shape[:2]
    scale = min(1.0, limit / max(height, width))
    if scale == 1.0:
        return rgb
    return cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


def auto_enhance(image: Image.Image) -> tuple[Image.Image, list[str]]:
    """Balance likely neutral areas, recover tonal range, and gently lift color."""
    rgb = np.asarray(image.convert("RGB"))
    sampled_rgb = _sample(rgb)
    sample = sampled_rgb.reshape(-1, 3).astype(np.float32)
    brightness = sample.max(axis=1)
    saturation = (brightness - sample.min(axis=1)) / np.maximum(brightness, 1)
    bright_cut = max(75.0, float(np.percentile(brightness, 67)))
    likely_neutral = (brightness >= bright_cut) & (brightness < 251) & (saturation < 0.34)
    sampled_lab = cv2.cvtColor(sampled_rgb, cv2.COLOR_RGB2LAB)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    changes: list[str] = []

    if likely_neutral.sum() >= max(120, sample.shape[0] * 0.008):
        # Move a plausible paper white/grey toward neutral without changing lightness.
        reference = np.median(sampled_lab.reshape(-1, 3)[likely_neutral, 1:], axis=0)
        shifts = np.clip((128.0 - reference) * 0.78, -18.0, 18.0)
        if float(np.max(np.abs(shifts))) >= 3.0:
            for channel, shift in ((1, shifts[0]), (2, shifts[1])):
                table = np.clip(np.arange(256, dtype=np.float32) + shift, 0, 255).astype(np.uint8)
                lab[:, :, channel] = cv2.LUT(lab[:, :, channel], table)
            changes.append("Color cast reduced")

    low, high = np.percentile(sampled_lab[:, :, 0], [2, 98])
    span = max(float(high - low), 1.0)
    slope = min(1.4, 195.0 / span)
    if span >= 20.0 and slope > 1.07:
        # Pivot at this photo's midpoint. Ease toward black and white, and never
        # lift a highlight by more than six lightness levels.
        pivot = float(np.median(sampled_lab[:, :, 0]))
        values = np.arange(256, dtype=np.float32)
        distance = values - pivot
        taper = np.where(distance >= 0, (255.0 - values) / max(255.0 - pivot, 1.0), values / max(pivot, 1.0))
        delta = distance * (slope - 1.0) * np.clip(taper, 0, 1)
        delta = np.where(delta > 0, np.minimum(delta, 6.0), delta)
        tone_table = np.clip(values + delta, 0, 255).astype(np.uint8)
        lab[:, :, 0] = cv2.LUT(lab[:, :, 0], tone_table)
        changes.append("Contrast refined; highlights protected")
    toned = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    hsv_sample = cv2.cvtColor(_sample(toned), cv2.COLOR_RGB2HSV)
    colored = hsv_sample[:, :, 1] > 25
    if colored.mean() > 0.08 and float(np.median(hsv_sample[:, :, 1][colored])) < 115:
        hsv = cv2.cvtColor(toned, cv2.COLOR_RGB2HSV)
        boost = np.clip(np.arange(256, dtype=np.float32) * 1.08, 0, 255).astype(np.uint8)
        hsv[:, :, 1] = cv2.LUT(hsv[:, :, 1], boost)
        toned = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        changes.append("Faded color lifted")

    if not changes:
        changes.append("No safe adjustment detected")
        return image.copy(), changes
    return Image.fromarray(toned), changes
