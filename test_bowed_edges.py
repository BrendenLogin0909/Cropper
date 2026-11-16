"""Synthetic bowed-edge warp tests. No real photos."""
from __future__ import annotations

import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw

import bowed_edges
from server import warp


PAGE = (203, 185, 154)
PRINT = (37, 72, 140)
# Qualifying single-edge bows: ≥ BOW_DEPTH_PX, ≤ 2% displacement, ≤ 0.5% area.
# Fitted mean is a bit above a 2/3 parabola, so 5 px at 1200×900 trips 0.5%.
QUALIFY_1200 = (1200, 900, 80, 4.5)
QUALIFY_2000 = (2000, 1500, 140, 8)
QUALIFY_4500 = (4500, 3400, 280, 18)


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
        self.assertEqual(bowed_edges.BACKGROUND_DE, 14.0)
        self.assertEqual(bowed_edges.BACKGROUND_BAND_FRAC, 0.30)
        self.assertEqual(bowed_edges.BOW_DEPTH_PX, 4.0)
        self.assertEqual(bowed_edges.BACKGROUND_MAX, 400)
        self.assertFalse(hasattr(bowed_edges, 'TRIM_CAP_FRAC'))

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

    def _assert_preview_vs_save(self, width, height, margin, bow):
        full, corners = synthetic_curved_rectangle(
            width=width, height=height, margin=margin, bow=bow, sides=('top',),
        )
        preview = full.copy()
        preview.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
        scaled = [
            [x * preview.width / full.width, y * preview.height / full.height]
            for x, y in corners
        ]
        previewed = warp(preview, scaled, straighten_bowed_edges=True)
        saved = warp(full, corners, straighten_bowed_edges=True)
        self.assertTrue(closer_to_print(mid_strip_mean(previewed, 'top')))
        self.assertTrue(closer_to_print(mid_strip_mean(saved, 'top')))
        saved_small = saved.resize(previewed.size, Image.Resampling.LANCZOS)
        diff = np.abs(np.asarray(previewed, dtype=np.int16) - np.asarray(saved_small, dtype=np.int16))
        # Measured 1400-cache vs full-res on synthetic single-edge bows (4.5 / 8 / 18):
        # 1200×900 MAE 0.0 / corner 0.0 / curve p95 0.0 (no downscale; 1200 < 1400)
        # 2000×1500 MAE 0.127 / corner max 0.0 / curve p95 1.00
        # 4500×3400 MAE 0.208 / corner max 4.24 / curve p95 3.00
        # Isolated resample spikes exist but do not move the mean. Tolerance is
        # MAE < 0.5 (~2× largest measured), corner max ≤ 6.0 px, curve p95 ≤ 5.0 px
        # (~1.4–1.7× measured on the 4500 fixture).
        self.assertLess(float(diff.mean()), 0.5)
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
        corner_d = np.linalg.norm(corner_preview - corner_saved, axis=1)
        curve_d = np.concatenate([
            np.abs(edge_pos(previewed_up, side) - edge_pos(saved, side))
            for side in ('top', 'bottom', 'left', 'right')
        ])
        self.assertLessEqual(float(corner_d.max()), 6.0)
        self.assertLessEqual(float(np.percentile(curve_d, 95)), 5.0)
        return float(diff.mean()), float(corner_d.max()), float(np.percentile(curve_d, 95))

    def test_preview_path_matches_saved_output(self):
        for width, height, margin, bow in (QUALIFY_1200, QUALIFY_2000, QUALIFY_4500):
            with self.subTest(size=f'{width}x{height}'):
                self._assert_preview_vs_save(width, height, margin, bow)

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
