"""Follow bowed print edges after the four-corner homography.

Default ON. An edge is remapped only with positive evidence. Chord-relative sag
must exceed max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX), keep a consistent inward
sign, and have a background-band WIDTH that tracks the fitted sag (Pearson ≥
BAND_TRACK_CORR). The profile is quadratic, cubic, or a ≤5-point spline, chosen
by BIC on residual vs the background band. Displacement is DISPLACEMENT_CAP_FRAC,
or DISPLACEMENT_BACKGROUND_FRAC when the pulled pixels are scanner background.
Qualify/skip runs once at the full-res warp; preview scales those params.
A cheap ≤960 downsample can skip full detection when no edge is a candidate.
Per-print content loss ≤ AREA_LOSS_FRAC, geometric backstop GEOMETRIC_LOSS_FRAC;
largest-loss edges drop first. No linear strip term.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
from PIL import Image

DETECT_MAX = 800
# Mid-scale refine longest side (between the ≤800 coarse fit and full-res).
REFINE_MID_MAX = 2000
# Cheap early-exit longest side, matching the 960 detect cache.
EARLY_MAX = 960
# Scanner-bed Lab is a colour sample; 400 px is enough and keeps straight saves cheap.
BACKGROUND_MAX = 400
STRIP_FRAC = 0.16
MIN_SAMPLES = 12
# CIE76 ΔE vs the scanner-bed Lab (median of source pixels outside a dilated quad).
BACKGROUND_DE = 14.0
# Minimum fraction of the edge that must show a background-coloured band outside
# the fitted curve.
BACKGROUND_BAND_FRAC = 0.30
# Minimum sag as a fraction of the side, after linear detrend. Retune this one
# constant; the gate is max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX).
BOW_DEPTH_FRAC = 0.002
# Absolute full-res floor of the depth gate.
BOW_DEPTH_PX = 3.0
# Pearson correlation between background-band width and the fitted sag. A real
# bow widens toward the middle with the curve; noise and picture edges do not.
BAND_TRACK_CORR = 0.75
# Spline interior control points; total knots = SPLINE_CONTROLS, ends locked.
SPLINE_CONTROLS = 5
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


def _working_rgb(rgb: np.ndarray, max_side: int = DETECT_MAX) -> tuple[np.ndarray, float]:
    height, width = rgb.shape[:2]
    longest = max(width, height)
    if longest <= max_side:
        return rgb, 1.0
    scale = max_side / longest
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA), scale


def _depth_gate(edge_length: float) -> float:
    """Full-res sag must reach max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX)."""
    return max(BOW_DEPTH_FRAC * float(edge_length), BOW_DEPTH_PX)


def _edge_length(side: str, width: int, height: int) -> float:
    return float(width if side in {'top', 'bottom'} else height)


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


def _sample_mask(raw: np.ndarray) -> np.ndarray:
    count = int(raw.shape[0])
    xs = np.arange(count, dtype=np.float64)
    lo = int(round(0.04 * count))
    hi = int(round(0.96 * count))
    return (xs >= lo) & (xs < hi) & (raw > 0.5)


def _t_coord(count: int) -> np.ndarray:
    return np.arange(count, dtype=np.float64) / max(count - 1, 1)


def _fit_locked_poly(raw: np.ndarray, degree: int, mask: np.ndarray) -> np.ndarray | None:
    """Polynomial sag with both ends locked at 0. degree 2 or 3."""
    count = int(raw.shape[0])
    t = _t_coord(count)
    chord = raw[0] + (raw[-1] - raw[0]) * t
    sag_obs = raw - chord
    bases = [t * (1.0 - t)]
    if degree >= 3:
        bases.append(t * (1.0 - t) * (2.0 * t - 1.0))
    matrix = np.column_stack(bases)
    if int(mask.sum()) < MIN_SAMPLES:
        return None
    try:
        coef, *_ = np.linalg.lstsq(matrix[mask], sag_obs[mask], rcond=None)
    except np.linalg.LinAlgError:
        return None
    sag = matrix @ coef
    sag[0] = 0.0
    sag[-1] = 0.0
    return np.clip(sag, 0.0, None)


def _fit_locked_spline(raw: np.ndarray, mask: np.ndarray, n_ctrl: int = SPLINE_CONTROLS) -> np.ndarray | None:
    """Piecewise-linear spline, ≤5 control points, ends locked at sag 0."""
    n_ctrl = max(3, min(int(n_ctrl), SPLINE_CONTROLS))
    count = int(raw.shape[0])
    t = _t_coord(count)
    chord = raw[0] + (raw[-1] - raw[0]) * t
    sag_obs = raw - chord
    knots = np.linspace(0.0, 1.0, n_ctrl)
    width = float(knots[1] - knots[0]) if n_ctrl > 1 else 1.0
    interior = knots[1:-1]
    if interior.size == 0 or int(mask.sum()) < MIN_SAMPLES:
        return None
    matrix = np.column_stack([np.clip(1.0 - np.abs(t - knot) / width, 0.0, None) for knot in interior])
    try:
        coef, *_ = np.linalg.lstsq(matrix[mask], sag_obs[mask], rcond=None)
    except np.linalg.LinAlgError:
        return None
    sag = matrix @ coef
    sag[0] = 0.0
    sag[-1] = 0.0
    return np.clip(sag, 0.0, None)


def _profile_residual(curve: np.ndarray, raw: np.ndarray, mask: np.ndarray) -> float:
    if int(mask.sum()) < 1:
        return float('inf')
    return float(np.sqrt(np.mean((curve[mask] - raw[mask]) ** 2)))


def _bic(residual: float, n: int, k: int) -> float:
    """BIC on RMSE vs the background-band samples. k is free parameters."""
    n = max(int(n), 2)
    rss = max(residual * residual * n, 1e-12)
    return float(n * np.log(rss / n) + k * np.log(n))


def _fit_profile(raw: np.ndarray) -> tuple[np.ndarray, str, float]:
    """Pick quadratic, cubic, or ≤5-point spline by BIC vs the band.

    Ends stay locked (sag 0). A more complex model wins only if BIC improves
    on the k=0 zero-sag baseline. RMSE > POOR_RESIDUAL_PX → none (never a trim).
    k = 1 (quadratic), 2 (cubic), 3 (spline).
    """
    count = int(raw.shape[0])
    zeros = np.zeros(count, dtype=np.float64)
    mask = _sample_mask(raw)
    n = int(mask.sum())
    if n < MIN_SAMPLES:
        return zeros, 'none', float('inf')
    candidates: list[tuple[float, float, str, np.ndarray]] = []
    sag_obs = raw - (raw[0] + (raw[-1] - raw[0]) * _t_coord(count))
    # k=0 zero-sag baseline: a spline must beat this on BIC and stay under
    # POOR_RESIDUAL_PX so it cannot model picture content or a perspective margin.
    zero_residual = _profile_residual(zeros, sag_obs, mask)
    candidates.append((_bic(zero_residual, n, 0), zero_residual, 'none', zeros))
    for name, curve, k in (
        ('quadratic', _fit_locked_poly(raw, 2, mask), 1),
        ('cubic', _fit_locked_poly(raw, 3, mask), 2),
        ('spline', _fit_locked_spline(raw, mask), 3),
    ):
        if curve is None:
            continue
        residual = _profile_residual(curve, sag_obs, mask)
        if residual > POOR_RESIDUAL_PX:
            continue
        candidates.append((_bic(residual, n, k), residual, name, curve))
    if not candidates:
        return zeros, 'none', float('inf')
    candidates.sort(key=lambda item: (item[0], item[1], {'none': 0, 'quadratic': 1, 'cubic': 2, 'spline': 3}[item[2]]))
    _bic_score, residual, name, curve = candidates[0]
    if name == 'none':
        return zeros, 'none', residual
    return curve, name, residual


def _fit_quadratic(raw: np.ndarray) -> tuple[np.ndarray, str]:
    """Back-compat wrapper: profile fit, 'curve' if any model won."""
    curve, model, _residual = _fit_profile(raw)
    return curve, ('curve' if model != 'none' else 'none')


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
    """Walk a band around the coarse edge using Lab ΔE. Vectorised over the side."""
    inward = _from_border(lab, side)
    depth = inward.shape[0]
    width = inward.shape[1]
    if width == 0 or coarse.shape[0] == 0:
        return np.zeros_like(coarse)
    edge_len = float(max(width, 1))
    band = max(
        REFINE_BAND_PX,
        int(np.ceil(3.0 / max(scale, 1e-6))),
        int(np.ceil(0.015 * edge_len * max(scale, 1e-6))),
    )
    centres = np.clip(np.round(coarse).astype(np.int32), 0, max(depth - 1, 0))
    pad = band
    padded = np.pad(inward, ((pad, pad), (0, 0), (0, 0)), mode='edge')
    offsets = np.arange(-band, band + 1, dtype=np.int32)[:, None]
    rows = centres[None, :] + pad + offsets
    cols = np.arange(width, dtype=np.int32)[None, :]
    strip = padded[rows, np.broadcast_to(cols, rows.shape)]
    delta = _delta_e(strip, background)
    inside = delta > BACKGROUND_DE
    found = inside.any(axis=0)
    local = inside.argmax(axis=0).astype(np.float64)
    result = (centres - band).astype(np.float64) + local
    result[~found] = centres[~found].astype(np.float64)
    return np.clip(result, 0.0, float(depth))


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


def _geometry_ok(
    curve: np.ndarray,
    side: str,
    width: int,
    height: int,
    band: np.ndarray | None = None,
) -> bool:
    """Depth gate vs background-band width; sag must still be a real bow.

    The gate is max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX). Depth is the
    observed band (p90) so a 3 px bow is not lost to 1 px end AA after the
    chord detrend. Chord-relative sag must stay ≥ half the gate so a strip
    or wedge (sag ~0) does not qualify.
    """
    edge_length = _edge_length(side, width, height)
    other = float(height if side in {'top', 'bottom'} else width)
    if edge_length < 2 or other < 2:
        return False
    sag = np.clip(_quadratic_sag(curve), 0.0, None)
    sag_peak = float(sag.max()) if sag.size else 0.0
    gate = _depth_gate(edge_length)
    if band is not None and getattr(band, 'size', 0):
        n = min(int(band.shape[0]), int(curve.shape[0]))
        depth = max(sag_peak, float(np.percentile(np.asarray(band[:n], dtype=np.float64), 90)))
    else:
        depth = sag_peak
    if depth < gate:
        return False
    if sag_peak < 0.5 * gate:
        return False
    if not _consistent_inward_sag(curve):
        return False
    if sag_peak > DISPLACEMENT_BACKGROUND_FRAC * edge_length:
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


def _edge_log(
    model: str = 'none',
    depth: float = 0.0,
    residual: float = 0.0,
    area: float = 0.0,
    content_loss: float = 0.0,
) -> dict:
    return {
        'model': model,
        'depth': float(depth),
        'residual': float(residual) if np.isfinite(residual) else 0.0,
        'area': float(area),
        'content_loss': float(content_loss),
    }


def _qualify_edge(
    curve: np.ndarray,
    band: np.ndarray,
    side: str,
    width: int,
    height: int,
    lab: np.ndarray,
    background: np.ndarray,
    model: str = 'quadratic',
    residual: float = 0.0,
) -> tuple[np.ndarray, str, str, float, float, dict]:
    """Return (applied sag, mode, model, geometric, content, log)."""
    zeros = np.zeros_like(curve)
    empty = _edge_log()
    if model == 'none' or not _geometry_ok(curve, side, width, height, band=band):
        return zeros, 'none', 'none', 0.0, 0.0, empty
    sag = np.clip(_quadratic_sag(curve), 0.0, None)
    if sag.size >= 2:
        sag[0] = 0.0
        sag[-1] = 0.0
    if _band_track_correlation(band, sag) < BAND_TRACK_CORR:
        return zeros, 'none', 'none', 0.0, 0.0, empty
    if _background_band_fraction(lab, side, sag, background) < BACKGROUND_BAND_FRAC:
        return zeros, 'none', 'none', 0.0, 0.0, empty
    if not _displacement_allows(sag, side, width, height, lab, background):
        return zeros, 'none', 'none', 0.0, 0.0, empty
    edge_length = _edge_length(side, width, height)
    capped = np.minimum(sag, DEPTH_CAP_FRAC * edge_length)
    geometric, content = _sag_loss_fractions(lab, capped, side, background, width, height)
    log = _edge_log(model, float(capped.max()), residual, geometric, content)
    return capped, 'curve', model, geometric, content, log


def _trim_print_area(
    applied: dict,
    modes: dict,
    models: dict,
    geometric: dict,
    content: dict,
    logs: dict,
) -> tuple[float, float]:
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
        models[side] = 'none'
        applied[side] = np.zeros_like(applied[side])
        content[side] = 0.0
        geometric[side] = 0.0
        logs[side] = _edge_log()
    return max(0.0, content_total), max(0.0, geometric_total)


def _empty_curves(width: int, height: int, elapsed_s: float = 0.0, scale: float = 1.0) -> dict:
    none = {'top': 'none', 'bottom': 'none', 'left': 'none', 'right': 'none'}
    return {
        'top': np.zeros(width),
        'bottom': np.zeros(width),
        'left': np.zeros(height),
        'right': np.zeros(height),
        'modes': dict(none),
        'models': dict(none),
        'edges': {side: _edge_log() for side in none},
        'elapsed_s': elapsed_s,
        'scale': scale,
        'area_loss': 0.0,
        'content_loss': 0.0,
        'geometric_loss': 0.0,
        'early_exit': False,
    }


def _upsample_curve(curve: np.ndarray, dest_len: int, depth_scale: float) -> np.ndarray:
    dest_len = max(int(dest_len), 0)
    if dest_len == 0:
        return np.zeros(0, dtype=np.float64)
    if curve.size == 0:
        return np.zeros(dest_len, dtype=np.float64)
    xs = np.linspace(0, curve.size - 1, dest_len)
    return np.interp(xs, np.arange(curve.size, dtype=np.float64), curve) * depth_scale


def _cheap_has_candidate(rgb: np.ndarray, background: np.ndarray, full_w: int, full_h: int) -> bool:
    """True if a ≤960 downsample still looks like a bow on any side."""
    work_rgb, scale = _working_rgb(rgb, EARLY_MAX)
    work = _to_lab(work_rgb)
    raw = _raw_offsets(work, background)
    inv = 1.0 / max(scale, 1e-6)
    for side in ('top', 'bottom', 'left', 'right'):
        fitted, model, _residual = _fit_profile(raw[side])
        if model == 'none':
            continue
        sag = np.clip(_quadratic_sag(fitted), 0.0, None)
        peak = float(sag.max()) * inv
        if peak < 0.5 * _depth_gate(_edge_length(side, full_w, full_h)):
            continue
        if not _consistent_inward_sag(fitted):
            continue
        return True
    return False


def detect_edge_curves(
    image: Image.Image,
    background_lab: np.ndarray | None = None,
    *,
    force_full: bool = False,
) -> dict:
    """Sample edges by Lab ΔE vs the scanner bed. Timed for the harness.

    force_full=True skips the cheap ≤960 early exit so tests can compare
    decisions. Early skip is only taken when the cheap path sees no candidate;
    otherwise the full ≤800 + mid/full refine path runs.
    """
    started = time.perf_counter()
    full_w, full_h = image.size
    if background_lab is None:
        empty = _empty_curves(full_w, full_h)
        empty['elapsed_s'] = time.perf_counter() - started
        return empty
    rgb = np.asarray(image) if image.mode == 'RGB' else np.asarray(image.convert('RGB'))
    if not force_full and not _cheap_has_candidate(rgb, background_lab, full_w, full_h):
        empty = _empty_curves(full_w, full_h)
        empty['elapsed_s'] = time.perf_counter() - started
        empty['early_exit'] = True
        return empty
    work_rgb, scale = _working_rgb(rgb, DETECT_MAX)
    work = _to_lab(work_rgb)
    raw = _raw_offsets(work, background_lab)
    work_h, work_w = work.shape[:2]
    fits = {side: _fit_profile(raw[side]) for side in ('top', 'bottom', 'left', 'right')}
    inv = 1.0 / max(scale, 1e-6)
    coarse = {}
    work_fitted = {}
    for side, (curve, model, _residual) in fits.items():
        dest = full_w if side in {'top', 'bottom'} else full_h
        src_len = work_w if side in {'top', 'bottom'} else work_h
        if model == 'none':
            coarse[side] = np.zeros(dest)
            work_fitted[side] = np.zeros(src_len)
            continue
        coarse[side] = _upsample_curve(curve, dest, inv)
        work_fitted[side] = curve
    candidates = []
    for side in ('top', 'bottom', 'left', 'right'):
        gate = _depth_gate(_edge_length(side, full_w, full_h))
        raw_peak = float(raw[side].max()) * inv
        if not _geometry_ok(coarse[side], side, full_w, full_h) and raw_peak < 0.5 * gate:
            continue
        band_src = work_fitted[side]
        if float(band_src.max()) < 0.5:
            band_src = raw[side]
        if _background_band_fraction(work, side, band_src, background_lab) < BACKGROUND_BAND_FRAC:
            continue
        candidates.append(side)
    if not candidates:
        empty = _empty_curves(full_w, full_h, time.perf_counter() - started, scale)
        return empty
    lab = None
    mid_lab = None
    mid_scale = 1.0
    longest = max(full_w, full_h)
    if longest > REFINE_MID_MAX:
        mid_rgb, mid_scale = _working_rgb(rgb, REFINE_MID_MAX)
        mid_lab = _to_lab(mid_rgb)
    lab = _to_lab(rgb)
    refined = {}
    bands = {}
    models = {}
    residuals = {}
    for side in ('top', 'bottom', 'left', 'right'):
        dest = coarse[side]
        if side not in candidates:
            refined[side] = np.zeros_like(dest)
            bands[side] = np.zeros_like(dest)
            models[side] = 'none'
            residuals[side] = 0.0
            continue
        estimate = dest
        if mid_lab is not None:
            mid_len = mid_lab.shape[1] if side in {'top', 'bottom'} else mid_lab.shape[0]
            estimate_mid = _upsample_curve(dest, mid_len, mid_scale)
            hits_mid = _refine_edge(mid_lab, side, estimate_mid, background_lab, mid_scale)
            mid_fit, mid_model, _mid_res = _fit_profile(hits_mid)
            if mid_model != 'none':
                estimate = _upsample_curve(mid_fit, dest.shape[0], 1.0 / max(mid_scale, 1e-6))
        hits = _refine_edge(lab, side, estimate, background_lab, 1.0 if mid_lab is not None else scale)
        fitted, model, residual = _fit_profile(hits)
        refined[side] = fitted if model != 'none' else np.zeros_like(hits)
        bands[side] = hits
        models[side] = model
        residuals[side] = residual
    modes = {}
    applied = {}
    geometric = {}
    content = {}
    logs = {}
    chosen = {}
    for side in ('top', 'bottom', 'left', 'right'):
        curve, mode, model, geo, cont, log = _qualify_edge(
            refined[side], bands[side], side, full_w, full_h, lab, background_lab,
            model=models[side], residual=residuals[side],
        )
        applied[side] = curve
        modes[side] = mode
        chosen[side] = model
        geometric[side] = geo
        content[side] = cont
        logs[side] = log
    content_loss, geometric_loss = _trim_print_area(applied, modes, chosen, geometric, content, logs)
    return {
        'top': applied['top'],
        'bottom': applied['bottom'],
        'left': applied['left'],
        'right': applied['right'],
        'modes': modes,
        'models': chosen,
        'edges': logs,
        'elapsed_s': time.perf_counter() - started,
        'scale': scale,
        'area_loss': content_loss,
        'content_loss': content_loss,
        'geometric_loss': geometric_loss,
        'early_exit': False,
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

    scaled_edges = {}
    for side, log in dict(curves.get('edges', {})).items():
        depth_scale = sy if side in {'top', 'bottom'} else sx
        scaled_edges[side] = _edge_log(
            log.get('model', 'none'),
            float(log.get('depth', 0.0)) * depth_scale,
            log.get('residual', 0.0),
            log.get('area', 0.0),
            log.get('content_loss', 0.0),
        )
    return {
        'top': resample(curves['top'], dest_w, sy),
        'bottom': resample(curves['bottom'], dest_w, sy),
        'left': resample(curves['left'], dest_h, sx),
        'right': resample(curves['right'], dest_h, sx),
        'modes': dict(curves['modes']),
        'models': dict(curves.get('models', curves['modes'])),
        'edges': scaled_edges,
        'elapsed_s': curves.get('elapsed_s', 0.0),
        'scale': curves.get('scale', 1.0),
        'area_loss': curves.get('area_loss', 0.0),
        'content_loss': curves.get('content_loss', curves.get('area_loss', 0.0)),
        'geometric_loss': curves.get('geometric_loss', 0.0),
        'early_exit': bool(curves.get('early_exit', False)),
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
