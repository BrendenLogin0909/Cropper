#!/usr/bin/env python3
"""Synthetic corner-prediction timing harness. No real photos, no paid APIs."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import bowed_edges  # noqa: E402
import crop_cache  # noqa: E402
import crop_detection  # noqa: E402
import server  # noqa: E402
from test_bowed_edges import synthetic_curved_rectangle  # noqa: E402
from test_crop_detection import synthetic_print  # noqa: E402

SIZES = ((1200, 900), (3000, 2200), (4500, 3400))
# Single-edge bows that qualify: ≥ BOW_DEPTH_PX, ≤ 2% displacement, ≤ 0.5% area.
BOW_FIXTURES = {
    (1200, 900): (80, 4.5),
    (3000, 2200): (180, 12),
    (4500, 3400): (280, 18),
}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[index]


def time_ms(fn) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def report(label: str, samples: list[float]) -> str:
    return f'{label:18} p50={percentile(samples, 50):7.1f}  p95={percentile(samples, 95):7.1f}  n={len(samples)}'


def bench_size(width: int, height: int, runs: int, folder: Path) -> None:
    path = folder / f'synthetic_{width}x{height}.jpg'
    synthetic_print(width, height).save(path, format='JPEG', quality=90)
    decode, resize, detect, cold, cached, opens = [], [], [], [], [], []
    preview_off, preview_on, warp_off, warp_on = [], [], [], []
    points = [[width * 0.18, height * 0.16], [width * 0.82, height * 0.16], [width * 0.82, height * 0.84], [width * 0.18, height * 0.84]]
    for _ in range(runs):
        crop_cache.reset()

        def do_decode():
            image, _ = server.load_image(path)
            do_decode.image = image
        decode.append(time_ms(do_decode))
        image = do_decode.image

        def do_resize():
            do_resize.preview = crop_detection.detection_preview(image)
        resize.append(time_ms(do_resize))
        preview = do_resize.preview

        def do_detect():
            crop_detection.predict_corners(preview, original_size=image.size)
        detect.append(time_ms(do_detect))

        crop_cache.reset()

        def do_open():
            opened, _ = server.load_image(path)
            do_open.image = opened
            display = opened
            if max(opened.size) > 7000:
                display = opened.copy()
                display.thumbnail((7000, 7000), Image.Resampling.LANCZOS)
            buf = BytesIO()
            display.save(buf, format='JPEG', quality=88, optimize=True)
        opens.append(time_ms(do_open))
        crop_cache.fill_from_full_image_exclusive(path, do_open.image)
        filled = crop_cache.IMAGE_CACHE.get(path)
        preview_src = filled['preview']
        original_size = filled['original_size']
        scaled = [[x * preview_src.width / original_size[0], y * preview_src.height / original_size[1]] for x, y in points]

        def encode_preview(straighten):
            warped = server.warp(preview_src, scaled, 0, None, straighten)
            buf = BytesIO()
            warped.save(buf, format='JPEG', quality=89, optimize=True)

        preview_off.append(time_ms(lambda: encode_preview(False)))
        preview_on.append(time_ms(lambda: encode_preview(True)))
        warp_off.append(time_ms(lambda: server.warp(image, points, 0, None, False)))
        warp_on.append(time_ms(lambda: server.warp(image, points, 0, None, True)))

        crop_cache.reset()
        cold.append(time_ms(lambda: crop_cache.predict_cached(path, None, foreground=True)))
        cached.append(time_ms(lambda: crop_cache.predict_cached(path, None, foreground=True)))

    print(f'## {width}x{height}')
    print(report('decode', decode))
    print(report('resize-960', resize))
    print(report('detect', detect))
    print(report('open', opens))
    print(report('preview-off', preview_off))
    print(report('preview-on', preview_on))
    print(report('warp-off', warp_off))
    print(report('warp-on', warp_on))
    print(report('server cold', cold))
    print(report('server cached', cached))
    print()


def bench_bowed(width: int, height: int, runs: int) -> None:
    margin, bow = BOW_FIXTURES[(width, height)]
    image, corners = synthetic_curved_rectangle(width, height, margin=margin, bow=bow, sides=('top',))
    warped = server.warp(image, corners, straighten_bowed_edges=False)
    background = bowed_edges.scanner_background_lab(image, corners)
    warp_off, warp_on, strips, remaps = [], [], [], []
    for _ in range(runs):
        warp_off.append(time_ms(lambda: server.warp(image, corners, straighten_bowed_edges=False)))
        warp_on.append(time_ms(lambda: server.warp(image, corners, straighten_bowed_edges=True)))

        def do_strips():
            do_strips.curves = bowed_edges.detect_edge_curves(warped, background)
        strips.append(time_ms(do_strips))
        remaps.append(time_ms(lambda: bowed_edges.straighten_bowed_print(warped, background)))
    print(f'## bowed-edge {width}x{height} (bow={bow}px top)')
    print(report('warp-off', warp_off))
    print(report('warp-on', warp_on))
    print(report('edge-strips', strips))
    print(report('remap', remaps))
    print()


def main() -> int:
    if os.environ.get('OPENAI_API_KEY'):
        print('Refusing to run: OPENAI_API_KEY is set. This harness is local/synthetic only.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description='Synthetic crop-corner timing (no real photos).')
    parser.add_argument('--runs', type=int, default=7, help='repetitions per size')
    args = parser.parse_args()
    print(f'Synthetic crop-corner bench, runs={args.runs}')
    print('Fixtures stay in a temp dir and are deleted afterwards.')
    print('server cold is full-decode predict (bowed flag is not in the cache key).')
    print('preview-off matches 26e94c5 live preview; preview-on adds the conservative warp.')
    print('straight warp-on extra vs off target ≤ +50 ms (no-qualify ≈ zero remap).')
    print('bowed warp-on extra vs off target ≤ +500 ms p95 at 4500 px.\n')
    with tempfile.TemporaryDirectory(prefix='cropper-bench-') as raw:
        folder = Path(raw)
        for width, height in SIZES:
            bench_size(width, height, max(1, args.runs), folder)
        print('## bowed-edge warp (qualifying single-edge bow; skip if no evidence)')
        print()
        for width, height in SIZES:
            bench_bowed(width, height, max(1, args.runs))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
