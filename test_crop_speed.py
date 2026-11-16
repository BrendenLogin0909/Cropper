import json
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

import app_settings
import crop_cache
import crop_detection
import server
from test_crop_detection import synthetic_print


class CropSpeedTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        app_settings.configure_runtime(self.root)
        self.addCleanup(lambda: setattr(app_settings, '_data_dir', None))
        crop_cache.reset()
        self.http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        server.reset_tabs()
        server.reset_confirmations()
        self.tab = 'tabid-speed-000001'
        server.register_tab(self.tab)
        with patch.object(server, 'choose_folder_dialog', return_value=self.root):
            self.post('/api/pick-folder', {})

    def stop_server(self):
        self.http.shutdown()
        self.http.server_close()
        crop_cache.reset()

    def post(self, endpoint, body):
        status, payload = self.post_status(endpoint, body)
        if status >= 400:
            raise AssertionError(f'{endpoint} -> {status}: {payload}')
        return payload

    def post_status(self, endpoint, body):
        request = Request(
            self.base + endpoint,
            json.dumps(body).encode(),
            {'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': self.tab, 'Content-Type': 'application/json'},
        )
        try:
            with urlopen(request) as response:
                payload = json.load(response) if 'json' in response.headers.get('Content-Type', '') else response.read()
                return response.status, payload
        except HTTPError as exc:
            payload = json.loads(exc.read().decode())
            exc.close()
            return exc.code, payload

    def get(self, endpoint):
        request = Request(self.base + endpoint, headers={'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': self.tab})
        with urlopen(request) as response:
            return response.read()

    def png_source(self, name='photo.png', size=(800, 600)):
        path = self.root / name
        image = Image.new('RGB', size, '#cbb99a')
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 70, size[0] - 80, size[1] - 70), fill='#254080')
        image.save(path)
        return path

    def jpeg_source(self, name, size):
        path = self.root / name
        synthetic_print(*size).save(path, format='JPEG', quality=92)
        return path

    def test_png_cache_hit_matches_full_decode_corners(self):
        path = self.png_source()
        full, _ = server.load_image(path)
        expected = crop_detection.predict_corners(full)['candidates']
        self.get(f'/api/image?path={path}')
        self.assertGreater(len(crop_cache.IMAGE_CACHE), 0)
        result = self.post('/api/crop-corners', {'path': str(path)})
        self.assertEqual(result['candidates'], expected)
        self.assertEqual(len(crop_cache.RESULT_CACHE), 1)

    def test_result_lru_invalidates_when_size_changes(self):
        path = self.png_source()
        first = self.post('/api/crop-corners', {'path': str(path)})
        self.assertEqual(len(crop_cache.RESULT_CACHE), 1)
        Image.new('RGB', (640, 480), 'navy').save(path)
        second = self.post('/api/crop-corners', {'path': str(path)})
        self.assertEqual(len(crop_cache.RESULT_CACHE), 1)
        self.assertNotEqual(first['candidates'], second['candidates'])

    def test_draft_decode_used_only_for_large_jpeg(self):
        small = self.jpeg_source('small.jpg', (1200, 900))
        large = self.jpeg_source('large.jpg', (3000, 2200))
        png = self.png_source('plain.png')
        crop_cache.reset()
        _, _, used_small = crop_cache.decode_detection_image(small, allow_draft=True)
        crop_cache.reset()
        detect, original, used_large = crop_cache.decode_detection_image(large, allow_draft=True)
        crop_cache.reset()
        _, _, used_png = crop_cache.decode_detection_image(png, allow_draft=True)
        self.assertFalse(used_small)
        self.assertFalse(used_png)
        self.assertTrue(used_large)
        self.assertEqual(original, (3000, 2200))
        self.assertLessEqual(max(detect.size), crop_detection.DETECTION_SIZE)

    def test_draft_corners_stay_within_bound_of_full_decode(self):
        path = self.jpeg_source('draft.jpg', (3000, 2200))
        full, _ = server.load_image(path)
        full_result = crop_detection.predict_corners(full)
        crop_cache.reset()
        detect, original, used = crop_cache.decode_detection_image(path, allow_draft=True)
        self.assertTrue(used)
        draft_result = crop_detection.predict_corners(detect, original_size=original)
        self.assertTrue(full_result['candidates'])
        self.assertTrue(draft_result['candidates'])
        diffs = []
        for full_pt, draft_pt in zip(full_result['candidates'][0]['points'], draft_result['candidates'][0]['points']):
            diffs.append(((full_pt[0] - draft_pt[0]) ** 2 + (full_pt[1] - draft_pt[1]) ** 2) ** 0.5)
        diffs.sort()
        self.assertLessEqual(diffs[len(diffs) // 2], 5.0)
        self.assertLessEqual(diffs[-1], 20.0)

    def test_save_still_loads_full_resolution(self):
        path = self.jpeg_source('save.jpg', (3000, 2200))
        result = self.post('/api/crop', {
            'path': str(path),
            'points': [[0, 0], [2999, 0], [2999, 2199], [0, 2199]],
            'rotation': 0,
        })
        saved = Path(result['path'])
        with Image.open(saved) as image:
            self.assertEqual(image.size, (result['width'], result['height']))
            self.assertGreater(max(image.size), 2000)
            self.assertGreater(min(image.size), 1500)

    def test_crop_overwrite_evicts_prediction_caches_and_refetches(self):
        path = self.png_source('overwrite.png', size=(640, 480))
        first = self.post('/api/crop-corners', {'path': str(path)})
        self.assertTrue(first['candidates'])
        resolved = str(path.resolve())
        self.assertTrue(crop_cache.RESULT_CACHE.contains_resolved(resolved))
        self.assertTrue(crop_cache.IMAGE_CACHE.contains_resolved(resolved))
        original = path.read_bytes()
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip',
            'replace_original': True,
            'backup_originals': True,
        })
        corners = [[120, 100], [520, 100], [520, 380], [120, 380]]
        status, refusal = self.post_status('/api/crop', {
            'path': str(path), 'points': corners, 'rotation': 0,
        })
        self.assertEqual(status, 409)
        status, cropped = self.post_status('/api/crop', {
            'path': str(path), 'points': corners, 'rotation': 0,
            'confirm_replace': True,
            'confirmation_token': refusal['confirmation_token'],
            'batch_id': refusal['batch_id'],
        })
        self.assertEqual(status, 200)
        self.assertNotEqual(path.read_bytes(), original)
        self.assertFalse(crop_cache.RESULT_CACHE.contains_resolved(resolved))
        self.assertFalse(crop_cache.IMAGE_CACHE.contains_resolved(resolved))
        second = self.post('/api/crop-corners', {'path': str(path)})
        self.assertNotEqual(first['candidates'], second['candidates'])

    def test_begin_batch_resets_caches_and_prefetch_queue(self):
        path = self.png_source('batch.png')
        self.post('/api/crop-corners', {'path': str(path)})
        self.assertGreater(len(crop_cache.RESULT_CACHE), 0)
        self.post('/api/begin-batch', {'path': str(self.root)})
        self.assertEqual(len(crop_cache.RESULT_CACHE), 0)
        self.assertEqual(len(crop_cache.IMAGE_CACHE), 0)

    def test_prefetch_abort_event_stops_detect_and_skips_cache_put(self):
        path = self.png_source('abort-prefetch.png')
        started = threading.Event()

        def slow_predict(*args, abort=None, **kwargs):
            started.set()
            deadline = time.time() + 4
            while time.time() < deadline:
                if abort is not None and abort():
                    return {'candidates': [], 'elapsed_ms': 0, 'aborted': True}
                time.sleep(0.01)
            return {'candidates': [{'points': [[1, 1], [10, 1], [10, 10], [1, 10]], 'score': 1}], 'elapsed_ms': 0}

        with patch.object(crop_detection, 'predict_corners', slow_predict):
            crop_cache.queue_prefetch([path])
            self.assertTrue(started.wait(3))
            crop_cache.queue_prefetch([])
            deadline = time.time() + 2
            while time.time() < deadline and crop_cache.prefetch_aborted():
                if len(crop_cache.RESULT_CACHE) == 0:
                    break
                time.sleep(0.02)
            time.sleep(0.15)
            self.assertEqual(len(crop_cache.RESULT_CACHE), 0)

    def test_prefetch_lands_in_result_lru(self):
        path = self.png_source('prefetch.png')
        crop_cache.queue_prefetch([path])
        deadline = time.time() + 8
        while time.time() < deadline and len(crop_cache.RESULT_CACHE) == 0:
            time.sleep(0.05)
        self.assertEqual(len(crop_cache.RESULT_CACHE), 1)
        cached = crop_cache.RESULT_CACHE.get(path, None)
        self.assertIsNotNone(cached)
        self.assertIn('candidates', cached)

    def test_decoded_preview_cache_evicts_at_memory_cap(self):
        tiny = crop_cache.ImagePreviewCache(memory_cap=20_000)
        first = self.png_source('one.png')
        second = self.png_source('two.png')
        image = Image.new('RGB', (80, 60), 'red')
        tiny.put(first, image.copy(), image.copy(), image.size)
        self.assertEqual(len(tiny), 1)
        tiny.put(second, image.copy(), image.copy(), image.size)
        self.assertEqual(len(tiny), 1)
        self.assertIsNone(tiny.get(first))
        self.assertIsNotNone(tiny.get(second))


if __name__ == '__main__':
    unittest.main()
