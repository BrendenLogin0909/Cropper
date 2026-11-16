"""Synthetic bowed-edge warp tests. No real photos."""
from __future__ import annotations

import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw

import bowed_edges
import crop_cache
from server import warp


PAGE = (203, 185, 154)
PRINT = (37, 72, 140)
# Qualifying single-edge bows: sag ≥ max(BOW_DEPTH_FRAC × side, BOW_DEPTH_PX),
# displacement 2% or 3% when the sag is scanner background, content ≤ 0.5%.
QUALIFY_1200 = (1200, 900, 80, 8)
QUALIFY_2000 = (2000, 1500, 140, 12)
QUALIFY_4500 = (4500, 3400, 280, 18)
# 11 px at 4500 is above max(0.2% × side, 3 px) ≈ 7.9 px: both paths remap.
QUALIFY_4500_SHALLOW = (4500, 3400, 280, 11)


def synthetic_curved_rectangle(width=640, height=480, margin=48, bow=22, inward=True, sides=None):
    """Axis-aligned rectangle whose chosen edges curve in (bow) or out (pincushion)."""
    sides = set(sides or ('top', 'bottom', 'left', 'right'))
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    xs = np.arange(width)
    ys = np.arange(height)
    xx, yy = np.meshgrid(xs, ys)
    inner_w = max(1, width - 2 * margin)
    inner_h = max(1, height - 2 * margin)
    t = np.clip((xx - margin) / inner_w, 0.0, 1.0)
    s = np.clip((yy - margin) / inner_h, 0.0, 1.0)
    bow_x = 4.0 * t * (1.0 - t) * bow
    bow_y = 4.0 * s * (1.0 - s) * bow
    sign = 1.0 if inward else -1.0
    top = margin + (sign * bow_x if 'top' in sides else 0.0)
    bot = height - 1 - margin - (sign * bow_x if 'bottom' in sides else 0.0)
    left = margin + (sign * bow_y if 'left' in sides else 0.0)
    right = width - 1 - margin - (sign * bow_y if 'right' in sides else 0.0)
    inside = (yy >= top) & (yy <= bot) & (xx >= left) & (xx <= right)
    pixels[inside] = PRINT
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


def synthetic_ragged_rectangle(width=480, height=360, margin=40, bite=36):
    """Print whose top edge is a step, so a quadratic fit is a poor model."""
    image = Image.new('RGB', (width, height), PAGE)
    draw = ImageDraw.Draw(image)
    mid = width // 2
    draw.polygon(
        [
            (margin, margin + 6),
            (mid, margin + 6),
            (mid, margin + bite),
            (width - 1 - margin, margin + bite),
            (width - 1 - margin, height - 1 - margin),
            (margin, height - 1 - margin),
        ],
        fill=PRINT,
    )
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return image, corners


