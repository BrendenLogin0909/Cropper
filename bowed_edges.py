"""Follow bowed print edges after the four-corner homography.

Detection runs on a downscaled working copy. A quadratic is fitted per edge.
A good fit is applied with a Coons patch remap; a poor fit falls back to a
capped constant trim. Straight edges skip the remap so the output matches the
homography-only warp.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
from PIL import Image

DETECT_MAX = 800
STRIP_FRAC = 0.16
MIN_SAMPLES = 12
# Working-copy pixels (long side is at most DETECT_MAX, 800). Not full-res.
STRAIGHT_PX = 1.25
SAFETY_FRAC = 0.12
TRIM_CAP_FRAC = 0.025
POOR_RESIDUAL_PX = 2.5
POOR_RESIDUAL_RATIO = 0.2
WEAK_GRAD = 10.0


def enabled(settings, extra=None) -> bool:
    """Default ON. Explicit false in the request or settings turns it off."""
    if isinstance(extra, dict):
        if 'straighten_bowed_edges' in extra:
            return bool(extra['straighten_bowed_edges'])
        if 'straightenBowedEdges' in extra:
            return bool(extra['straightenBowedEdges'])
    if isinstance(settings, dict):
        if 'straightenBowedEdges' in settings:
            return bool(settings['straightenBowedEdges'])
        if 'straighten_bowed_edges' in settings:
            return bool(settings['straighten_bowed_edges'])
    return True


def _working_copy(gray: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = gray.shape[:2]
    longest = max(width, height)
    if longest <= DETECT_MAX:
        return gray, 1.0
    scale = DETECT_MAX / longest
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA), scale


def _first_inward_hit(strip: np.ndarray) -> np.ndarray:
    """First strong gradient from the outer border, or 0 if none."""
    if strip.shape[0] < 3:
        return np.zeros(strip.shape[1], dtype=np.float64)
    blurred = cv2.GaussianBlur(strip, (0, 0), sigmaX=1.1, sigmaY=0.7)
    mag = np.abs(np.diff(blurred, axis=0))
    col_std = strip.std(axis=0)
    thresh = np.maximum(WEAK_GRAD, 0.4 * col_std)
    hits = mag >= thresh[np.newaxis, :]
    first = hits.argmax(axis=0).astype(np.float64) + 1.0
    first[~hits.any(axis=0)] = 0.0
    return first


def _raw_offsets(gray: np.ndarray) -> dict[str, np.ndarray]:
    height, width = gray.shape
    depth_y = max(10, int(round(height * STRIP_FRAC)))
    depth_x = max(10, int(round(width * STRIP_FRAC)))
    return {
        'top': _first_inward_hit(gray[:depth_y, :]),
        'bottom': _first_inward_hit(gray[height - 1:height - depth_y - 1:-1, :]),
        'left': _first_inward_hit(gray[:, :depth_x].T),
        'right': _first_inward_hit(gray[:, width - 1:width - depth_x - 1:-1].T),
    }


def _fit_edge(raw: np.ndarray, side_length: int) -> tuple[np.ndarray, str]:
    count = raw.shape[0]
    fitted = np.zeros(count, dtype=np.float64)
    xs = np.arange(count, dtype=np.float64)
    lo = int(round(0.04 * count))
    hi = int(round(0.96 * count))
    mask = (xs >= lo) & (xs < hi) & (raw > 0.5)
    if int(mask.sum()) < MIN_SAMPLES:
        return fitted, 'none'
    try:
        coeff = np.polyfit(xs[mask], raw[mask], 2)
    except (np.linalg.LinAlgError, ValueError):
        return fitted, 'none'
    curve = np.clip(np.polyval(coeff, xs), 0.0, None)
    residual = float(np.sqrt(np.mean((curve[mask] - raw[mask]) ** 2)))
    peak = float(curve.max())
    raw_peak = float(np.percentile(raw[mask], 90))
    safety = SAFETY_FRAC * side_length
    trim_cap = TRIM_CAP_FRAC * side_length
    if peak < STRAIGHT_PX:
        return fitted, 'none'
    if residual > POOR_RESIDUAL_PX and residual > POOR_RESIDUAL_RATIO * max(raw_peak, 1.0):
        inset = min(trim_cap, float(np.percentile(raw[mask], 75)), safety)
        fitted[:] = max(0.0, inset)
        return fitted, 'trim'
    fitted[:] = np.minimum(curve, safety)
    return fitted, 'curve'


def detect_edge_curves(image: Image.Image) -> dict:
    """Sample edge strips and fit per-edge curves. Timed for the harness."""
    started = time.perf_counter()
    rgb = np.asarray(image.convert('RGB'))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    work, scale = _working_copy(gray)
    raw = _raw_offsets(work)
    work_h, work_w = work.shape
    top, top_mode = _fit_edge(raw['top'], work_h)
    bottom, bottom_mode = _fit_edge(raw['bottom'], work_h)
    left, left_mode = _fit_edge(raw['left'], work_w)
    right, right_mode = _fit_edge(raw['right'], work_w)
    inv = 1.0 / scale
    full_w, full_h = image.size
    curves = {
        'top': np.interp(np.linspace(0, work_w - 1, full_w), np.arange(work_w), top) * inv,
        'bottom': np.interp(np.linspace(0, work_w - 1, full_w), np.arange(work_w), bottom) * inv,
        'left': np.interp(np.linspace(0, work_h - 1, full_h), np.arange(work_h), left) * inv,
        'right': np.interp(np.linspace(0, work_h - 1, full_h), np.arange(work_h), right) * inv,
        'modes': {'top': top_mode, 'bottom': bottom_mode, 'left': left_mode, 'right': right_mode},
        'elapsed_s': time.perf_counter() - started,
        'scale': scale,
    }
    return curves


def _coons_remap(pixels: np.ndarray, top, bottom, left, right) -> np.ndarray:
    height, width = pixels.shape[:2]
    if width < 2 or height < 2:
        return pixels
    u = np.linspace(0.0, 1.0, width, dtype=np.float32)
    v = np.linspace(0.0, 1.0, height, dtype=np.float32)
    uu, vv = np.meshgrid(u, v)
    top = np.clip(top.astype(np.float32), 0.0, height - 2)
    bottom = np.clip(bottom.astype(np.float32), 0.0, height - 2)
    left = np.clip(left.astype(np.float32), 0.0, width - 2)
    right = np.clip(right.astype(np.float32), 0.0, width - 2)
    # Keep opposite edges from crossing.
    room_y = np.maximum(1.0, (height - 2) - top - bottom)
    scale_y = np.minimum(1.0, room_y / np.maximum(top + bottom, 1e-3))
    top = top * scale_y
    bottom = bottom * scale_y
    room_x = np.maximum(1.0, (width - 2) - left - right)
    scale_x = np.minimum(1.0, room_x / np.maximum(left + right, 1e-3))
    left = left * scale_x
    right = right * scale_x
    # Curve endpoints must match the inset corners or Coons cancels the offset.
    p00x, p00y = float(left[0]), float(top[0])
    p10x, p10y = float((width - 1) - right[0]), float(top[-1])
    p01x, p01y = float(left[-1]), float((height - 1) - bottom[0])
    p11x, p11y = float((width - 1) - right[-1]), float((height - 1) - bottom[-1])
    top_x = np.linspace(p00x, p10x, width, dtype=np.float32)[np.newaxis, :]
    bot_x = np.linspace(p01x, p11x, width, dtype=np.float32)[np.newaxis, :]
    top_y = top[np.newaxis, :]
    bot_y = ((height - 1) - bottom)[np.newaxis, :]
    left_x = left[:, np.newaxis]
    right_x = ((width - 1) - right)[:, np.newaxis]
    left_y = np.linspace(p00y, p01y, height, dtype=np.float32)[:, np.newaxis]
    right_y = np.linspace(p10y, p11y, height, dtype=np.float32)[:, np.newaxis]
    map_x = (
        (1.0 - vv) * top_x + vv * bot_x + (1.0 - uu) * left_x + uu * right_x
        - ((1.0 - uu) * (1.0 - vv) * p00x + uu * (1.0 - vv) * p10x + (1.0 - uu) * vv * p01x + uu * vv * p11x)
    )
    map_y = (
        (1.0 - vv) * top_y + vv * bot_y + (1.0 - uu) * left_y + uu * right_y
        - ((1.0 - uu) * (1.0 - vv) * p00y + uu * (1.0 - vv) * p10y + (1.0 - uu) * vv * p01y + uu * vv * p11y)
    )
    return cv2.remap(
        pixels,
        np.ascontiguousarray(map_x, dtype=np.float32),
        np.ascontiguousarray(map_y, dtype=np.float32),
        interpolation=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def straighten_bowed_print(image: Image.Image) -> Image.Image:
    """Remap a homography crop so bowed print edges become the rectangle border."""
    curves = detect_edge_curves(image)
    modes = curves['modes']
    if all(mode == 'none' for mode in modes.values()):
        return image
    pixels = np.asarray(image.convert('RGB'))
    remapped = _coons_remap(pixels, curves['top'], curves['bottom'], curves['left'], curves['right'])
    return Image.fromarray(remapped)
