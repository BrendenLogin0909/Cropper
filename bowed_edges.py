"""Follow bowed print edges after the four-corner homography.

Default ON. An edge is remapped only with positive evidence. Quadratic sag
must exceed BOW_DEPTH_FRAC of the side (chord-relative), keep a consistent
inward sign, and have a background-band WIDTH that tracks the fitted sag
(Pearson ≥ BAND_TRACK_CORR). Displacement is DISPLACEMENT_CAP_FRAC, or
DISPLACEMENT_BACKGROUND_FRAC when the pulled pixels are scanner background.
Qualify/skip runs once at the full-res warp; preview scales those params.
Poor quadratic fit stays straight. Per-print content loss ≤ AREA_LOSS_FRAC,
geometric backstop GEOMETRIC_LOSS_FRAC; largest-loss edges drop first.

A linear (offset + tilt) bed-strip term is not applied: the 80% background
rule plus the ΔE content measure cannot tell a matching-colour print border
from a scanner-bed strip, and there is no blind fallback.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
from PIL import Image

DETECT_MAX = 800
# Scanner-bed Lab is a colour sample; 400 px is enough and keeps straight saves cheap.
BACKGROUND_MAX = 400
STRIP_FRAC = 0.16
MIN_SAMPLES = 12
# CIE76 ΔE vs the scanner-bed Lab (median of source pixels outside a dilated quad).
BACKGROUND_DE = 14.0
# Minimum fraction of the edge that must show a background-coloured band outside
# the fitted curve.
BACKGROUND_BAND_FRAC = 0.30
# Minimum quadratic sag as a fraction of the side, after linear detrend.
# Below this the edge is treated as straight. 0.4% is ~4 px on a 1000 px side
# and ~16 px on a 4000 px side.
BOW_DEPTH_FRAC = 0.004
# Pearson correlation between background-band width and the fitted sag. A real
# bow widens toward the middle with the curve; noise and picture edges do not.
BAND_TRACK_CORR = 0.75
# Interior sag samples that must share one sign (inward). Asymmetric bows are
# allowed; the peak need not be centred.
SIGN_FRAC = 0.80
# Clip fitted bow depth to this fraction of the edge length.
DEPTH_CAP_FRAC = 0.03
# Skip the edge if any pixel would move more than this fraction of the side.
# Raised to DISPLACEMENT_BACKGROUND_FRAC only when the pulled pixels are
# scanner background under BACKGROUND_DE.
DISPLACEMENT_CAP_FRAC = 0.02
DISPLACEMENT_BACKGROUND_FRAC = 0.03
# Per-print CONTENT loss: sag pixels that do not match the ring background Lab
# (ΔE > BACKGROUND_DE). Scanner-bed in the sag does not count.
AREA_LOSS_FRAC = 0.005
# Hard backstop: total GEOMETRIC sag area as a fraction of the warped rectangle.
GEOMETRIC_LOSS_FRAC = 0.015
# Dilate the source quad by this fraction of min(width, height) before taking
# the outside-median Lab.
QUAD_DILATE_FRAC = 0.01
# Full-resolution half-width (px) of the refine search around the ≤800 px curve.
REFINE_BAND_PX = 6
POOR_RESIDUAL_PX = 2.5
POOR_RESIDUAL_RATIO = 0.2


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


def _to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)


def _delta_e(lab: np.ndarray, background: np.ndarray) -> np.ndarray:
    """CIE76 ΔE. OpenCV 8-bit Lab stores L in [0, 255] = L* × 255/100."""
    d_l = (lab[..., 0] - float(background[0])) * (100.0 / 255.0)
    d_a = lab[..., 1] - float(background[1])
    d_b = lab[..., 2] - float(background[2])
    return np.sqrt(d_l * d_l + d_a * d_a + d_b * d_b)


def scanner_background_lab(image: Image.Image, corners: np.ndarray, rgb: np.ndarray | None = None) -> np.ndarray | None:
    """Median Lab of scanner-bed pixels around the crop quad.

    Prefers a sparse ring of full-resolution samples (no full-image Lab convert).
    Falls back to a ≤ BACKGROUND_MAX outside-median. Returns None when too few
    outside pixels remain (full-bleed crop) — callers then skip remapping.
    """
    if rgb is None:
        rgb = np.asarray(image) if image.mode == 'RGB' else np.asarray(image.convert('RGB'))
    else:
        rgb = np.asarray(rgb)
    ring = _ring_background_lab(rgb, corners)
    if ring is not None:
        return ring
    return _downsampled_outside_median(rgb, corners)


def _ring_background_lab(rgb: np.ndarray, corners: np.ndarray) -> np.ndarray | None:
    """Median Lab of a sparse ring just outside the quad. None if too few hits."""
    height, width = rgb.shape[:2]
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] != 4:
        return None
    centroid = pts.mean(axis=0)
    radius = max(2, int(round(QUAD_DILATE_FRAC * min(width, height))))
    offsets = []
    for offset in (2, 4, radius, radius * 2):
        value = int(offset)
        if value >= 1 and value not in offsets:
            offsets.append(value)
    contour = np.round(pts).astype(np.int32).reshape(-1, 1, 2)
    samples = []
    ts = np.linspace(0.08, 0.92, 32)
    for i in range(4):
        start = pts[i]
        end = pts[(i + 1) % 4]
        tangent = end - start
        length = float(np.hypot(tangent[0], tangent[1]))
        if length < 1.0:
            continue
        normal = np.array([-tangent[1], tangent[0]], dtype=np.float64) / length
        midpoint = (start + end) * 0.5
        if float(np.dot(normal, midpoint - centroid)) < 0.0:
            normal = -normal
        for t in ts:
            base = start + t * tangent
            for offset in offsets:
                x, y = base + normal * offset
                xi = int(round(x))
                yi = int(round(y))
                if xi < 0 or yi < 0 or xi >= width or yi >= height:
                    continue
                if cv2.pointPolygonTest(contour, (float(xi), float(yi)), False) >= 0.0:
                    continue
                samples.append(rgb[yi, xi])
    if len(samples) < 64:
        return None
    stacked = np.asarray(samples, dtype=np.uint8).reshape(-1, 1, 3)
    return np.median(_to_lab(stacked).reshape(-1, 3), axis=0)


def _downsampled_outside_median(rgb: np.ndarray, corners: np.ndarray) -> np.ndarray | None:
    """Median Lab of all pixels outside a dilated quad on a ≤ BACKGROUND_MAX copy."""
    rgb = np.asarray(rgb)
    height, width = rgb.shape[:2]
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    longest = max(width, height)
    if longest > BACKGROUND_MAX:
        scale = BACKGROUND_MAX / longest
        rgb = cv2.resize(
            rgb,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_LINEAR,
        )
        pts = pts * scale
        height, width = rgb.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(mask, np.round(pts).astype(np.int32), 1)
    radius = max(4, int(round(QUAD_DILATE_FRAC * min(width, height))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    dilated = cv2.dilate(mask, kernel)
    outside = dilated == 0
    if int(outside.sum()) < 64:
        return None
    return np.median(_to_lab(rgb)[outside], axis=0)


def _working_rgb(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = rgb.shape[:2]
    longest = max(width, height)
    if longest <= DETECT_MAX:
        return rgb, 1.0
    scale = DETECT_MAX / longest
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA), scale


def _first_non_background(strip: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Depth of the first pixel that is not scanner background, from the outer row."""
    if strip.shape[0] < 1:
        return np.zeros(strip.shape[1], dtype=np.float64)
    delta = _delta_e(strip, background)
    inside = delta > BACKGROUND_DE
    hits = inside.argmax(axis=0).astype(np.float64)
    hits[~inside.any(axis=0)] = 0.0
    return hits


