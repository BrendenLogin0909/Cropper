import json
import os
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

    def wait_until(self, predicate, timeout=8, message='condition not met'):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        self.fail(message)

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
        self.wait_until(lambda: crop_cache.IMAGE_CACHE.get(path) is not None, message='IMAGE_CACHE not filled after /api/image')
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

    def test_image_response_does_not_fill_cache_synchronously(self):
        path = self.jpeg_source('open-async.jpg', (3000, 2200))
        started = threading.Event()
        release = threading.Event()
        real = crop_cache.fill_from_full_image

        def blocking_fill(p, img):
            started.set()
            self.assertTrue(release.wait(6), 'fill was not released')
            return real(p, img)

        with patch.object(crop_cache, 'fill_from_full_image', blocking_fill):
            body = []
            errors = []

            def fetch():
                try:
                    body.append(self.get(f'/api/image?path={path}'))
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=fetch)
            worker.start()
            self.assertTrue(started.wait(5), 'fill never started')
            worker.join(2)
            self.assertFalse(errors)
            self.assertTrue(body, 'GET did not return before fill finished')
            self.assertGreater(len(body[0]), 100)
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_predict_reuses_in_progress_image_fill(self):
        path = self.jpeg_source('fill-reuse.jpg', (2400, 1800))
        previews = []
        started = threading.Event()
        release = threading.Event()
        real_preview = crop_detection.detection_preview

        def counting_preview(image):
            previews.append(1)
            started.set()
            self.assertTrue(release.wait(6), 'preview fill was not released')
            return real_preview(image)

        with patch.object(crop_detection, 'detection_preview', counting_preview):
            body = []
            errors = []

            def fetch():
                try:
                    body.append(self.get(f'/api/image?path={path}'))
                except Exception as exc:
                    errors.append(exc)

            opener = threading.Thread(target=fetch)
            opener.start()
            self.assertTrue(started.wait(5), 'image fill never started')
            predicted = []

            def predict():
                try:
                    predicted.append(self.post('/api/crop-corners', {'path': str(path)}))
                except Exception as exc:
                    errors.append(exc)

            predictor = threading.Thread(target=predict)
            predictor.start()
            time.sleep(0.2)
            self.assertEqual(len(previews), 1)
            release.set()
            opener.join(8)
            predictor.join(8)
            self.assertFalse(errors)
            self.assertTrue(body)
            self.assertTrue(predicted)
            self.assertEqual(len(previews), 1)
            full, _ = server.load_image(path)
            expected = crop_detection.predict_corners(full)
            self.assertEqual(predicted[0]['candidates'], expected['candidates'])

    def test_in_flight_fill_does_not_put_after_evict(self):
        path = self.jpeg_source('stale-fill.jpg', (2400, 1800))
        started = threading.Event()
        release = threading.Event()
        real_preview = crop_detection.detection_preview
        before = crop_cache.file_identity(path)

        def blocked_preview(image):
            started.set()
            self.assertTrue(release.wait(6), 'fill was not released')
            return real_preview(image)

        with patch.object(crop_detection, 'detection_preview', blocked_preview):
            opener_errors = []

            def fetch():
                try:
                    self.get(f'/api/image?path={path}')
                except Exception as exc:
                    opener_errors.append(exc)

            opener = threading.Thread(target=fetch)
            opener.start()
            self.assertTrue(started.wait(5), 'image fill never started')
            Image.new('RGB', (640, 480), 'navy').save(path)
            after = crop_cache.file_identity(path)
            if after[1] == before[1]:
                os.utime(path, ns=(before[1] + 1_000_000, before[1] + 1_000_000))
                after = crop_cache.file_identity(path)
            self.assertNotEqual(before[1], after[1], 'overwrite did not change mtime')
            self.assertNotEqual(before[2], after[2], 'overwrite did not change size')
            crop_cache.evict_path(path)
            self.assertIsNone(crop_cache.IMAGE_CACHE.get(path))
            release.set()
            opener.join(8)
            self.assertFalse(opener_errors)
            self.wait_until(
                lambda: crop_cache._inflight_fills.get(crop_cache._resolved_str(path)) is None,
                timeout=4,
                message='in-flight fill did not finish after release',
            )
            cached = crop_cache.IMAGE_CACHE.get(path)
            if cached is not None:
                self.assertNotEqual(
                    cached['original_size'],
                    (2400, 1800),
                    'old pixels stored under the new mtime/size identity',
                )
            self.assertIsNone(cached, 'stale in-flight fill repopulated IMAGE_CACHE')
            self.assertFalse(crop_cache.IMAGE_CACHE.contains_resolved(str(path.resolve())))
            for key, item in list(crop_cache.IMAGE_CACHE._data.items()):
                self.assertNotEqual(key, after, 'old pixels stored under the new identity')
                self.assertNotEqual(item['original_size'], (2400, 1800))
        result = self.post('/api/crop-corners', {'path': str(path)})
        cached = crop_cache.IMAGE_CACHE.get(path)
        self.assertIsNotNone(cached)
        self.assertEqual(cached['original_size'], (640, 480))
        self.assertTrue(result['candidates'] or result['candidates'] == [])

    def test_fill_leader_error_propagates_to_waiters(self):
        path = self.png_source('fill-err.png')
        entered = threading.Event()
        release_error = threading.Event()
        waiter_waiting = threading.Event()
        previews = []
        real_wait = crop_cache.wait_for_pending_fill

        def boom(image):
            previews.append(1)
            entered.set()
            self.assertTrue(release_error.wait(4), 'error was not released')
            raise RuntimeError('fill exploded')

        def wrapped_wait(wait_path, timeout=None):
            resolved = crop_cache._resolved_str(wait_path)
            with crop_cache._fill_lock:
                inflight = crop_cache._inflight_fills.get(resolved)
            if inflight is not None:
                waiter_waiting.set()
            return real_wait(wait_path, timeout)

        leader_errors = []
        waiter_errors = []
        with patch.object(crop_detection, 'detection_preview', boom):
            with patch.object(crop_cache, 'wait_for_pending_fill', wrapped_wait):
                image, _ = server.load_image(path)

                def leader():
                    try:
                        crop_cache.fill_from_full_image_exclusive(path, image)
                    except Exception as exc:
                        leader_errors.append(exc)

                def waiter():
                    try:
                        crop_cache.decode_detection_image(path)
                    except Exception as exc:
                        waiter_errors.append(exc)

                lead = threading.Thread(target=leader)
                wait = threading.Thread(target=waiter)
                lead.start()
                self.assertTrue(entered.wait(4), 'leader fill never started')
                wait.start()
                self.assertTrue(waiter_waiting.wait(4), 'waiter never joined the in-flight fill')
                release_error.set()
                lead.join(6)
                wait.join(6)
        self.assertTrue(leader_errors)
        self.assertTrue(waiter_errors, 'waiter did not receive the leader error')
        self.assertEqual(str(waiter_errors[0]), 'fill exploded')
        self.assertEqual(len(previews), 1)

    def test_same_identity_evict_during_put_does_not_leave_cached_entry(self):
        """evict_path between the identity/generation check and IMAGE_CACHE.put.

        Identity is unchanged (the coarse-mtime case generation exists for).
        A hooked put starts evict_path on another thread before the real put.
        After both finish the path must not remain cached: on 9c6dce9 the put
        overwrites the evict; with an atomic check+put the evict wins.
        """
        path = self.png_source('same-id-evict.png', size=(200, 160))
        image, _ = server.load_image(path)
        evict_started = threading.Event()
        evict_finished = threading.Event()
        evict_threads = []
        original_put = crop_cache.IMAGE_CACHE.put

        def racing_put(*args, **kwargs):
            def do_evict():
                evict_started.set()
                crop_cache.evict_path(path)
                evict_finished.set()

            worker = threading.Thread(target=do_evict, name='evict-during-put')
            worker.start()
            evict_threads.append(worker)
            self.assertTrue(evict_started.wait(2), 'evict_path never started')
            # Old code has already dropped _fill_lock, so evict finishes here
            # and the subsequent put stores start-identity pixels. New code
            # still holds _fill_lock, so this wait times out and evict runs
            # only after put returns and the lock is released.
            evict_finished.wait(0.2)
            return original_put(*args, **kwargs)

        with patch.object(crop_cache.IMAGE_CACHE, 'put', racing_put):
            crop_cache.fill_from_full_image(path, image)
        for worker in evict_threads:
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'evict_path deadlocked with IMAGE_CACHE.put')
        self.assertTrue(evict_threads, 'IMAGE_CACHE.put was never called')
        self.assertTrue(evict_finished.is_set(), 'evict_path did not finish')
        self.assertIsNone(
            crop_cache.IMAGE_CACHE.get(path),
            'same-identity evict_path was overwritten by a later put',
        )
        self.assertFalse(crop_cache.IMAGE_CACHE.contains_resolved(str(path.resolve())))

    def test_concurrent_fill_and_evict_do_not_deadlock(self):
        path = self.png_source('fill-evict-deadlock.png', size=(320, 240))
        image, _ = server.load_image(path)
        stop = threading.Event()
        errors = []

        def filler():
            try:
                while not stop.wait(0.001):
                    crop_cache.fill_from_full_image(path, image)
            except Exception as exc:
                errors.append(exc)

        def evictor():
            try:
                while not stop.wait(0.001):
                    crop_cache.evict_path(path)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=filler, name=f'fill-{i}') for i in range(3)]
        threads += [threading.Thread(target=evictor, name=f'evict-{i}') for i in range(3)]
        for thread in threads:
            thread.start()
        time.sleep(0.4)
        stop.set()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive(), f'{thread.name} did not finish; deadlock')
        self.assertFalse(errors, errors)

    def test_fill_waiter_times_out_and_decodes_itself(self):
        path = self.png_source('fill-timeout.png', size=(400, 300))
        started = threading.Event()
        release = threading.Event()
        real_preview = crop_detection.detection_preview
        previews = []

        def blocked_preview(image):
            previews.append(1)
            if len(previews) == 1:
                started.set()
                self.assertTrue(release.wait(6), 'leader fill was not released')
            return real_preview(image)

        with patch.object(crop_detection, 'detection_preview', blocked_preview):
            with patch.object(crop_cache, 'FILL_WAIT_SECONDS', 0.05):
                image, _ = server.load_image(path)
                lead_errors = []

                def leader():
                    try:
                        crop_cache.fill_from_full_image_exclusive(path, image)
                    except Exception as exc:
                        lead_errors.append(exc)

                lead = threading.Thread(target=leader)
                lead.start()
                self.assertTrue(started.wait(4), 'leader fill never started')
                detect, size = crop_cache.decode_detection_image(path)
                self.assertEqual(size, (400, 300))
                self.assertLessEqual(max(detect.size), crop_detection.DETECTION_SIZE)
                self.assertGreaterEqual(len(previews), 2)
                release.set()
                lead.join(6)
                self.assertFalse(lead_errors)

    def test_prefetch_then_open_predict_matches_cold_full_decode(self):
        path = self.jpeg_source('pref-full.jpg', (3000, 2200))
        decode_calls = []
        real_decode = crop_cache.decode_detection_image

        def wrapped_decode(*args, **kwargs):
            decode_calls.append({'args': args, 'kwargs': kwargs})
            return real_decode(*args, **kwargs)

        full, _ = server.load_image(path)
        expected = crop_detection.predict_corners(full)
        with patch.object(crop_cache, 'decode_detection_image', wrapped_decode):
            crop_cache.queue_prefetch([path])
            self.wait_until(
                lambda: crop_cache.RESULT_CACHE.get(path, None) is not None,
                timeout=15,
                message='prefetch did not land in RESULT_CACHE',
            )
        drafted = any(
            call['kwargs'].get('allow_draft') is True
            or (len(call['args']) >= 2 and call['args'][1] is True)
            for call in decode_calls
        )
        self.assertFalse(drafted, 'prefetch worker used JPEG draft decode')
        self.get(f'/api/image?path={path}')
        result = self.post('/api/crop-corners', {'path': str(path)})
        self.assertEqual(result['candidates'], expected['candidates'])
        cached = crop_cache.RESULT_CACHE.get(path, None)
        self.assertIsNotNone(cached)
        self.assertEqual(cached['candidates'], expected['candidates'])

    def test_foreground_predict_does_not_drop_neighbour_prefetch(self):
        current = self.png_source('now.png', size=(800, 600))
        neighbour = self.png_source('next.png', size=(640, 480))
        started = threading.Event()
        real = crop_detection.predict_corners

        def wrapped(image, target=None, original_size=None, abort=None):
            size = original_size or image.size
            if abort is not None and size == (800, 600):
                started.set()
                deadline = time.time() + 6
                while time.time() < deadline:
                    if abort():
                        return {'candidates': [], 'elapsed_ms': 0, 'aborted': True}
                    time.sleep(0.01)
                return {'candidates': [], 'elapsed_ms': 0, 'aborted': True}
            if abort is None:
                time.sleep(0.35)
            return real(image, target, original_size=original_size, abort=abort)

        with patch.object(crop_detection, 'predict_corners', wrapped):
            crop_cache.queue_prefetch([current, neighbour])
            self.assertTrue(started.wait(5), 'current prefetch never started')
            self.post('/api/crop-corners', {'path': str(current)})
            self.wait_until(
                lambda: crop_cache.RESULT_CACHE.get(neighbour, None) is not None,
                timeout=10,
                message='neighbour prefetch was dropped after foreground predict',
            )
        self.assertIsNotNone(crop_cache.RESULT_CACHE.get(neighbour, None))

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
        tiny.put(first, image.copy(), image.copy(), image.size, identity=crop_cache.file_identity(first))
        self.assertEqual(len(tiny), 1)
        tiny.put(second, image.copy(), image.copy(), image.size, identity=crop_cache.file_identity(second))
        self.assertEqual(len(tiny), 1)
        self.assertIsNone(tiny.get(first))
        self.assertIsNotNone(tiny.get(second))


if __name__ == '__main__':
    unittest.main()
