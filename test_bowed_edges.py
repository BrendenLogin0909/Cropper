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
# Qualifying single-edge bows: ≥ BOW_DEPTH_FRAC, ≤ 2% displacement, ≤ 0.5% area.
# Fitted mean is a bit above a 2/3 parabola, so 5 px at 1200×900 trips 0.5%.
QUALIFY_1200 = (1200, 900, 80, 4.5)
QUALIFY_2000 = (2000, 1500, 140, 8)
QUALIFY_4500 = (4500, 3400, 280, 18)
# 10–12 px at 4500 used to skip on the 1400 preview (3 px < 4 px) while save remapped.
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


def synthetic_bowed_with_streak(width=1200, height=900, margin=80, bow=4.5, streak=2):
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


def synthetic_linear_wedge(width=1200, height=900, margin=80, wedge=8):
    """Straight print whose top PAGE depth goes 0 → `wedge` px left to right."""
    image, corners = synthetic_straight_print(width, height, margin)
    pixels = np.asarray(image).copy()
    inner_w = max(1, width - 2 * margin)
    for x in range(margin, width - margin):
        depth = int(round(wedge * (x - margin) / max(inner_w - 1, 1)))
        if depth > 0:
            pixels[margin:margin + depth, x] = PAGE
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
        self.assertEqual(bowed_edges.AREA_LOSS_FRAC, 0.005)
        self.assertEqual(bowed_edges.GEOMETRIC_LOSS_FRAC, 0.015)
        self.assertEqual(bowed_edges.BACKGROUND_DE, 14.0)
        self.assertEqual(bowed_edges.BACKGROUND_BAND_FRAC, 0.30)
        self.assertEqual(bowed_edges.BOW_DEPTH_FRAC, 0.002)
        self.assertEqual(bowed_edges.BACKGROUND_MAX, 400)
        self.assertFalse(hasattr(bowed_edges, 'TRIM_CAP_FRAC'))
        self.assertFalse(hasattr(bowed_edges, 'BOW_DEPTH_PX'))

    def test_default_on_clears_inward_bow_page_margin(self):
        image, corners = synthetic_curved_rectangle(
            width=1200, height=900, margin=80, bow=4.5, sides=('top',),
        )
        result = warp(image, corners)
        self.assertTrue(closer_to_print(mid_strip_mean(result, 'top')))
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertFalse(closer_to_print(mid_strip_mean(off, 'top')))
        self.assertLessEqual(pixel_area_loss(off, result), bowed_edges.AREA_LOSS_FRAC)

    def test_off_matches_legacy_homography_bytes(self):
        image, corners = synthetic_curved_rectangle(width=1200, height=900, margin=80, bow=4.5, sides=('top',))
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
        """10–12 px bow at 4500: one full-res decision, both paths remap."""
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
        """Skip when peak > DISPLACEMENT_CAP_FRAC of the side (2%), even if depth < 3%."""
        # Narrow crop: 2% of width ≈ 6 px, bow=8 exceeds that and stays under 3% and 0.5% area.
        image, corners = synthetic_curved_rectangle(
            width=400, height=1400, margin=50, bow=8, sides=('top',),
        )
        off = warp(image, corners, straighten_bowed_edges=False)
        on = warp(image, corners, straighten_bowed_edges=True)
        self.assertGreater(8, bowed_edges.DISPLACEMENT_CAP_FRAC * off.width)
        self.assertLessEqual(8, bowed_edges.DEPTH_CAP_FRAC * off.width)
        bg = source_background(image, corners)
        curves = bowed_edges.detect_edge_curves(off, bg)
        self.assertEqual(curves['modes']['top'], 'none')
        self.assertEqual(on.tobytes(), off.tobytes())

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
            width=3000, height=2200, margin=180, bow=12, sides=('top',),
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


if __name__ == '__main__':
    unittest.main()
