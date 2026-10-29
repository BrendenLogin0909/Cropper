import io
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import json

from PIL import Image

import app_settings
import chatgpt_pending
import server
import stage_files as stages


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(stream, 'PNG')
    return stream.getvalue()


class OutputHandlingTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        app_settings.configure_runtime(self.folder.name)
        self.addCleanup(lambda: setattr(app_settings, '_data_dir', None))

    def source(self, root, name='photo_c.png'):
        path = root / stages.CROP_QUEUE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png())
        return path

    def test_filename_template_preview_and_invalid_characters(self):
        source = Path('photo.jpg')
        self.assertEqual(stages.render_filename(source, 'crop', template='{name}{suffix}{ext}'), 'photo_c.jpg')
        self.assertEqual(stages.render_filename(source, 'restore', template='{name}_{date}{suffix}{ext}', date='20261029'),
                         'photo_20261029_ai.png')
        with self.assertRaisesRegex(ValueError, 'invalid characters'):
            stages.validate_template('{name}/{ext}')
        with self.assertRaisesRegex(ValueError, 'invalid characters'):
            stages.windows_safe_filename('photo<>.png')
        with self.assertRaisesRegex(ValueError, 'Filename template supports'):
            stages.validate_template('{name}{unknown}{ext}')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.validate_template('CON{ext}')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('CON.png')
        with self.assertRaisesRegex(ValueError, 'extension'):
            stages.validate_template('{name}')
        with self.assertRaisesRegex(ValueError, 'trailing'):
            stages.windows_safe_filename('photo.png.')

    def test_conflict_policies_skip_overwrite_auto_suffix(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            existing = root / stages.AI_QUEUE / 'photo_c_ai.png'
            existing.parent.mkdir(); existing.write_bytes(b'existing')
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'skip', 'replace_original': False,
                                                                 'backup_originals': True, 'output_folder': ''}):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    stages.file_results([source], [png('blue')], 'restore')
                self.assertEqual(existing.read_bytes(), b'existing')
                self.assertTrue(source.exists())
            source = self.source(root)
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'overwrite', 'replace_original': False,
                                                                 'backup_originals': True, 'output_folder': ''}):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
                self.assertTrue(stages.same_path(output, existing))
                self.assertEqual(output.read_bytes(), png('blue'))
            source = self.source(root, 'other_c.png')
            existing2 = root / stages.AI_QUEUE / 'other_c_ai.png'
            existing2.write_bytes(b'existing')
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'auto-suffix', 'replace_original': False,
                                                                 'backup_originals': True, 'output_folder': ''}):
                output = stages.file_results([source], [png('yellow')], 'restore')[0]
                self.assertEqual(output.name, 'other_c_ai-2.png')
                self.assertEqual(existing2.read_bytes(), b'existing')

    def test_replace_original_requires_backup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'skip', 'replace_original': True,
                                                                 'backup_originals': False, 'output_folder': ''}):
                with self.assertRaisesRegex(ValueError, 'Replace original needs backups'):
                    stages.plan(source, 'restore')
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'skip', 'replace_original': True,
                                                                 'backup_originals': True, 'output_folder': ''}):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
                self.assertTrue(stages.same_path(output, source))
                self.assertEqual(source.read_bytes(), png('blue'))
                self.assertEqual((root / stages.BACKUPS / 'photo_c.png').read_bytes(), original)

    def test_same_stem_names_are_reserved_before_pay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            jpg = self.source(root, 'photo1.jpg')
            png_source = self.source(root, 'photo1.png')
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'auto-suffix', 'replace_original': False,
                                                                 'backup_originals': True, 'output_folder': ''}):
                first = stages.prepare_ai_plans(jpg, 'restore')
                second = stages.prepare_ai_plans(png_source, 'restore')
                self.assertNotEqual(first[0]['output'], second[0]['output'])
                self.assertTrue(str(first[0]['output']).endswith('photo1_ai.png'))
                self.assertTrue(str(second[0]['output']).endswith('photo1_ai-2.png'))
            stages.release_outputs([first[0]['output'], second[0]['output']])
            with patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                                 'conflict_policy': 'skip', 'replace_original': False,
                                                                 'backup_originals': True, 'output_folder': ''}):
                stages.prepare_ai_plans(jpg, 'restore')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    stages.prepare_ai_plans(png_source, 'restore')

    def test_generate_reserves_then_skips_second_stem_without_second_paid_call(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        queue = Path(self.folder.name) / stages.CROP_QUEUE
        queue.mkdir()
        first = queue / 'photo1.jpg'
        second = queue / 'photo1.png'
        Image.new('RGB', (8, 8), 'gray').save(first)
        Image.new('RGB', (8, 8), 'gray').save(second)
        calls = []

        def edit(path, **kwargs):
            calls.append(path)
            return png('blue')

        def post(payload):
            request = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-generate',
                              json.dumps(payload).encode(),
                              {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'})
            with urlopen(request) as response:
                return json.load(response)

        with patch.object(server, 'read_links', return_value=[]), \
             patch.object(server, 'edit_front', side_effect=edit), \
             patch.object(stages, '_settings', return_value={'filename_template': '{name}{suffix}{ext}',
                                                            'conflict_policy': 'skip', 'replace_original': False,
                                                            'backup_originals': True, 'output_folder': ''}):
            post({'front_path': str(first)})
            with self.assertRaises(HTTPError) as caught:
                post({'front_path': str(second)})
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(len(calls), 1)

    def test_rollback_keeps_created_files_if_source_cannot_be_restored(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            source_key = source.resolve()
            unlinked = {'done': False}
            original_unlink = Path.unlink
            original_replace = os.replace
            original = source.read_bytes()

            def unlink_then_fail(self, missing_ok=False):
                original_unlink(self, missing_ok=missing_ok)
                if stages.same_path(self, source_key):
                    unlinked['done'] = True
                    raise OSError('fail after unlink')

            def fail_source_replace(src, dst, *args, **kwargs):
                dest = Path(dst)
                if unlinked['done'] and stages.same_path(dest, source_key):
                    raise OSError('cannot restore source')
                return original_replace(src, dst, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink_then_fail), patch.object(os, 'replace', fail_source_replace):
                with self.assertRaises(OSError):
                    stages.file_results([source], [png('blue')], 'restore')
            recovered = source.with_name(source.name + '.cropper-recovered')
            self.assertTrue((root / stages.CROPPED / source.name).is_file())
            self.assertTrue((root / stages.AI_QUEUE / 'photo_c_ai.png').is_file())
            self.assertTrue(recovered.is_file())
            self.assertEqual(recovered.read_bytes(), original)
            self.assertFalse(source.exists())

    def test_rollback_does_not_overwrite_existing_recovered_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            source_key = source.resolve()
            original = source.read_bytes()
            existing = source.with_name(source.name + '.cropper-recovered')
            existing.write_bytes(b'previous-recovery')
            unlinked = {'done': False}
            original_unlink = Path.unlink
            original_replace = os.replace

            def unlink_then_fail(self, missing_ok=False):
                original_unlink(self, missing_ok=missing_ok)
                if stages.same_path(self, source_key):
                    unlinked['done'] = True
                    raise OSError('fail after unlink')

            def fail_source_replace(src, dst, *args, **kwargs):
                dest = Path(dst)
                if unlinked['done'] and stages.same_path(dest, source_key):
                    raise OSError('cannot restore source')
                return original_replace(src, dst, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink_then_fail), patch.object(os, 'replace', fail_source_replace):
                with self.assertRaises(OSError):
                    stages.file_results([source], [png('blue')], 'restore')
            self.assertEqual(existing.read_bytes(), b'previous-recovery')
            unique = source.with_name(source.name + '.cropper-recovered-2')
            self.assertTrue(unique.is_file())
            self.assertEqual(unique.read_bytes(), original)

    def test_exclusive_writes_use_temp_and_replace(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'fresh.png'
            with patch.object(os, 'replace', wraps=os.replace) as replaced:
                created = stages._atomic_create(target, b'abc', overwrite=False)
            self.assertTrue(created)
            self.assertEqual(target.read_bytes(), b'abc')
            replaced.assert_called()
            with self.assertRaises(FileExistsError):
                stages._atomic_create(target, b'other', overwrite=False)
            self.assertEqual(target.read_bytes(), b'abc')
            stages._atomic_create(target, b'new', overwrite=True)
            self.assertEqual(target.read_bytes(), b'new')

    def test_expired_pending_releases_reservations(self):
        root = Path(self.folder.name)
        photos = root / stages.CROP_QUEUE
        photos.mkdir(parents=True)
        source = photos / 'front.png'
        source.write_bytes(png())
        dest = root / stages.AI_QUEUE / 'front_ai.png'
        dest.parent.mkdir(parents=True)
        stages.reserve_plan_outputs([{'output': dest}])
        self.assertIn(dest.resolve(), stages.reserved_paths())
        record = chatgpt_pending.save_pending(source, png('blue'), output_paths=[dest])
        meta = chatgpt_pending.default_pending_root() / f"{record['id']}.json"
        data = json.loads(meta.read_text(encoding='utf-8'))
        data['created'] = 1
        meta.write_text(json.dumps(data), encoding='utf-8')
        removed = chatgpt_pending.cleanup_expired(now=time.time(), ttl=60)
        self.assertIn(record['id'], removed)
        self.assertNotIn(dest.resolve(), stages.reserved_paths())
        pending_root = chatgpt_pending.default_pending_root()
        self.assertTrue((pending_root / f"{record['id']}.png").is_file())
        self.assertTrue((pending_root / f"{record['id']}.json").is_file())
        self.assertIn(record['id'], [item['id'] for item in chatgpt_pending.list_recovered()])

    def test_discard_releases_reservations(self):
        root = Path(self.folder.name)
        photos = root / stages.CROP_QUEUE
        photos.mkdir(parents=True)
        source = photos / 'front.png'
        source.write_bytes(png())
        dest = root / stages.AI_QUEUE / 'front_ai.png'
        dest.parent.mkdir(parents=True)
        stages.reserve_plan_outputs([{'output': dest}])
        record = chatgpt_pending.save_pending(source, png('blue'), output_paths=[dest])
        self.assertIn(dest.resolve(), stages.reserved_paths())
        chatgpt_pending.discard_recovered(record['id'])
        self.assertNotIn(dest.resolve(), stages.reserved_paths())

    def test_unbound_reservations_expire_after_ttl_without_save_pending(self):
        root = Path(self.folder.name)
        dest = root / stages.AI_QUEUE / 'front_ai.png'
        dest.parent.mkdir(parents=True)
        stages.reserve_plan_outputs([{'output': dest}])
        self.assertIn(dest.resolve(), stages.reserved_paths())
        entries = [item for item in stages._load_reservation_entries()
                   if stages.path_key(Path(item['path'])) == stages.path_key(dest)]
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0].get('id'))
        created = float(entries[0]['created'])
        stages.sweep_stale_reservations(now=created + stages.NULL_RESERVATION_TTL_SECONDS - 1)
        self.assertIn(dest.resolve(), stages.reserved_paths())
        stages.sweep_stale_reservations(now=created + stages.NULL_RESERVATION_TTL_SECONDS)
        self.assertNotIn(dest.resolve(), stages.reserved_paths())

    def test_windows_reserved_names_and_long_paths(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('CON.png')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('nul.txt')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('COM1.jpg')
        with self.assertRaisesRegex(ValueError, 'trailing'):
            stages.windows_safe_filename('photo.png.')
        with self.assertRaisesRegex(ValueError, 'trailing'):
            stages.windows_safe_filename('photo.png ')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.render_filename(Path('CON.jpg'), 'crop', template='{name}{ext}')
        with patch.object(stages, '_is_windows', return_value=True):
            long = Path('C:/' + ('a' * 300) + '/photo.png')
            prefixed = stages.windows_long_path(long)
            self.assertTrue(str(prefixed).startswith('\\\\?\\'))
            unc = stages.windows_long_path(Path('//server/share/photo.png'))
            self.assertTrue(str(unc).startswith('\\\\?\\UNC\\') or str(unc).startswith('\\\\?\\'))

    def test_windows_lock_pads_and_times_out(self):
        fake = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=Mock())
        with patch.object(stages, 'windows_long_path', side_effect=lambda path: Path(path)), \
             patch.object(stages, '_is_windows', return_value=True), \
             patch.dict(sys.modules, {'msvcrt': fake}):
            with TemporaryDirectory() as tmp:
                lock = Path(tmp) / 'cropper.lock'
                with stages.interprocess_lock(lock, timeout=1):
                    self.assertGreaterEqual(lock.stat().st_size, 1)
                fake.locking.side_effect = OSError('locked')
                with self.assertRaises(TimeoutError):
                    with stages.interprocess_lock(lock, timeout=0.2):
                        pass
        self.assertGreaterEqual(fake.locking.call_count, 2)

    def test_permission_error_distinguishes_locked_file(self):
        folder = PermissionError('denied')
        folder.filename = str(Path(self.folder.name))
        status, body = stages.permission_error_response(folder)
        self.assertEqual(status, 403)
        self.assertEqual(body['code'], 'permission_denied')
        self.assertIn('Choose another output folder', body['error'])
        locked = PermissionError('in use')
        locked.filename = str(Path(self.folder.name) / 'photo_c_ai.png')
        status, body = stages.permission_error_response(locked)
        self.assertEqual(body['code'], 'file_locked')
        self.assertIn('Close the file', body['error'])
        self.assertNotIn('Choose another output folder', body['error'])

    def test_resolve_port_cli_wins_over_env(self):
        with patch.dict(os.environ, {'CROPPER_PORT': '8767'}):
            self.assertEqual(server.resolve_port(None), 8767)
            self.assertEqual(server.resolve_port(9001), 9001)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CROPPER_PORT', None)
            self.assertEqual(server.resolve_port(None), 8765)

    def test_second_listener_on_the_same_port_fails(self):
        first = server.LocalServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            server.LocalServer(('127.0.0.1', first.server_port), server.Handler)

    def test_permission_error_offers_another_output_folder(self):
        self.assertIn('Choose another output folder', Path(stages.__file__).read_text(encoding='utf-8'))
        self.assertIn('choose-output-folder', Path(__file__).with_name('index.html').read_text(encoding='utf-8'))
        self.assertIn('offerChooseOutputFolder', Path(__file__).with_name('app.js').read_text(encoding='utf-8'))

    def test_index_increments_and_is_zero_padded(self):
        self.assertEqual(stages.format_index(1, 1), '001')
        self.assertEqual(stages.format_index(12, 99), '012')
        self.assertEqual(stages.format_index(5, 1000), '0005')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.source(root, 'a_c.png')
            second = self.source(root, 'b_c.png')
            with patch.object(stages, '_settings', return_value={
                'filename_template': '{index}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                outputs = stages.file_results([first, second], [png('blue'), png('green')], 'restore')
            self.assertEqual(outputs[0].name, '001.png')
            self.assertEqual(outputs[1].name, '002.png')

    def test_commit_removes_batch_lock_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            leftover = root / '.cropper.lock'
            leftover.write_bytes(b'stale')
            with patch.object(stages, '_settings', return_value={
                'filename_template': '{name}{suffix}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                stages.file_results([source], [png('blue')], 'restore')
            self.assertFalse(leftover.exists())

    def test_gps_stripped_unless_keep_gps(self):
        from PIL.TiffImagePlugin import IFDRational
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / 'geo.jpg'
            image = Image.new('RGB', (16, 16), 'red')
            exif = Image.Exif()
            exif[271] = 'CropperCam'
            exif[274] = 1
            gps = exif.get_ifd(0x8825)
            gps[1] = 'N'
            gps[2] = (IFDRational(37, 1), IFDRational(48, 1), IFDRational(0, 1))
            image.save(source, 'JPEG', exif=exif)
            loaded, info = server.load_image(source)
            stripped = Path(tmp) / 'stripped.jpg'
            server.save_rendered(loaded, stripped, info)
            with Image.open(stripped) as image:
                gone = image.getexif()
                self.assertNotIn(0x8825, gone)
                self.assertEqual(dict(gone.get_ifd(0x8825)), {})
                self.assertEqual(gone.get(271), 'CropperCam')
            app_settings.save_settings({'keep_gps': True})
            kept_path = Path(tmp) / 'kept.jpg'
            server.save_rendered(loaded, kept_path, info)
            with Image.open(kept_path) as image:
                kept = image.getexif()
                self.assertIn(0x8825, kept)
                self.assertEqual(kept.get_ifd(0x8825).get(1), 'N')

    def test_invalid_cropper_port_prints_plain_error(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = ['server.py', '--no-browser', '--data-dir', self.folder.name]
        with patch.dict(os.environ, {'CROPPER_PORT': 'not-a-port'}), \
             patch('sys.argv', argv), patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            with self.assertRaises(SystemExit) as caught:
                server.main()
        self.assertEqual(caught.exception.code, 1)
        blob = stdout.getvalue() + stderr.getvalue()
        self.assertIn('CROPPER_PORT must be an integer port', blob)
        self.assertNotIn('Traceback', blob)

    def test_overwrite_confirmation_once_per_batch(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        server.OVERWRITE_SKIP_SESSION.clear()
        queue = Path(self.folder.name) / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        source = queue / 'photo_c.png'
        Image.new('RGB', (8, 8), 'gray').save(source)
        existing = Path(self.folder.name) / stages.AI_QUEUE / 'photo_c_ai.png'
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b'existing')
        app_settings.save_settings({'conflict_policy': 'overwrite', 'replace_original': False})

        def post(payload, confirmed=None):
            body = {'front_path': str(source), 'operation': 'restore'}
            if confirmed is not None:
                body.update(confirmed)
            request = Request(
                f'http://127.0.0.1:{http.server_port}/api/overwrite-preview',
                json.dumps({'path': str(source), 'operation': 'restore'}).encode(),
                {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'},
            )
            with urlopen(request) as response:
                preview = json.load(response)
            return preview

        preview = post({})
        self.assertGreaterEqual(preview['count'], 1)
        self.assertIn('photo_c_ai.png', preview['names'])

        # Direct overwrite gate via restore plan:
        with patch.object(stages, '_settings', return_value={
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite', 'replace_original': False,
            'backup_originals': True, 'output_folder': '',
        }):
            plans = [stages.plan(source, 'restore')]
            refusal = server.overwrite_response(plans, {})
            self.assertIsNotNone(refusal)
            self.assertEqual(refusal['code'], 'overwrite_confirmation_required')
            self.assertEqual(refusal['count'], 1)
            self.assertIsNone(server.overwrite_response(plans, {'confirm_overwrite': True}))
            self.assertIsNone(server.overwrite_response(plans, {'skip_overwrite_prompt': True}))
            self.assertIsNone(server.overwrite_response(plans, {}))
        server.OVERWRITE_SKIP_SESSION.clear()


@unittest.skipUnless(os.name == 'nt', 'Windows-only path and lock behaviour')
class WindowsIntegrationTests(unittest.TestCase):
    def test_file_lock_times_out_while_held_and_unlocks_after(self):
        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / 'cropper.lock'
            started = threading.Event()
            release = threading.Event()
            errors = []

            def hold():
                try:
                    with stages.interprocess_lock(lock, timeout=5):
                        started.set()
                        if not release.wait(20):
                            errors.append('holder wait timed out')
                except Exception as exc:
                    errors.append(exc)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(5), 'Windows lock was not acquired')
            with self.assertRaises(TimeoutError):
                with stages.interprocess_lock(lock, timeout=0.4):
                    pass
            release.set()
            holder.join(10)
            self.assertFalse(holder.is_alive())
            self.assertEqual(errors, [])
            with stages.interprocess_lock(lock, timeout=2):
                self.assertGreaterEqual(lock.stat().st_size, 1)

    def test_long_paths(self):
        with TemporaryDirectory() as tmp:
            current = Path(tmp).resolve()
            while len(str(current)) < 260:
                current = current / ('folder with spaces ' + 'a' * 40)
            self.assertGreater(len(str(current / 'photo.png')), 260)
            target = current / 'photo.png'
            created = stages._atomic_create(target, png(), overwrite=False)
            self.assertTrue(created)
            prefixed = stages.windows_long_path(target)
            self.assertTrue(str(prefixed).startswith('\\\\?\\'))
            self.assertTrue(prefixed.is_file())
            self.assertEqual(prefixed.read_bytes(), png())

    def test_reserved_names(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('CON.png')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('nul.txt')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            stages.windows_safe_filename('COM1.jpg')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'safe.png'
            path.write_bytes(b'device-safe')
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b'device-safe')

    def test_paths_with_spaces(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'batch root'
            source = root / stages.CROP_QUEUE / 'my photo.png'
            source.parent.mkdir(parents=True)
            source.write_bytes(png())
            with patch.object(stages, '_settings', return_value={
                'filename_template': '{name}{suffix}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
            self.assertTrue(output.is_file())
            self.assertIn(' ', str(output))
            self.assertIn(' ', str(source))
            self.assertEqual(output.read_bytes(), png('blue'))
            self.assertFalse(source.exists())

    def test_permission_error(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'photo_c_ai.png'
            target.write_bytes(b'original')
            other = Path(tmp) / 'replacement.png'
            other.write_bytes(b'new')
            handle = _windows_exclusive_handle(target)
            try:
                with self.assertRaises(PermissionError) as caught:
                    os.replace(other, target)
                status, body = stages.permission_error_response(caught.exception)
            finally:
                _windows_close_handle(handle)
            self.assertEqual(status, 403)
            self.assertEqual(body['code'], 'file_locked')
            self.assertIn('Close the file', body['error'])
            self.assertEqual(target.read_bytes(), b'original')


def _windows_exclusive_handle(path: Path):
    import ctypes
    from ctypes import wintypes
    generic_read_write = 0x80000000 | 0x40000000
    open_existing = 3
    file_attribute_normal = 0x80
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), generic_read_write, 0, None, open_existing, file_attribute_normal, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in (0, invalid, None):
        raise OSError('CreateFileW could not open an exclusive handle')
    return handle


def _windows_close_handle(handle):
    import ctypes
    ctypes.windll.kernel32.CloseHandle(handle)


class RecoveredServerTests(unittest.TestCase):
    def test_pending_api_surfaces_truncated_json(self):
        with TemporaryDirectory() as folder:
            app_settings.configure_runtime(folder)
            photos = Path(folder) / stages.CROP_QUEUE
            photos.mkdir(parents=True)
            pending = Path(folder) / 'pending'
            source = photos / 'front.png'
            source.write_bytes(png())
            record = chatgpt_pending.save_pending(source, png('blue'), pending)
            metadata = pending / f"{record['id']}.json"
            metadata.write_text(metadata.read_text(encoding='utf-8') + ',', encoding='utf-8')
            http = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            request = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-pending?path={quote(str(photos))}',
                              headers={'X-Cropper-Token': server.TOKEN})
            with urlopen(request) as response:
                body = json.load(response)
            self.assertEqual(body['items'], [])
            self.assertEqual(len(body['recovered']), 1)
            self.assertTrue(body['recovered'][0]['can_keep'])
            keep = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-recovered-keep',
                           json.dumps({'id': record['id']}).encode(),
                           {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'})
            with urlopen(keep) as response:
                kept = json.load(response)
            self.assertEqual(kept['id'], record['id'])
            with urlopen(request) as response:
                body = json.load(response)
            self.assertEqual(len(body['items']), 1)
            self.assertEqual(body['recovered'], [])


if __name__ == '__main__':
    unittest.main()