def _raw_offsets(lab: np.ndarray, background: np.ndarray) -> dict[str, np.ndarray]:
    height, width = lab.shape[:2]
    depth_y = max(10, int(round(height * STRIP_FRAC)))
    depth_x = max(10, int(round(width * STRIP_FRAC)))
    return {
        'top': _first_non_background(lab[:depth_y], background),
        'bottom': _first_non_background(lab[height - 1:height - depth_y - 1:-1], background),
        'left': _first_non_background(np.transpose(lab[:, :depth_x], (1, 0, 2)), background),
        'right': _first_non_background(np.transpose(lab[:, width - 1:width - depth_x - 1:-1], (1, 0, 2)), background),
    }


def _fit_quadratic(raw: np.ndarray) -> tuple[np.ndarray, str]:
    """Fit a quadratic. Poor residual → none (never a trim)."""
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
    raw_peak = float(np.percentile(raw[mask], 90))
    if residual > POOR_RESIDUAL_PX and residual > POOR_RESIDUAL_RATIO * max(raw_peak, 1.0):
        return fitted, 'none'
    fitted[:] = curve
    return fitted, 'curve'


def _from_border(lab: np.ndarray, side: str) -> np.ndarray:
    """Reorient so axis 0 walks inward from that side's outer border."""
    if side == 'top':
        return lab
    if side == 'bottom':
        return lab[::-1]
    if side == 'left':
        return np.transpose(lab, (1, 0, 2))
    return np.transpose(lab[:, ::-1], (1, 0, 2))


