"""Bounded, local automatic correction for faded print photographs.

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
    """Balance whites and shadows, recover faded tones, and gently lift color."""
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
    neutral_reference = None

    if likely_neutral.sum() >= max(120, sample.shape[0] * 0.008):
        # A white garment or paper gives the light end of the colour cast.
        reference = np.median(sampled_lab.reshape(-1, 3)[likely_neutral, 1:], axis=0)
        neutral_reference = reference
        light_shifts = np.clip((128.0 - reference) * 0.78, -18.0, 18.0)

        # Faded prints often have red/brown blacks even after whites are fixed.
        # Estimate those separately, using the least colourful darker pixels so
        # a bright red carpet or similarly coloured object has less influence.
        sample_lightness = sampled_lab[:, :, 0]
        dark_low, dark_high = np.percentile(sample_lightness, [5, 20])
        darker = (sample_lightness >= dark_low) & (sample_lightness <= dark_high)
        chroma = np.linalg.norm(sampled_lab[:, :, 1:].astype(np.float32) - 128.0, axis=2)
        darker &= chroma <= np.percentile(chroma[darker], 60)
        dark_shifts = np.zeros(2, dtype=np.float32)
        if darker.sum() >= max(120, sample.shape[0] * 0.008):
            dark_reference = np.median(sampled_lab[darker, 1:], axis=0)
            dark_shifts[0] = np.clip((128.0 - dark_reference[0]) * 0.75, -20.0, 20.0)
            dark_shifts[1] = np.clip((128.0 - dark_reference[1]) * 0.35, -8.0, 8.0)

        if max(float(np.max(np.abs(light_shifts))), float(np.max(np.abs(dark_shifts)))) >= 3.0:
            fade_start, fade_end = np.percentile(sample_lightness, [20, 85])
            weight = np.clip((lab[:, :, 0].astype(np.float32) - fade_start) / max(fade_end - fade_start, 1), 0, 1)
            for channel, dark_shift, light_shift in ((1, dark_shifts[0], light_shifts[0]), (2, dark_shifts[1], light_shifts[1])):
                shift = dark_shift + (light_shift - dark_shift) * weight
                lab[:, :, channel] = np.clip(lab[:, :, channel].astype(np.float32) + shift, 0, 255).astype(np.uint8)
            changes.append("Shadows and whites balanced" if float(np.max(np.abs(dark_shifts - light_shifts))) >= 5 else "Color cast reduced")

    low, high = np.percentile(sampled_lab[:, :, 0], [2, 98])
    span = max(float(high - low), 1.0)
    slope = min(1.4, 195.0 / span)
    neutral_highlights = neutral_reference is not None and likely_neutral.mean() >= 0.04 and float(np.linalg.norm(neutral_reference - 128.0)) <= 20
    if neutral_highlights and 32 <= low <= 80 and 170 <= high <= 220 and 90 <= span <= 185:
        # A faded print can have grey blacks and dull whites. Stretch its measured
        # range while keeping a soft toe and shoulder so texture is not clipped.
        values = np.arange(256, dtype=np.float32)
        tone = 12.0 + (values - low) * (230.0 / span)
        tone = np.where(values < low, values * (12.0 / low), tone)
        tone = np.where(values > high, 242.0 + (values - high) * (13.0 / (255.0 - high)), tone)
        lab[:, :, 0] = cv2.LUT(lab[:, :, 0], np.clip(tone, 0, 255).astype(np.uint8))
        changes.append("Faded blacks and whites restored")
    elif span >= 20.0 and slope > 1.07:
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

    # Old colour prints can retain a warm veil after their whites are balanced.
    # Compress excess yellow and red chroma without moving nearly neutral whites.
    # The cast estimate controls strength separately for each photograph.
    if neutral_reference is not None and neutral_reference[1] >= 138 and float(np.median(sampled_lab[:, :, 2])) >= 145:
        corrected_lab = cv2.cvtColor(toned, cv2.COLOR_RGB2LAB)
        yellow = np.arange(256, dtype=np.float32)
        yellow_strength = float(np.clip((neutral_reference[1] - 134.0) * 0.07, 0.25, 0.6))
        yellow -= np.minimum(np.maximum(yellow - 133.0, 0.0) * yellow_strength, 10.0)
        corrected_lab[:, :, 2] = cv2.LUT(corrected_lab[:, :, 2], yellow.astype(np.uint8))
        red_strength = float(np.clip((neutral_reference[0] - 129.0) * 0.18, 0.0, 0.35))
        if red_strength:
            red = np.arange(256, dtype=np.float32)
            red -= np.minimum(np.maximum(red - 133.0, 0.0) * red_strength, 5.0)
            corrected_lab[:, :, 1] = cv2.LUT(corrected_lab[:, :, 1], red.astype(np.uint8))
        toned = cv2.cvtColor(corrected_lab, cv2.COLOR_LAB2RGB)
        changes.append("Residual warm cast softened")

    if not changes:
        changes.append("No safe adjustment detected")
        return image.copy(), changes
    return Image.fromarray(toned), changes