def synthetic_content_to_edges(kind, width=900, height=700, margin=48):
    """High-contrast content to the print edges; PAGE only outside the quad.

    No scanner-background band inside the warped rectangle.
    """
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    inner = pixels[margin:height - margin, margin:width - margin]
    ih, iw = inner.shape[:2]
    if kind == 'stripes':
        inner[:] = PRINT
        inner[:, ::16] = (240, 240, 240)
        inner[:, 1::16] = (18, 18, 18)
    elif kind == 'blocks':
        inner[:ih // 2, :iw // 2] = (18, 18, 18)
        inner[:ih // 2, iw // 2:] = (240, 240, 220)
        inner[ih // 2:, :iw // 2] = (240, 230, 20)
        inner[ih // 2:, iw // 2:] = (20, 40, 180)
    elif kind == 'horizon':
        inner[:28] = (28, 28, 38)
        inner[28:ih // 2] = (80, 120, 180)
        inner[ih // 2:] = (40, 90, 40)
    elif kind == 'sky':
        inner[:ih // 3] = (220, 230, 245)
        inner[ih // 3:] = (50, 90, 40)
    else:
        raise ValueError(kind)
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


def synthetic_bowed_with_streak(width=1200, height=900, margin=80, bow=8, streak=2):
    """Bowed print on PAGE with a thin varying streak in the top concavity."""
    image, corners = synthetic_curved_rectangle(
        width=width, height=height, margin=margin, bow=bow, sides=('top',),
    )
    pixels = np.asarray(image).copy()
    xs = np.arange(width)
    inner_w = max(1, width - 2 * margin)
    t = np.clip((xs - margin) / inner_w, 0.0, 1.0)
    top = margin + 4.0 * t * (1.0 - t) * bow
    for x in range(margin, width - margin):
        edge = int(np.floor(top[x]))
        for k in range(streak):
            y = edge - 1 - k
            if y < 0 or y >= height:
                continue
            shift = int(18 * np.sin(x / 11.0))
            pixels[y, x] = (
                np.clip(PAGE[0] + shift, 0, 255),
                np.clip(PAGE[1] - shift // 2, 0, 255),
                np.clip(PAGE[2] + shift // 3, 0, 255),
            )
    return Image.fromarray(pixels), corners


def synthetic_straight_print(width=1200, height=900, margin=80):
    """Axis-aligned straight print on PAGE. Corners sit on the print corners."""
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    pixels[margin:height - margin, margin:width - margin] = PRINT
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


def synthetic_uniform_strip(width=1200, height=900, margin=80, inset=5):
    """Straight print whose top sits `inset` px inside the locked top edge."""
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    pixels[margin:margin + inset, margin:width - margin] = PAGE
    return Image.fromarray(pixels), corners


def synthetic_linear_wedge(width=1200, height=900, margin=80, start=0, wedge=8):
    """Straight print whose top PAGE depth goes `start` → `wedge` px left to right."""
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    inner_w = max(1, width - 2 * margin)
    for x in range(margin, width - margin):
        t = (x - margin) / max(inner_w - 1, 1)
        depth = int(round(start + (wedge - start) * t))
        if depth > 0:
            pixels[margin:margin + depth, x] = PAGE
    return Image.fromarray(pixels), corners


def synthetic_matching_print_border(width=3000, height=2200, margin=80, border=15):
    """Photo with a uniform print border the same colour as the scanner bed.

    Locked corners sit on the outer print rectangle, so the warped crop starts
    on the border. There is no print-edge line or shadow separating border
    from bed.
    """
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    inner = margin + border
    pixels[inner:height - inner, inner:width - inner] = PRINT
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


def synthetic_noisy_straight_edge(width=2000, height=1500, margin=140, amplitude=10, seed=1):
    """Straight print whose top PAGE bite is uncorrelated noise, not a bow."""
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    rng = np.random.RandomState(seed)
    for x in range(margin, width - margin):
        depth = int(rng.randint(0, amplitude + 1))
        if depth:
            pixels[margin:margin + depth, x] = PAGE
    return Image.fromarray(pixels), corners


def synthetic_picture_edge(width=2000, height=1500, margin=140, bow=16):
    """Straight print whose *content* has a curved dark edge near the top.

    The locked crop starts on PRINT. A picture-edge curve is not a bed bow.
    """
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    inner_w = max(1, width - 2 * margin)
    xs = np.arange(width)
    t = np.clip((xs - margin) / inner_w, 0.0, 1.0)
    curve = margin + 8 + 4.0 * t * (1.0 - t) * bow
    for x in range(margin, width - margin):
        y = int(round(curve[x]))
        lo = max(margin, y - 2)
        hi = min(height - margin, y + 3)
        pixels[lo:hi, x] = (18, 18, 22)
    return Image.fromarray(pixels), corners


def synthetic_wavy_content_line(width=2000, height=1500, margin=140, amp=14):
    """Straight print with a wavy dark content line just inside the top edge."""
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    inner_w = max(1, width - 2 * margin)
    for x in range(margin, width - margin):
        t = (x - margin) / max(inner_w - 1, 1)
        y = margin + 10 + int(round(amp * np.sin(t * 8.0 * np.pi)))
        lo = max(margin, y - 1)
        hi = min(height - margin, y + 2)
        pixels[lo:hi, x] = (20, 18, 16)
    return Image.fromarray(pixels), corners


def synthetic_asymmetric_bow(width=3000, height=3400, margin=80, bow=60, peak_t=0.35, streak=True):
    """Inward bow peaking off-centre. Sag is PAGE when streak, else content."""
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    xs = np.arange(width)
    inner_w = max(1, width - 2 * margin)
    t = np.clip((xs - margin) / inner_w, 0.0, 1.0)
    peak_t = min(max(float(peak_t), 0.05), 0.95)
    bow_x = (t / peak_t) * ((1.0 - t) / (1.0 - peak_t)) * bow
    top = margin + bow_x
    content = (72, 48, 36)
    yy = np.arange(height)[:, None]
    inside = (
        (yy >= top[None, :])
        & (yy <= height - 1 - margin)
        & (xs[None, :] >= margin)
        & (xs[None, :] <= width - 1 - margin)
    )
    pixels[inside] = PRINT
    if not streak:
        pixels[margin:height - margin, margin:width - margin] = PRINT
        for x in range(margin, width - margin):
            edge = int(np.ceil(top[x]))
            pixels[margin:min(edge, height - margin), x] = content
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


def synthetic_side_streaks(width=3000, height=2200, margin=80, left=25, right=10):
    """Straight top/bottom, inward parabolic PAGE streaks on left and right.

    Streak depth is 0 px at the corners and `left`/`right` px at the middle.
    Locked corners sit on the outer rectangle so the warp includes both streaks.
    """
    pixels = np.zeros((height, width, 3), np.uint8)
    pixels[:] = PAGE
    xs = np.arange(width)
    ys = np.arange(height)
    xx, yy = np.meshgrid(xs, ys)
    inner_h = max(1, height - 2 * margin)
    t = np.clip((yy - margin) / inner_h, 0.0, 1.0)
    bow_y = 4.0 * t * (1.0 - t)
    left_edge = margin + bow_y * left
    right_edge = width - 1 - margin - bow_y * right
    inside = (yy >= margin) & (yy <= height - 1 - margin) & (xx >= left_edge) & (xx <= right_edge)
    pixels[inside] = PRINT
    corners = [
        [float(margin), float(margin)],
        [float(width - 1 - margin), float(margin)],
        [float(width - 1 - margin), float(height - 1 - margin)],
        [float(margin), float(height - 1 - margin)],
    ]
    return Image.fromarray(pixels), corners


# Pale sky that resembles the scanner bed but is still a print-edge colour.
SKY = (214, 198, 170)


def synthetic_bowed_pale_sky(width=2000, height=1500, margin=140, bow=8):
    """Bowed top edge whose print-side band is pale sky the colour of the bed."""
    image, corners = synthetic_curved_rectangle(
        width=width, height=height, margin=margin, bow=bow, sides=('top',),
    )
    pixels = np.asarray(image).copy()
    xs = np.arange(width)
    inner_w = max(1, width - 2 * margin)
    t = np.clip((xs - margin) / inner_w, 0.0, 1.0)
    top = margin + 4.0 * t * (1.0 - t) * bow
    sky_depth = max(24, int(round(bow * 3)))
    for x in range(margin, width - margin):
        edge = int(np.ceil(top[x]))
        lo = min(height - margin, edge + sky_depth)
        pixels[edge:lo, x] = SKY
    return Image.fromarray(pixels), corners


def legacy_warp(image, points, rotation=0, aspect_ratio=None):
    """Homography-only warp as of the frozen crop-speed head (no bowed remap)."""
    from server import order_corners
    corners = order_corners(points)
    width, height = image.size
    top, right, bottom, left = [np.linalg.norm(corners[i] - corners[(i + 1) % 4]) for i in range(4)]
    out_width = max(1, int(round(max(top, bottom))))
    out_height = max(1, int(round(max(left, right))))
    if aspect_ratio is not None:
        aspect_ratio = float(aspect_ratio)
        if out_width / out_height < aspect_ratio:
            out_width = max(out_width, int(round(out_height * aspect_ratio)))
        else:
            out_height = max(out_height, int(round(out_width / aspect_ratio)))
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
    return output


def mid_strip_mean(image, side='top', depth=8, span=16):
    pixels = np.asarray(image)
    height, width = pixels.shape[:2]
    mid = width // 2 if side in {'top', 'bottom'} else height // 2
    lo, hi = max(0, mid - span), min(width if side in {'top', 'bottom'} else height, mid + span)
    if side == 'top':
        return pixels[:depth, lo:hi].mean(axis=(0, 1))
    if side == 'bottom':
        return pixels[height - depth:, lo:hi].mean(axis=(0, 1))
    if side == 'left':
        return pixels[lo:hi, :depth].mean(axis=(0, 1))
    return pixels[lo:hi, width - depth:].mean(axis=(0, 1))


def closer_to_print(rgb) -> bool:
    rgb = np.asarray(rgb, dtype=np.float64)
    print_dist = np.linalg.norm(rgb - PRINT)
    page_dist = np.linalg.norm(rgb - PAGE)
    return print_dist + 12 < page_dist


def print_mask(image) -> np.ndarray:
    pixels = np.asarray(image).astype(np.float64)
    print_d = np.linalg.norm(pixels - PRINT, axis=-1)
    page_d = np.linalg.norm(pixels - PAGE, axis=-1)
    return print_d + 12 < page_d


def pixel_area_loss(off, on) -> float:
    off_m = print_mask(off)
    on_m = print_mask(on)
    lost = off_m & ~on_m
    return float(lost.sum()) / float(off_m.size)


def pixel_area_gained(off, on) -> float:
    """Fraction of the crop that was PAGE in OFF and PRINT in ON (filled sag)."""
    off_m = print_mask(off)
    on_m = print_mask(on)
    gained = (~off_m) & on_m
    return float(gained.sum()) / float(off_m.size)


def lab_delta_e(a, b) -> float:
    d_l = (float(a[0]) - float(b[0])) * (100.0 / 255.0)
    d_a = float(a[1]) - float(b[1])
    d_b = float(a[2]) - float(b[2])
    return float(np.sqrt(d_l * d_l + d_a * d_a + d_b * d_b))


def residual_nonprint_depth(image, side='top', samples=48) -> float:
    pixels = np.asarray(image)
    height, width = pixels.shape[:2]
    print_c = np.array(PRINT, dtype=np.float64)
    page_c = np.array(PAGE, dtype=np.float64)
    hits = []
    if side in {'top', 'bottom'}:
        xs = np.linspace(width * 0.2, width * 0.8, samples).astype(int)
        rows = range(height) if side == 'top' else range(height - 1, -1, -1)
        for x in xs:
            found = height if side == 'top' else height
            for i, y in enumerate(rows):
                pix = pixels[y, x].astype(np.float64)
                if np.linalg.norm(pix - print_c) + 12 < np.linalg.norm(pix - page_c):
                    found = i
                    break
            hits.append(float(found))
    else:
        ys = np.linspace(height * 0.2, height * 0.8, samples).astype(int)
        cols = range(width) if side == 'left' else range(width - 1, -1, -1)
        for y in ys:
            found = width
            for i, x in enumerate(cols):
                pix = pixels[y, x].astype(np.float64)
                if np.linalg.norm(pix - print_c) + 12 < np.linalg.norm(pix - page_c):
                    found = i
                    break
            hits.append(float(found))
    return float(np.max(hits)) if hits else 0.0


def source_background(image, corners):
    return bowed_edges.scanner_background_lab(image, np.asarray(corners, dtype=np.float64))


class BowedEdgeTests(unittest.TestCase):
    def test_documented_caps(self):
        self.assertEqual(bowed_edges.DEPTH_CAP_FRAC, 0.03)
        self.assertEqual(bowed_edges.DISPLACEMENT_CAP_FRAC, 0.02)
        self.assertEqual(bowed_edges.DISPLACEMENT_BACKGROUND_FRAC, 0.03)
        self.assertEqual(bowed_edges.AREA_LOSS_FRAC, 0.005)
        self.assertEqual(bowed_edges.GEOMETRIC_LOSS_FRAC, 0.015)
        self.assertEqual(bowed_edges.BACKGROUND_DE, 14.0)
        self.assertEqual(bowed_edges.BACKGROUND_BAND_FRAC, 0.30)
        self.assertEqual(bowed_edges.BOW_DEPTH_FRAC, 0.002)
        self.assertEqual(bowed_edges.BOW_DEPTH_PX, 3.0)
        self.assertEqual(bowed_edges.BAND_TRACK_CORR, 0.75)
        self.assertEqual(bowed_edges.SPLINE_CONTROLS, 5)
        self.assertEqual(bowed_edges.EARLY_MAX, 960)
        self.assertEqual(bowed_edges.REFINE_MID_MAX, 2000)
        self.assertEqual(bowed_edges.DETECT_MAX, 800)
        self.assertEqual(bowed_edges.POOR_RESIDUAL_PX, 2.5)
        self.assertFalse(hasattr(bowed_edges, 'LINEAR_CAP_FRAC'))
        self.assertEqual(bowed_edges.BACKGROUND_MAX, 400)
        self.assertFalse(hasattr(bowed_edges, 'TRIM_CAP_FRAC'))
        self.assertAlmostEqual(bowed_edges._depth_gate(1500), 3.0)
        self.assertAlmostEqual(bowed_edges._depth_gate(4500), 9.0)

    def test_default_on_clears_inward_bow_page_margin(self):
        image, corners = synthetic_curved_rectangle(
            width=1200, height=900, margin=80, bow=8, sides=('top',),
        )
        result = warp(image, corners)
        self.assertTrue(closer_to_print(mid_strip_mean(result, 'top')))
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertFalse(closer_to_print(mid_strip_mean(off, 'top')))
        self.assertLessEqual(pixel_area_loss(off, result), bowed_edges.AREA_LOSS_FRAC)

    def test_off_matches_legacy_homography_bytes(self):
        image, corners = synthetic_curved_rectangle(width=1200, height=900, margin=80, bow=8, sides=('top',))
        off = warp(image, corners, straighten_bowed_edges=False)
        legacy = legacy_warp(image, corners)
        self.assertEqual(off.size, legacy.size)
        self.assertEqual(off.tobytes(), legacy.tobytes())
        image2, corners2 = synthetic_curved_rectangle(width=400, height=500, inward=False)
        self.assertEqual(
            warp(image2, corners2, 90, straighten_bowed_edges=False).tobytes(),
            legacy_warp(image2, corners2, 90).tobytes(),
        )

    def test_no_qualify_is_byte_identical_to_off(self):
        image, corners = synthetic_curved_rectangle(
            width=1200, height=900, margin=80, bow=2, sides=('top',),
        )
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(on.tobytes(), off.tobytes())
        self.assertLessEqual(pixel_area_loss(off, on), 0.001)
        self.assertIs(bowed_edges.straighten_bowed_print(off), off)

    def _preview_vs_save(self, width, height, margin, bow):
        full, corners = synthetic_curved_rectangle(
            width=width, height=height, margin=margin, bow=bow, sides=('top',),
        )
        preview = crop_cache._preview_resize(full)
        scaled = [
            [x * preview.width / full.width, y * preview.height / full.height]
            for x, y in corners
        ]
        full_off = warp(full, corners, straighten_bowed_edges=False)
        preview_off = warp(preview, scaled, straighten_bowed_edges=False)
        bg = source_background(full, corners)
        save_curves = bowed_edges.detect_edge_curves(full_off, bg)
        preview_curves = bowed_edges.scale_curves(save_curves, full_off.size, preview_off.size)
        previewed = warp(
            preview, scaled, straighten_bowed_edges=True,
            curve_source=full, curve_points=corners,
        )
        saved = warp(full, corners, straighten_bowed_edges=True)
        saved_small = saved.resize(previewed.size, Image.Resampling.LANCZOS)
        diff = np.abs(np.asarray(previewed, dtype=np.int16) - np.asarray(saved_small, dtype=np.int16))
        previewed_up = previewed.resize(saved.size, Image.Resampling.LANCZOS)
        print_c = np.array(PRINT, dtype=np.float64)
        page_c = np.array(PAGE, dtype=np.float64)

        def edge_pos(image, side, samples=48):
            pixels = np.asarray(image)
            height, width = pixels.shape[:2]
            hits = []
            if side in {'top', 'bottom'}:
                xs = np.linspace(width * 0.08, width * 0.92, samples).astype(int)
                rows = range(height) if side == 'top' else range(height - 1, -1, -1)
                for x in xs:
                    found = 0 if side == 'top' else height - 1
                    for y in rows:
                        pix = pixels[y, x].astype(np.float64)
                        if np.linalg.norm(pix - print_c) + 12 < np.linalg.norm(pix - page_c):
                            found = y
                            break
                    hits.append(float(found))
            else:
                ys = np.linspace(height * 0.08, height * 0.92, samples).astype(int)
                cols = range(width) if side == 'left' else range(width - 1, -1, -1)
                for y in ys:
                    found = 0 if side == 'left' else width - 1
                    for x in cols:
                        pix = pixels[y, x].astype(np.float64)
                        if np.linalg.norm(pix - print_c) + 12 < np.linalg.norm(pix - page_c):
                            found = x
                            break
                    hits.append(float(found))
            return np.array(hits, dtype=np.float64)

        corner_preview = np.array([
            [edge_pos(previewed_up, 'left')[0], edge_pos(previewed_up, 'top')[0]],
            [edge_pos(previewed_up, 'right')[0], edge_pos(previewed_up, 'top')[-1]],
            [edge_pos(previewed_up, 'right')[-1], edge_pos(previewed_up, 'bottom')[-1]],
            [edge_pos(previewed_up, 'left')[-1], edge_pos(previewed_up, 'bottom')[0]],
        ])
        corner_saved = np.array([
            [edge_pos(saved, 'left')[0], edge_pos(saved, 'top')[0]],
            [edge_pos(saved, 'right')[0], edge_pos(saved, 'top')[-1]],
            [edge_pos(saved, 'right')[-1], edge_pos(saved, 'bottom')[-1]],
            [edge_pos(saved, 'left')[-1], edge_pos(saved, 'bottom')[0]],
        ])
        axis_d = np.abs(corner_preview - corner_saved)
        corner_euclid_save = float(np.linalg.norm(axis_d, axis=1).max())
        corner_axis_save = float(axis_d.max())
        curve_d = np.concatenate([
            np.abs(edge_pos(previewed_up, side) - edge_pos(saved, side))
            for side in ('top', 'bottom', 'left', 'right')
        ])
        scale = saved.size[0] / max(previewed.size[0], 1)
        curve_save = float(np.percentile(curve_d, 95))
        return {
            'previewed': previewed,
            'saved': saved,
            'mae': float(diff.mean()),
            'scale': float(scale),
            'corner_euclid_save': corner_euclid_save,
            'corner_save': corner_axis_save,
            'curve_save': curve_save,
            'corner_euclid_preview': corner_euclid_save / scale,
            'corner_preview': corner_axis_save / scale,
            'curve_preview': curve_save / scale,
            'save_modes': dict(save_curves['modes']),
            'preview_modes': dict(preview_curves['modes']),
        }

    def _assert_preview_vs_save(self, width, height, margin, bow):
        pair = self._preview_vs_save(width, height, margin, bow)
        self.assertEqual(pair['save_modes'], pair['preview_modes'])
        self.assertTrue(closer_to_print(mid_strip_mean(pair['previewed'], 'top')))
        self.assertTrue(closer_to_print(mid_strip_mean(pair['saved'], 'top')))
        # Bounds are in preview pixels: MAE < 0.5, corner ≤ 1.0, curve p95 ≤ 1.0.
        self.assertLess(pair['mae'], 0.5)
        self.assertLessEqual(
            pair['corner_preview'], 1.0,
            msg=(
                f"axis save={pair['corner_save']:.3f} preview={pair['corner_preview']:.3f} "
                f"euclid save={pair['corner_euclid_save']:.3f} preview={pair['corner_euclid_preview']:.3f} "
                f"scale={pair['scale']:.3f}"
            ),
        )
        self.assertLessEqual(
            pair['curve_preview'], 1.0,
            msg=f"save={pair['curve_save']:.3f} preview={pair['curve_preview']:.3f} scale={pair['scale']:.3f}",
        )
        return pair

    def test_preview_path_matches_saved_output(self):
        for width, height, margin, bow in (QUALIFY_1200, QUALIFY_2000, QUALIFY_4500):
            with self.subTest(size=f'{width}x{height}'):
                self._assert_preview_vs_save(width, height, margin, bow)

    def test_preview_and_save_same_decision_on_shallow_4500_bow(self):
        """11 px bow at 4500 is above the 0.2%/3 px gate: one decision, both remap."""
        pair = self._assert_preview_vs_save(*QUALIFY_4500_SHALLOW)
        self.assertEqual(pair['save_modes']['top'], 'curve')

    def test_preview_and_save_share_scaled_curve_params(self):
        """Preview applies the save curve, scaled, not a 1400-only refit."""
        for width, height, margin, bow in (QUALIFY_1200, QUALIFY_2000, QUALIFY_4500, QUALIFY_4500_SHALLOW):
            with self.subTest(size=f'{width}x{height} bow={bow}'):
                full, corners = synthetic_curved_rectangle(
                    width=width, height=height, margin=margin, bow=bow, sides=('top',),
                )
                preview = crop_cache._preview_resize(full)
                scaled = [
                    [x * preview.width / full.width, y * preview.height / full.height]
                    for x, y in corners
                ]
                full_off = warp(full, corners, straighten_bowed_edges=False)
                preview_off = warp(preview, scaled, straighten_bowed_edges=False)
                bg = source_background(full, corners)
                save_curves = bowed_edges.detect_edge_curves(full_off, bg)
                preview_curves = bowed_edges.scale_curves(save_curves, full_off.size, preview_off.size)
                self.assertEqual(save_curves['modes'], preview_curves['modes'])
                self.assertEqual(preview_curves['top'].shape[0], preview_off.size[0])
                self.assertEqual(preview_curves['left'].shape[0], preview_off.size[1])
                self.assertEqual(save_curves['top'].shape[0], full_off.size[0])
                if save_curves['modes']['top'] == 'curve' and full_off.size != preview_off.size:
                    sy = preview_off.size[1] / full_off.size[1]
                    self.assertAlmostEqual(
                        float(preview_curves['top'].max()),
                        float(save_curves['top'].max()) * sy,
                        places=4,
                    )
                applied = bowed_edges.apply_curves(preview_off, preview_curves)
                via_warp = warp(
                    preview, scaled, straighten_bowed_edges=True,
                    curve_source=full, curve_points=corners,
                )
                self.assertEqual(applied.tobytes(), via_warp.tobytes())
                saved = warp(full, corners, straighten_bowed_edges=True)
                self.assertEqual(bowed_edges.apply_curves(full_off, save_curves).tobytes(), saved.tobytes())

    def test_displacement_cap_skips_edge(self):
        """PAGE sag between 2% and 3% remaps; sag above 3% skips."""
        image, corners = synthetic_curved_rectangle(
            width=400, height=1400, margin=50, bow=8, sides=('top',),
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        self.assertGreater(8, bowed_edges.DISPLACEMENT_CAP_FRAC * off.width)
        self.assertLessEqual(8, bowed_edges.DISPLACEMENT_BACKGROUND_FRAC * off.width)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(curves['modes']['top'], 'curve')
        self.assertNotEqual(on.tobytes(), off.tobytes())

        over, over_corners = synthetic_curved_rectangle(
            width=400, height=1400, margin=50, bow=12, sides=('top',),
        )
        over_off = warp(over, over_corners, straighten_bowed_edges=False)
        over_on = warp(over, over_corners, straighten_bowed_edges=True)
        self.assertGreater(12, bowed_edges.DISPLACEMENT_BACKGROUND_FRAC * over_off.width)
        over_curves = bowed_edges.detect_edge_curves(over_off, source_background(over, over_corners))
        self.assertEqual(over_curves['modes']['top'], 'none')
        self.assertEqual(over_on.tobytes(), over_off.tobytes())

        wide, wide_corners = synthetic_curved_rectangle(
            width=1200, height=1400, margin=50, bow=8, sides=('top',),
        )
        wide_off = warp(wide, wide_corners, straighten_bowed_edges=False)
        wide_on = warp(wide, wide_corners, straighten_bowed_edges=True)
        self.assertLessEqual(8, bowed_edges.DISPLACEMENT_CAP_FRAC * wide_off.width)
        self.assertTrue(closer_to_print(mid_strip_mean(wide_on, 'top')))
        self.assertNotEqual(wide_on.tobytes(), wide_off.tobytes())

    def test_poor_fit_is_straight_even_with_background(self):
        image, corners = synthetic_ragged_rectangle()
        warped = warp(image, corners, straighten_bowed_edges=False)
        bg = source_background(image, corners)
        self.assertIsNotNone(bg)
        curves = bowed_edges.detect_edge_curves(warped, bg)
        self.assertEqual(curves['modes']['top'], 'none')
        self.assertEqual(float(curves['top'].max()), 0.0)
        result = warp(image, corners)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(result.tobytes(), off.tobytes())

    def test_pincushion_stays_straight(self):
        image, corners = synthetic_curved_rectangle(
            width=1200, height=900, margin=80, bow=4.5, inward=False, sides=('top',),
        )
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(on.size, off.size)
        self.assertEqual(on.tobytes(), off.tobytes())

    def test_false_positives_are_byte_identical_to_off(self):
        for kind in ('stripes', 'blocks', 'horizon', 'sky'):
            with self.subTest(kind=kind):
                image, corners = synthetic_content_to_edges(kind)
                on = warp(image, corners, straighten_bowed_edges=True)
                off = warp(image, corners, straighten_bowed_edges=False)
                self.assertEqual(on.tobytes(), off.tobytes())
                self.assertLessEqual(pixel_area_loss(off, on), 0.001)
                bg = source_background(image, corners)
                curves = bowed_edges.detect_edge_curves(off, bg)
                self.assertEqual(set(curves['modes'].values()), {'none'})

    def test_straight_print_is_byte_identical_to_off(self):
        image, corners = synthetic_straight_print()
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(on.tobytes(), off.tobytes())
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(set(curves['modes'].values()), {'none'})

    def test_uniform_strip_is_byte_identical_to_off(self):
        for inset in (3, 5, 6):
            with self.subTest(inset=inset):
                image, corners = synthetic_uniform_strip(inset=inset)
                on = warp(image, corners, straighten_bowed_edges=True)
                off = warp(image, corners, straighten_bowed_edges=False)
                self.assertEqual(on.tobytes(), off.tobytes())
                bg = source_background(image, corners)
                curves = bowed_edges.detect_edge_curves(off, bg)
                self.assertEqual(set(curves['modes'].values()), {'none'})

    def test_linear_wedge_is_byte_identical_to_off(self):
        image, corners = synthetic_linear_wedge(wedge=8)
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(on.tobytes(), off.tobytes())
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(set(curves['modes'].values()), {'none'})

    def test_print_area_budget_drops_largest_edges(self):
        """20 px bow on all 4 edges: drop until content ≤ 0.5% and geometric ≤ 1.5%."""
        image, corners = synthetic_curved_rectangle(
            width=4000, height=3000, margin=200, bow=20,
            sides=('top', 'bottom', 'left', 'right'),
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertLessEqual(curves['content_loss'], bowed_edges.AREA_LOSS_FRAC)
        self.assertLessEqual(curves['geometric_loss'], bowed_edges.GEOMETRIC_LOSS_FRAC)
        gained = pixel_area_gained(off, on)
        self.assertAlmostEqual(gained, curves['geometric_loss'], delta=0.002)
        remaining = [side for side, mode in curves['modes'].items() if mode == 'curve']
        self.assertTrue(remaining)
        self._four_edge_report = {
            'modes': dict(curves['modes']),
            'remaining': remaining,
            'content_loss': float(curves['content_loss']),
            'geometric_loss': float(curves['geometric_loss']),
            'gained': gained,
        }

    def test_tapered_side_streaks_respect_caps(self):
        """3000×2200: left ~25 px / right ~10 px streaks, 0 px at the corners."""
        image, corners = synthetic_side_streaks(left=25, right=10)
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertLessEqual(curves['content_loss'], bowed_edges.AREA_LOSS_FRAC)
        self.assertLessEqual(curves['geometric_loss'], bowed_edges.GEOMETRIC_LOSS_FRAC)
        self.assertEqual(curves['modes']['left'], 'curve')
        self.assertEqual(curves['modes']['right'], 'curve')
        left_peak = float(curves['left'].max())
        right_peak = float(curves['right'].max())
        self.assertLessEqual(left_peak, bowed_edges.DISPLACEMENT_CAP_FRAC * off.height)
        self.assertLessEqual(right_peak, bowed_edges.DISPLACEMENT_CAP_FRAC * off.height)
        self.assertLessEqual(left_peak, bowed_edges.DEPTH_CAP_FRAC * off.height)
        self.assertLessEqual(right_peak, bowed_edges.DEPTH_CAP_FRAC * off.height)
        self.assertLessEqual(residual_nonprint_depth(on, 'left'), 2.0)
        self.assertLessEqual(residual_nonprint_depth(on, 'right'), 2.0)
        self._side_streak_report = {
            'modes': dict(curves['modes']),
            'content_loss': float(curves['content_loss']),
            'geometric_loss': float(curves['geometric_loss']),
            'left_peak': left_peak,
            'right_peak': right_peak,
            'left_residual': residual_nonprint_depth(on, 'left'),
            'right_residual': residual_nonprint_depth(on, 'right'),
            'gained': pixel_area_gained(off, on),
        }

    def test_pale_sky_bow_stays_within_content_budget(self):
        """Pale sky the colour of the bed at a bowed edge: content ≤ 0.5%, geometric ≤ 1.5%."""
        image, corners = synthetic_bowed_pale_sky()
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        self.assertIsNotNone(bg)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertLessEqual(curves['content_loss'], bowed_edges.AREA_LOSS_FRAC)
        self.assertLessEqual(curves['geometric_loss'], bowed_edges.GEOMETRIC_LOSS_FRAC)
        self.assertEqual(curves['modes']['top'], 'curve')
        self.assertNotEqual(on.tobytes(), off.tobytes())
        on_top = np.asarray(mid_strip_mean(on, 'top'), dtype=np.float64)
        off_top = np.asarray(mid_strip_mean(off, 'top'), dtype=np.float64)
        sky = np.asarray(SKY, dtype=np.float64)
        page = np.asarray(PAGE, dtype=np.float64)
        self.assertLess(np.linalg.norm(on_top - sky), np.linalg.norm(off_top - sky))
        self.assertLess(np.linalg.norm(on_top - sky), np.linalg.norm(on_top - page) + 8)

    def test_background_lab_ring_matches_dense(self):
        image, corners = synthetic_curved_rectangle(
            width=2000, height=1500, margin=140, bow=8, sides=('top',),
        )
        rgb = np.asarray(image)
        pts = np.asarray(corners, dtype=np.float64)
        ring = bowed_edges.scanner_background_lab(image, pts, rgb=rgb)
        dense = bowed_edges._downsampled_outside_median(rgb, pts)
        self.assertIsNotNone(ring)
        self.assertIsNotNone(dense)
        self.assertLess(lab_delta_e(ring, dense), 2.0)

    def test_streak_residual_at_most_two_pixels(self):
        image, corners = synthetic_bowed_with_streak()
        saved = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertTrue(closer_to_print(mid_strip_mean(saved, 'top')))
        self.assertLessEqual(residual_nonprint_depth(saved, 'top'), 2.0)
        self.assertLessEqual(pixel_area_loss(off, saved), bowed_edges.AREA_LOSS_FRAC)

    def test_straight_fixtures_lose_almost_no_area(self):
        image, corners = synthetic_content_to_edges('blocks', width=1200, height=900, margin=80)
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertLessEqual(pixel_area_loss(off, on), 0.001)

    def test_edge_strip_checks_stay_under_budget(self):
        image, corners = synthetic_curved_rectangle(
            width=3000, height=2200, margin=180, bow=16, sides=('top',),
        )
        warped = warp(image, corners, straighten_bowed_edges=False)
        bg = source_background(image, corners)
        samples = []
        for _ in range(5):
            samples.append(bowed_edges.detect_edge_curves(warped, bg)['elapsed_s'])
        self.assertLessEqual(sorted(samples)[len(samples) // 2], 0.5)

    def test_prediction_cache_key_omits_bowed_flag(self):
        import crop_cache
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as raw:
            path = Path(raw) / 'synthetic.png'
            Image.new('RGB', (32, 24), PAGE).save(path)
            key = crop_cache.RESULT_CACHE.make_key(path, None)
        self.assertEqual(len(key), 4)
        self.assertNotIn('straighten', str(key).lower())
        self.assertNotIn('bowed', str(key).lower())

    def test_missing_settings_default_on(self):
        self.assertTrue(bowed_edges.enabled({}))
        self.assertTrue(bowed_edges.enabled(None))
        self.assertFalse(bowed_edges.enabled({'straightenBowedEdges': False}))
        self.assertTrue(bowed_edges.enabled({'straightenBowedEdges': False}, {'straighten_bowed_edges': True}))

    def test_band_track_correlation_threshold(self):
        """Documented Pearson gate: band width must track fitted sag."""
        n = 400
        t = np.linspace(0.0, 1.0, n)
        sag = 4.0 * t * (1.0 - t) * 20.0
        self.assertGreaterEqual(bowed_edges._band_track_correlation(sag, sag), bowed_edges.BAND_TRACK_CORR)
        rng = np.random.RandomState(2)
        noise = rng.uniform(0.0, 12.0, size=n)
        self.assertLess(bowed_edges._band_track_correlation(noise, sag), bowed_edges.BAND_TRACK_CORR)

    def test_bow_band_tracks_sag(self):
        image, corners = synthetic_curved_rectangle(
            width=2000, height=1500, margin=140, bow=12, sides=('top',),
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        bg = source_background(image, corners)
        rgb = np.asarray(off)
        lab = bowed_edges._to_lab(rgb)
        raw = bowed_edges._raw_offsets(lab, bg)
        fitted, mode = bowed_edges._fit_quadratic(raw['top'])
        self.assertEqual(mode, 'curve')
        sag = np.clip(bowed_edges._quadratic_sag(fitted), 0.0, None)
        corr = bowed_edges._band_track_correlation(raw['top'], sag)
        self.assertGreaterEqual(corr, bowed_edges.BAND_TRACK_CORR)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(curves['modes']['top'], 'curve')
        self._band_track_report = {'corr': corr, 'mode': curves['modes']['top']}

    def test_noisy_and_picture_edge_do_not_qualify(self):
        noisy, noisy_corners = synthetic_noisy_straight_edge()
        noisy_off = warp(noisy, noisy_corners, straighten_bowed_edges=False)
        noisy_on = warp(noisy, noisy_corners, straighten_bowed_edges=True)
        noisy_bg = source_background(noisy, noisy_corners)
        noisy_curves = bowed_edges.detect_edge_curves(noisy_off, noisy_bg)
        self.assertEqual(noisy_curves['modes']['top'], 'none')
        self.assertEqual(noisy_on.tobytes(), noisy_off.tobytes())

        picture, picture_corners = synthetic_picture_edge()
        picture_off = warp(picture, picture_corners, straighten_bowed_edges=False)
        picture_on = warp(picture, picture_corners, straighten_bowed_edges=True)
        picture_bg = source_background(picture, picture_corners)
        picture_curves = bowed_edges.detect_edge_curves(picture_off, picture_bg)
        self.assertEqual(picture_curves['modes']['top'], 'none')
        self.assertEqual(picture_on.tobytes(), picture_off.tobytes())
        rgb = np.asarray(noisy_off)
        lab = bowed_edges._to_lab(rgb)
        raw = bowed_edges._raw_offsets(lab, noisy_bg)
        fitted, _mode = bowed_edges._fit_quadratic(raw['top'])
        sag = np.clip(bowed_edges._quadratic_sag(fitted), 0.0, None)
        corr = bowed_edges._band_track_correlation(raw['top'], sag)
        self._false_bow_report = {
            'noisy_corr': corr,
            'noisy_modes': dict(noisy_curves['modes']),
            'picture_modes': dict(picture_curves['modes']),
        }

    def test_matching_print_border_is_not_removed(self):
        """Matching-colour print border stays; linear strip removal is not shipped."""
        image, corners = synthetic_matching_print_border()
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        lab = bowed_edges._to_lab(np.asarray(off))
        strip = np.full(off.width, 15.0)
        top_frac = bowed_edges._background_band_fraction(lab, 'top', strip, bg)
        geo, content = bowed_edges._sag_loss_fractions(lab, strip, 'top', bg, off.width, off.height)
        self._border_probe = {
            'modes': dict(curves['modes']),
            'content_loss': float(curves['content_loss']),
            'geometric_loss': float(curves['geometric_loss']),
            'top_background_frac': float(top_frac),
            'top_strip_content': float(content),
            'top_strip_geometric': float(geo),
            'top_residual_on': residual_nonprint_depth(on, 'top'),
            'top_residual_off': residual_nonprint_depth(off, 'top'),
        }
        self.assertGreaterEqual(top_frac, 0.80)
        self.assertLessEqual(content, 0.0005)
        self.assertEqual(set(curves['modes'].values()), {'none'})
        self.assertEqual(on.tobytes(), off.tobytes())

    def test_asymmetric_bow_background_raises_displacement_cap(self):
        """60 px asymmetric bow on ~3000 px: streak qualifies (≤2 px); no streak skips."""
        with_streak, with_corners = synthetic_asymmetric_bow(streak=True)
        with_off = warp(with_streak, with_corners, straighten_bowed_edges=False)
        with_on = warp(with_streak, with_corners, straighten_bowed_edges=True)
        with_bg = source_background(with_streak, with_corners)
        with_curves = bowed_edges.detect_edge_curves(with_off, with_bg)
        side = with_off.width
        self.assertGreater(60, bowed_edges.DISPLACEMENT_CAP_FRAC * side)
        self.assertLessEqual(60, bowed_edges.DISPLACEMENT_BACKGROUND_FRAC * side)
        self.assertEqual(with_curves['modes']['top'], 'curve')
        self.assertLessEqual(residual_nonprint_depth(with_on, 'top'), 2.0)
        self.assertLessEqual(with_curves['content_loss'], bowed_edges.AREA_LOSS_FRAC)

        without, without_corners = synthetic_asymmetric_bow(streak=False)
        without_off = warp(without, without_corners, straighten_bowed_edges=False)
        without_on = warp(without, without_corners, straighten_bowed_edges=True)
        without_bg = source_background(without, without_corners)
        without_curves = bowed_edges.detect_edge_curves(without_off, without_bg)
        self.assertEqual(without_curves['modes']['top'], 'none')
        self.assertEqual(without_on.tobytes(), without_off.tobytes())
        self._asymmetric_report = {
            'with_modes': dict(with_curves['modes']),
            'with_model': dict(with_curves.get('models', {})),
            'with_peak': float(with_curves['top'].max()),
            'with_residual': residual_nonprint_depth(with_on, 'top'),
            'with_content': float(with_curves['content_loss']),
            'without_modes': dict(without_curves['modes']),
            'side': side,
            'cap2': bowed_edges.DISPLACEMENT_CAP_FRAC * side,
            'cap3': bowed_edges.DISPLACEMENT_BACKGROUND_FRAC * side,
        }

    def test_depth_gate_3px_bow_on_1500_remaps(self):
        """3 px bow on a 1500 px side with a bed streak must remap."""
        image, corners = synthetic_curved_rectangle(
            width=1660, height=1200, margin=80, bow=3, sides=('top',),
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        self.assertAlmostEqual(off.width, 1500, delta=2)
        gate = bowed_edges._depth_gate(off.width)
        self.assertAlmostEqual(gate, 3.0, places=3)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(curves['modes']['top'], 'curve')
        self.assertLessEqual(residual_nonprint_depth(on, 'top'), 2.0)
        self.assertNotEqual(on.tobytes(), off.tobytes())

    def test_depth_gate_boundaries_1200_and_4500(self):
        cases = (
            (1200, 900, 80, 2, 'none'),
            (1200, 900, 80, 4, 'curve'),
            (4500, 3400, 280, 6, 'none'),
            (4500, 3400, 280, 10, 'curve'),
        )
        for width, height, margin, bow, expect in cases:
            with self.subTest(size=f'{width}x{height}', bow=bow):
                image, corners = synthetic_curved_rectangle(
                    width=width, height=height, margin=margin, bow=bow, sides=('top',),
                )
                off = warp(image, corners, straighten_bowed_edges=False)
                on = warp(image, corners, straighten_bowed_edges=True)
                bg = source_background(image, corners)
                curves = bowed_edges.detect_edge_curves(off, bg)
                self.assertEqual(curves['modes']['top'], expect)
                if expect == 'none':
                    self.assertEqual(on.tobytes(), off.tobytes())
                else:
                    self.assertNotEqual(on.tobytes(), off.tobytes())
                    self.assertLessEqual(residual_nonprint_depth(on, 'top'), 2.0)

    def test_asymmetric_nonquadratic_bow_at_4500(self):
        """Asymmetric bow at 4500: curved residual ≤ 2 full-res px."""
        image, corners = synthetic_asymmetric_bow(
            width=4500, height=3400, margin=280, bow=40, peak_t=0.35, streak=True,
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(curves['modes']['top'], 'curve')
        self.assertIn(curves['models']['top'], {'quadratic', 'cubic', 'spline'})
        residual = residual_nonprint_depth(on, 'top')
        self.assertLessEqual(residual, 2.0)
        self._asymmetric_4500_report = {
            'model': curves['models']['top'],
            'edges': dict(curves['edges']),
            'residual': residual,
            'content': float(curves['content_loss']),
            'geometric': float(curves['geometric_loss']),
            'peak': float(curves['top'].max()),
        }

    def test_wavy_content_line_does_not_pick_spline(self):
        image, corners = synthetic_wavy_content_line()
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertNotEqual(curves['models']['top'], 'spline')
        self.assertEqual(curves['modes']['top'], 'none')
        self.assertEqual(on.tobytes(), off.tobytes())

    def test_60px_symmetric_and_asymmetric_on_3000(self):
        """60 px bows on a ~3000 px side with a bed streak remap to ≤2 px."""
        for name, factory in (
            ('symmetric', lambda: synthetic_curved_rectangle(
                width=3000, height=3400, margin=80, bow=60, sides=('top',),
            )),
            ('asymmetric', lambda: synthetic_asymmetric_bow(
                width=3000, height=3400, margin=80, bow=60, peak_t=0.35, streak=True,
            )),
        ):
            with self.subTest(shape=name):
                image, corners = factory()
                off = warp(image, corners, straighten_bowed_edges=False)
                on = warp(image, corners, straighten_bowed_edges=True)
                bg = source_background(image, corners)
                curves = bowed_edges.detect_edge_curves(off, bg)
                self.assertGreater(off.width, 2800)
                self.assertLess(off.width, 3200)
                self.assertEqual(curves['modes']['top'], 'curve')
                self.assertLessEqual(residual_nonprint_depth(on, 'top'), 2.0)
                self.assertLessEqual(curves['content_loss'], bowed_edges.AREA_LOSS_FRAC)
                self.assertLessEqual(curves['geometric_loss'], bowed_edges.GEOMETRIC_LOSS_FRAC)

    def test_early_exit_matches_full_path_on_every_fixture(self):
        """Cheap ≤960 skip/continue decision matches the full path on the suite."""
        fixtures = (
            ('straight', lambda: synthetic_straight_print()),
            ('strip3', lambda: synthetic_uniform_strip(inset=3)),
            ('strip5', lambda: synthetic_uniform_strip(inset=5)),
            ('strip6', lambda: synthetic_uniform_strip(inset=6)),
            ('wedge', lambda: synthetic_linear_wedge(wedge=8)),
            ('border', lambda: synthetic_matching_print_border()),
            ('busy-stripes', lambda: synthetic_content_to_edges('stripes')),
            ('busy-blocks', lambda: synthetic_content_to_edges('blocks')),
            ('busy-horizon', lambda: synthetic_content_to_edges('horizon')),
            ('busy-sky', lambda: synthetic_content_to_edges('sky')),
            ('noisy', lambda: synthetic_noisy_straight_edge()),
            ('picture', lambda: synthetic_picture_edge()),
            ('wavy', lambda: synthetic_wavy_content_line()),
            ('bow-1200', lambda: synthetic_curved_rectangle(
                width=1200, height=900, margin=80, bow=8, sides=('top',),
            )),
            ('bow-3px-1500', lambda: synthetic_curved_rectangle(
                width=1660, height=1200, margin=80, bow=3, sides=('top',),
            )),
            ('bow-2000', lambda: synthetic_curved_rectangle(
                width=2000, height=1500, margin=140, bow=12, sides=('top',),
            )),
            ('bow-4500', lambda: synthetic_curved_rectangle(
                width=4500, height=3400, margin=280, bow=18, sides=('top',),
            )),
            ('bow-4500-11', lambda: synthetic_curved_rectangle(
                width=4500, height=3400, margin=280, bow=11, sides=('top',),
            )),
            ('pale-sky', lambda: synthetic_bowed_pale_sky()),
            ('streaks', lambda: synthetic_side_streaks()),
            ('asymmetric-60', lambda: synthetic_asymmetric_bow(streak=True)),
            ('asymmetric-no-streak', lambda: synthetic_asymmetric_bow(streak=False)),
        )
        for name, factory in fixtures:
            with self.subTest(fixture=name):
                image, corners = factory()
                off = warp(image, corners, straighten_bowed_edges=False)
                bg = source_background(image, corners)
                auto = bowed_edges.detect_edge_curves(off, bg)
                full = bowed_edges.detect_edge_curves(off, bg, force_full=True)
                self.assertEqual(auto['modes'], full['modes'], msg=name)
                self.assertEqual(auto['models'], full['models'], msg=name)


if __name__ == '__main__':
    unittest.main()