def _refine_edge(lab: np.ndarray, side: str, coarse: np.ndarray, background: np.ndarray, scale: float) -> np.ndarray:
    """Walk a narrow full-res band around the ≤800 px estimate using Lab ΔE."""
    inward = _from_border(lab, side)
    depth = inward.shape[0]
    band = max(REFINE_BAND_PX, int(np.ceil(3.0 / max(scale, 1e-6))))
    refined = np.zeros_like(coarse)
    for i in range(coarse.shape[0]):
        centre = int(round(float(coarse[i])))
        lo = max(0, centre - band)
        hi = min(depth, centre + band + 1)
        strip = inward[lo:hi, i:i + 1]
        if strip.size == 0:
            continue
        local = _first_non_background(strip, background)
        refined[i] = lo + float(local[0])
    return refined


def _background_band_fraction(lab: np.ndarray, side: str, curve: np.ndarray, background: np.ndarray) -> float:
    """Fraction of the edge whose outside-the-curve pixels match the scanner bed."""
    length = int(curve.shape[0])
    if length == 0:
        return 0.0
    peak = float(curve.max())
    if peak < 1.0:
        return 0.0
    inward = _from_border(lab, side)
    max_d = min(inward.shape[0], max(1, int(np.ceil(peak))))
    delta = _delta_e(inward[:max_d], background)
    depths = np.clip(np.floor(curve).astype(np.int32), 0, max_d)
    rows = np.arange(max_d)[:, None]
    valid = (depths[None, :] >= 1) & (rows < depths[None, :])
    masked = np.where(valid, delta.astype(np.float64), np.nan)
    med = np.full(length, np.inf)
    ok = depths >= 1
    if ok.any():
        with np.errstate(all='ignore'):
            med[ok] = np.nanmedian(masked[:, ok], axis=0)
    matches = np.isfinite(med) & (med < BACKGROUND_DE)
    return float(matches.mean())


def _quadratic_sag(curve: np.ndarray) -> np.ndarray:
    """Quadratic sag relative to the chord between locked endpoints.

    Subtracts the linear tilt/offset so a uniform strip or a linear wedge is ~0
    and a bowed edge keeps only its interior depth. Endpoints stay at sag 0.
    """
    count = int(curve.shape[0])
    if count < 2:
        return np.zeros(count, dtype=np.float64)
    xs = np.arange(count, dtype=np.float64)
    chord = curve[0] + (curve[-1] - curve[0]) * xs / max(count - 1, 1)
    return curve - chord


