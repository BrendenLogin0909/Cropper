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

import crop_cache  # noqa: E402
import crop_detection  # noqa: E402
import server  # noqa: E402
from test_crop_detection import synthetic_print  # noqa: E402

SIZES = ((1200, 900), (3000, 2200), (4500, 3400))


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
    decode, resize, detect, cold, cached, opens, previews = [], [], [], [], [], [], []
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

        def do_preview():
            warped = server.warp(preview_src, scaled, 0, None)
            buf = BytesIO()
            warped.save(buf, format='JPEG', quality=89, optimize=True)
        previews.append(time_ms(do_preview))

        crop_cache.reset()
        cold.append(time_ms(lambda: crop_cache.predict_cached(path, None, foreground=True)))
        cached.append(time_ms(lambda: crop_cache.predict_cached(path, None, foreground=True)))

    print(f'## {width}x{height}')
    print(report('decode', decode))
    print(report('resize-960', resize))
    print(report('detect', detect))
    print(report('open', opens))
    print(report('preview-1400', previews))
    print(report('server cold', cold))
    print(report('server cached', cached))
    print()


def main() -> int:
    if os.environ.get('OPENAI_API_KEY'):
        print('Refusing to run: OPENAI_API_KEY is set. This harness is local/synthetic only.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description='Synthetic crop-corner timing (no real photos).')
    parser.add_argument('--runs', type=int, default=7, help='repetitions per size')
    args = parser.parse_args()
    print(f'Synthetic crop-corner bench, runs={args.runs}')
    print('Fixtures stay in a temp dir and are deleted afterwards.\n')
    with tempfile.TemporaryDirectory(prefix='cropper-bench-') as raw:
        folder = Path(raw)
        for width, height in SIZES:
            bench_size(width, height, max(1, args.runs), folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
