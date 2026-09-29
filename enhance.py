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
    sample = _sample(rgb).reshape(-1, 3).astype(np.float32)
    brightness = sample.max(axis=1)
    saturation = (brightness - sample.min(axis=1)) / np.maximum(brightness, 1)
    bright_cut = max(75.0, float(np.percentile(brightness, 67)))
    likely_neutral = (brightness >= bright_cut) & (brightness < 251) & (saturation < 0.34)
    gains = np.ones(3, dtype=np.float32)
    changes: list[str] = []

    if likely_neutral.sum() >= max(120, sample.shape[0] * 0.008):
        reference = np.median(sample[likely_neutral], axis=0)
        target = float(reference.mean())
        suggested = np.clip(target / np.maximum(reference, 1), 0.78, 1.32)
        if float(np.max(suggested) - np.min(suggested)) > 0.045:
            gains = 1 + (suggested - 1) * 0.88
            changes.append("Color cast reduced")

    channel_tables = [np.clip(np.arange(256, dtype=np.float32) * gain, 0, 255).astype(np.uint8) for gain in gains]
    balanced = cv2.merge([cv2.LUT(rgb[:, :, channel], channel_tables[channel]) for channel in range(3)])

    balanced_sample = _sample(balanced)
    lab_sample = cv2.cvtColor(balanced_sample, cv2.COLOR_RGB2LAB)
    low, high = np.percentile(lab_sample[:, :, 0], [2, 98])
    span = max(float(high - low), 1.0)
    slope = min(1.42, 230.0 / span)
    lab = cv2.cvtColor(balanced, cv2.COLOR_RGB2LAB)
    if slope > 1.055:
        # Expand around the measured middle so faded prints are not made dark.
        offset = 127.5 - ((float(low) + float(high)) / 2) * slope
        original_l = np.arange(256, dtype=np.float32)
        stretched = np.clip(original_l * slope + offset, 0, 255)
        tone_table = np.clip(original_l * 0.16 + stretched * 0.84, 0, 255).astype(np.uint8)
        lab[:, :, 0] = cv2.LUT(lab[:, :, 0], tone_table)
        changes.append("Contrast restored")
    toned = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    hsv_sample = cv2.cvtColor(_sample(toned), cv2.COLOR_RGB2HSV)
    colored = hsv_sample[:, :, 1] > 25
    if colored.mean() > 0.08 and float(np.median(hsv_sample[:, :, 1][colored])) < 115:
        hsv = cv2.cvtColor(toned, cv2.COLOR_RGB2HSV)
        boost = np.clip(np.arange(256, dtype=np.float32) * 1.10, 0, 255).astype(np.uint8)
        hsv[:, :, 1] = cv2.LUT(hsv[:, :, 1], boost)
        toned = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        changes.append("Faded color lifted")

    if not changes:
        changes.append("Already balanced")
    return Image.fromarray(toned), changes