def _consistent_inward_sag(curve: np.ndarray) -> bool:
    count = curve.shape[0]
    if count < MIN_SAMPLES:
        return False
    sag = _quadratic_sag(curve)
    lo = int(round(0.04 * count))
    hi = int(round(0.96 * count))
    interior = sag[lo:hi]
    if interior.size == 0:
        return False
    positive = float((interior > 0.5).mean())
    negative = float((interior < -0.5).mean())
    if positive < SIGN_FRAC:
        return False
    if negative > 1.0 - SIGN_FRAC:
        return False
    return True


def _band_track_correlation(band: np.ndarray, sag: np.ndarray) -> float:
    """Pearson correlation of background-band width vs fitted sag (interior)."""
    n = min(int(band.shape[0]), int(sag.shape[0]))
    if n < MIN_SAMPLES:
        return 0.0
    lo = int(round(0.04 * n))
    hi = int(round(0.96 * n))
    widths = np.asarray(band[:n][lo:hi], dtype=np.float64)
    profile = np.asarray(sag[:n][lo:hi], dtype=np.float64)
    if widths.size < MIN_SAMPLES or float(profile.max()) < 0.5:
        return 0.0
    if float(widths.std()) < 0.25 or float(profile.std()) < 0.25:
        return 0.0
    corr = np.corrcoef(widths, profile)[0, 1]
    return 0.0 if not np.isfinite(corr) else float(corr)


def _geometry_ok(curve: np.ndarray, side: str, width: int, height: int) -> bool:
    """Chord-relative sag depth and inward sign. Displacement is applied later."""
    edge_length = float(width if side in {'top', 'bottom'} else height)
    other = float(height if side in {'top', 'bottom'} else width)
    if edge_length < 2 or other < 2:
        return False
    sag = np.clip(_quadratic_sag(curve), 0.0, None)
    peak = float(sag.max()) if sag.size else 0.0
    if peak < BOW_DEPTH_FRAC * edge_length:
        return False
    if not _consistent_inward_sag(curve):
        return False
    if peak > DISPLACEMENT_BACKGROUND_FRAC * edge_length:
        return False
    return True


def _displacement_allows(curve: np.ndarray, side: str, width: int, height: int, lab: np.ndarray, background: np.ndarray) -> bool:
    """2% cap, or 3% when the pulled pixels are scanner background."""
    edge_length = float(width if side in {'top', 'bottom'} else height)
    peak = float(curve.max()) if curve.size else 0.0
    if peak <= DISPLACEMENT_CAP_FRAC * edge_length:
        return True
    if peak > DISPLACEMENT_BACKGROUND_FRAC * edge_length:
        return False
    geometric, content = _sag_loss_fractions(lab, curve, side, background, width, height)
    if geometric <= 0.0:
        return False
    if content / geometric > 0.05:
        return False
    return True


def _sag_loss_fractions(
    lab: np.ndarray,
    curve: np.ndarray,
    side: str,
    background: np.ndarray,
    width: int,
    height: int,
) -> tuple[float, float]:
    """(geometric fraction, content fraction) of the sag region.

    A sag pixel is background only if it matches the ring Lab within
    BACKGROUND_DE; everything else is content.
    """
    edge_length = float(width if side in {'top', 'bottom'} else height)
    geometric = float(curve.mean() * edge_length) / (float(width) * float(height))
    inward = _from_border(lab, side)
    peak = float(curve.max()) if curve.size else 0.0
    if peak < 0.5:
        return geometric, 0.0
    max_d = min(inward.shape[0], max(1, int(np.ceil(peak))))
    depths = np.clip(np.floor(curve).astype(np.int32), 0, max_d)
    delta = _delta_e(inward[:max_d], background)
    rows = np.arange(max_d)[:, None]
    in_sag = rows < depths[None, :]
    content = in_sag & (delta > BACKGROUND_DE)
    content_frac = float(content.sum()) / (float(width) * float(height))
    return geometric, content_frac


