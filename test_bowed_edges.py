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


def synthetic_curved_rectangle(width=640, height=480, margin=48, bow=22, inward=True):
    """Axis-aligned rectangle whose edges curve in (bow) or out (pincushion)."""
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
    top = margin + sign * bow_x
    bot = height - 1 - margin - sign * bow_x
    left = margin + sign * bow_y
    right = width - 1 - margin - sign * bow_y
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


class BowedEdgeTests(unittest.TestCase):
    def test_default_on_clears_inward_bow_page_margin(self):
        image, corners = synthetic_curved_rectangle()
        result = warp(image, corners)
        self.assertTrue(closer_to_print(mid_strip_mean(result, 'top')))
        self.assertTrue(closer_to_print(mid_strip_mean(result, 'bottom')))
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertFalse(closer_to_print(mid_strip_mean(off, 'top')))

    def test_off_matches_legacy_homography_bytes(self):
        image, corners = synthetic_curved_rectangle()
        off = warp(image, corners, straighten_bowed_edges=False)
        legacy = legacy_warp(image, corners)
        self.assertEqual(off.size, legacy.size)
        self.assertEqual(off.tobytes(), legacy.tobytes())
        image2, corners2 = synthetic_curved_rectangle(width=400, height=500, inward=False)
        self.assertEqual(
            warp(image2, corners2, 90, straighten_bowed_edges=False).tobytes(),
            legacy_warp(image2, corners2, 90).tobytes(),
        )

    def test_preview_path_matches_saved_output(self):
        full, corners = synthetic_curved_rectangle(width=2000, height=1500, margin=140, bow=48)
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
        self.assertLess(float(diff.mean()), 12.0)

    def test_poor_fit_uses_capped_trim(self):
        image, corners = synthetic_ragged_rectangle()
        warped = warp(image, corners, straighten_bowed_edges=False)
        curves = bowed_edges.detect_edge_curves(warped)
        self.assertEqual(curves['modes']['top'], 'trim')
        cap = bowed_edges.TRIM_CAP_FRAC * warped.height
        self.assertLessEqual(float(curves['top'].max()), cap + 1.0)
        result = warp(image, corners)
        off = warp(image, corners, straighten_bowed_edges=False)
        mean_diff = float(np.abs(np.asarray(result, dtype=np.int16) - np.asarray(off, dtype=np.int16)).mean())
        self.assertGreater(mean_diff, 0.5)

    def test_pincushion_does_not_invent_a_large_trim(self):
        image, corners = synthetic_curved_rectangle(inward=False, bow=16)
        on = warp(image, corners, straighten_bowed_edges=True)
        off = warp(image, corners, straighten_bowed_edges=False)
        self.assertEqual(on.size, off.size)
        diff = np.abs(np.asarray(on, dtype=np.int16) - np.asarray(off, dtype=np.int16))
        self.assertLess(float(diff.mean()), 6.0)

    def test_edge_strip_checks_stay_under_budget(self):
        image, corners = synthetic_curved_rectangle(width=3000, height=2200, margin=180, bow=60)
        warped = warp(image, corners, straighten_bowed_edges=False)
        samples = []
        for _ in range(5):
            samples.append(bowed_edges.detect_edge_curves(warped)['elapsed_s'])
        self.assertLessEqual(sorted(samples)[len(samples) // 2], 0.4)

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