def _qualify_edge(
    curve: np.ndarray,
    band: np.ndarray,
    side: str,
    width: int,
    height: int,
    lab: np.ndarray,
    background: np.ndarray,
) -> tuple[np.ndarray, str, float, float]:
    """Return (applied sag or zeros, mode, geometric fraction, content fraction)."""
    zeros = np.zeros_like(curve)
    if not _geometry_ok(curve, side, width, height):
        return zeros, 'none', 0.0, 0.0
    sag = np.clip(_quadratic_sag(curve), 0.0, None)
    if _band_track_correlation(band, sag) < BAND_TRACK_CORR:
        return zeros, 'none', 0.0, 0.0
    if _background_band_fraction(lab, side, sag, background) < BACKGROUND_BAND_FRAC:
        return zeros, 'none', 0.0, 0.0
    if not _displacement_allows(sag, side, width, height, lab, background):
        return zeros, 'none', 0.0, 0.0
    edge_length = float(width if side in {'top', 'bottom'} else height)
    capped = np.minimum(sag, DEPTH_CAP_FRAC * edge_length)
    geometric, content = _sag_loss_fractions(lab, capped, side, background, width, height)
    return capped, 'curve', geometric, content


def _trim_print_area(applied: dict, modes: dict, geometric: dict, content: dict) -> tuple[float, float]:
    """Drop largest-loss edges until content ≤ 0.5% and geometric ≤ 1.5%."""
    ranked = [side for side in ('top', 'bottom', 'left', 'right') if modes[side] == 'curve']
    content_total = float(sum(content[side] for side in ranked))
    geometric_total = float(sum(geometric[side] for side in ranked))
    while ranked and (content_total > AREA_LOSS_FRAC or geometric_total > GEOMETRIC_LOSS_FRAC):
        if content_total > AREA_LOSS_FRAC:
            side = max(ranked, key=lambda name: (content[name], geometric[name]))
        else:
            side = max(ranked, key=lambda name: geometric[name])
        ranked.remove(side)
        content_total -= content[side]
        geometric_total -= geometric[side]
        modes[side] = 'none'
        applied[side] = np.zeros_like(applied[side])
        content[side] = 0.0
        geometric[side] = 0.0
    return max(0.0, content_total), max(0.0, geometric_total)


def detect_edge_curves(image: Image.Image, background_lab: np.ndarray | None = None) -> dict:
    """Sample edges by Lab ΔE vs the scanner bed. Timed for the harness."""
    started = time.perf_counter()
    full_w, full_h = image.size
    empty = {
        'top': np.zeros(full_w),
        'bottom': np.zeros(full_w),
        'left': np.zeros(full_h),
        'right': np.zeros(full_h),
        'modes': {'top': 'none', 'bottom': 'none', 'left': 'none', 'right': 'none'},
        'elapsed_s': 0.0,
        'scale': 1.0,
        'area_loss': 0.0,
        'content_loss': 0.0,
        'geometric_loss': 0.0,
    }
    if background_lab is None:
        empty['elapsed_s'] = time.perf_counter() - started
        return empty
    rgb = np.asarray(image) if image.mode == 'RGB' else np.asarray(image.convert('RGB'))
    work_rgb, scale = _working_rgb(rgb)
    work = _to_lab(work_rgb)
    raw = _raw_offsets(work, background_lab)
    work_h, work_w = work.shape[:2]
    top_w, top_mode = _fit_quadratic(raw['top'])
    bottom_w, bottom_mode = _fit_quadratic(raw['bottom'])
    left_w, left_mode = _fit_quadratic(raw['left'])
    right_w, right_mode = _fit_quadratic(raw['right'])
    inv = 1.0 / scale
    coarse = {
        'top': np.interp(np.linspace(0, work_w - 1, full_w), np.arange(work_w), top_w) * inv if top_mode == 'curve' else np.zeros(full_w),
        'bottom': np.interp(np.linspace(0, work_w - 1, full_w), np.arange(work_w), bottom_w) * inv if bottom_mode == 'curve' else np.zeros(full_w),
        'left': np.interp(np.linspace(0, work_h - 1, full_h), np.arange(work_h), left_w) * inv if left_mode == 'curve' else np.zeros(full_h),
        'right': np.interp(np.linspace(0, work_h - 1, full_h), np.arange(work_h), right_w) * inv if right_mode == 'curve' else np.zeros(full_h),
    }
    work_fitted = {
        'top': top_w if top_mode == 'curve' else np.zeros(work_w),
        'bottom': bottom_w if bottom_mode == 'curve' else np.zeros(work_w),
        'left': left_w if left_mode == 'curve' else np.zeros(work_h),
        'right': right_w if right_mode == 'curve' else np.zeros(work_h),
    }
    candidates = []
    for side in ('top', 'bottom', 'left', 'right'):
        if not _geometry_ok(coarse[side], side, full_w, full_h):
            continue
        if _background_band_fraction(work, side, work_fitted[side], background_lab) < BACKGROUND_BAND_FRAC:
            continue
        candidates.append(side)
    if not candidates:
        empty['elapsed_s'] = time.perf_counter() - started
        empty['scale'] = scale
        return empty
    lab = _to_lab(rgb)
    refined = {}
    bands = {}
    for side in ('top', 'bottom', 'left', 'right'):
        if side not in candidates:
            refined[side] = np.zeros_like(coarse[side])
            bands[side] = np.zeros_like(coarse[side])
            continue
        hits = _refine_edge(lab, side, coarse[side], background_lab, scale)
        fitted, mode = _fit_quadratic(hits)
        refined[side] = fitted if mode == 'curve' else np.zeros_like(hits)
        bands[side] = hits
    modes = {}
    applied = {}
    geometric = {}
    content = {}
    for side in ('top', 'bottom', 'left', 'right'):
        curve, mode, geo, cont = _qualify_edge(
            refined[side], bands[side], side, full_w, full_h, lab, background_lab,
        )
        applied[side] = curve
        modes[side] = mode
        geometric[side] = geo
        content[side] = cont
    content_loss, geometric_loss = _trim_print_area(applied, modes, geometric, content)
    return {
        'top': applied['top'],
        'bottom': applied['bottom'],
        'left': applied['left'],
        'right': applied['right'],
        'modes': modes,
        'elapsed_s': time.perf_counter() - started,
        'scale': scale,
        'area_loss': content_loss,
        'content_loss': content_loss,
        'geometric_loss': geometric_loss,
    }


def _stretch_band(pixels: np.ndarray, curve: np.ndarray, side: str) -> np.ndarray:
    """Remap only the affected edge band. Identity elsewhere."""
    height, width = pixels.shape[:2]
    peak = float(curve.max())
    if peak < 0.5:
        return pixels
    pad = 4
    if side in {'top', 'bottom'}:
        band = min(height, max(2, int(np.ceil(peak * 2.0)) + pad))
        xs = np.arange(width, dtype=np.float32)[None, :]
        y_local = np.arange(band, dtype=np.float32)[:, None]
        denom = max(band - 1, 1)
        curve_f = curve.astype(np.float32)[None, :]
        if side == 'top':
            y_src = curve_f + y_local * ((band - 1) - curve_f) / denom
            x_src = np.broadcast_to(xs, (band, width))
            remapped = cv2.remap(
                pixels,
                np.ascontiguousarray(x_src),
                np.ascontiguousarray(y_src),
                interpolation=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
            out = np.array(pixels, copy=True)
            out[:band] = remapped
            return out
        y_src = (height - band) + y_local * ((band - 1) - curve_f) / denom
        x_src = np.broadcast_to(xs, (band, width))
        remapped = cv2.remap(
            pixels,
            np.ascontiguousarray(x_src),
            np.ascontiguousarray(y_src),
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        out = np.array(pixels, copy=True)
        out[height - band:] = remapped
        return out
    band = min(width, max(2, int(np.ceil(peak * 2.0)) + pad))
    ys = np.arange(height, dtype=np.float32)[:, None]
    x_local = np.arange(band, dtype=np.float32)[None, :]
    denom = max(band - 1, 1)
    curve_f = curve.astype(np.float32)[:, None]
    if side == 'left':
        x_src = curve_f + x_local * ((band - 1) - curve_f) / denom
        y_src = np.broadcast_to(ys, (height, band))
        remapped = cv2.remap(
            pixels,
            np.ascontiguousarray(x_src),
            np.ascontiguousarray(y_src),
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        out = np.array(pixels, copy=True)
        out[:, :band] = remapped
        return out
    x_src = (width - band) + x_local * ((band - 1) - curve_f) / denom
    y_src = np.broadcast_to(ys, (height, band))
    remapped = cv2.remap(
        pixels,
        np.ascontiguousarray(x_src),
        np.ascontiguousarray(y_src),
        interpolation=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    out = np.array(pixels, copy=True)
    out[:, width - band:] = remapped
    return out


def scale_curves(curves: dict, src_size: tuple[int, int], dest_size: tuple[int, int]) -> dict:
    """Resample already-qualified curves from a full-res warp onto a smaller warp.

    Depths scale with the destination size. Modes are copied, not re-qualified,
    so a bow that passed BOW_DEPTH_FRAC at save resolution is still applied on
    the 1400 px preview. Preview does not re-gate or refine.
    """
    src_w, src_h = src_size
    dest_w, dest_h = dest_size
    sx = dest_w / max(src_w, 1)
    sy = dest_h / max(src_h, 1)

    def resample(curve: np.ndarray, dest_len: int, depth_scale: float) -> np.ndarray:
        dest_len = max(int(dest_len), 0)
        if dest_len == 0:
            return np.zeros(0, dtype=np.float64)
        if curve.size == 0:
            return np.zeros(dest_len, dtype=np.float64)
        xs = np.linspace(0, curve.size - 1, dest_len)
        return np.interp(xs, np.arange(curve.size, dtype=np.float64), curve) * depth_scale

    return {
        'top': resample(curves['top'], dest_w, sy),
        'bottom': resample(curves['bottom'], dest_w, sy),
        'left': resample(curves['left'], dest_h, sx),
        'right': resample(curves['right'], dest_h, sx),
        'modes': dict(curves['modes']),
        'elapsed_s': curves.get('elapsed_s', 0.0),
        'scale': curves.get('scale', 1.0),
        'area_loss': curves.get('area_loss', 0.0),
        'content_loss': curves.get('content_loss', curves.get('area_loss', 0.0)),
        'geometric_loss': curves.get('geometric_loss', 0.0),
    }


def apply_curves(image: Image.Image, curves: dict) -> Image.Image:
    """Remap qualified edge bands. Same image object if every mode is none."""
    if all(mode == 'none' for mode in curves['modes'].values()):
        return image
    pixels = np.asarray(image) if image.mode == 'RGB' else np.asarray(image.convert('RGB'))
    remapped = _remap_qualified(pixels, curves)
    return Image.fromarray(remapped)


def _remap_qualified(pixels: np.ndarray, curves: dict) -> np.ndarray:
    out = pixels
    for side in ('top', 'bottom', 'left', 'right'):
        if curves['modes'][side] != 'curve':
            continue
        out = _stretch_band(out, curves[side], side)
    return out


def straighten_bowed_print(
    image: Image.Image,
    background_lab: np.ndarray | None = None,
    curves: dict | None = None,
) -> Image.Image:
    """Remap a homography crop so qualified bowed print edges become the border.

    If no edge qualifies, the same image object is returned (byte-identical to OFF).
    Pass `curves` to apply already-computed (or scaled) params instead of refitting.
    """
    if curves is None:
        curves = detect_edge_curves(image, background_lab)
    return apply_curves(image, curves)
