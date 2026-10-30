import hashlib
import io
import os
import secrets
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
import numpy as np

import app_settings
import chatgpt_pending
import photo_pairs
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
        server.reset_confirmations()
        server.reset_tabs()

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

    def test_probe_folder_writable_leaves_no_temp_file(self):
        with TemporaryDirectory() as tmp:
            stages.probe_folder_writable(Path(tmp))
            self.assertEqual([path.name for path in Path(tmp).iterdir() if path.name.startswith('.cropper-preflight-')], [])

    def test_probe_folder_writable_wraps_oserror_as_permission_error(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'locked'
            dest.mkdir()
            error = OSError('denied')
            error.filename = str(dest)
            with patch.object(stages.tempfile, 'mkstemp', side_effect=error):
                with self.assertRaises(PermissionError) as caught:
                    stages.probe_folder_writable(dest)
            self.assertEqual(str(caught.exception.filename), str(dest))

    @unittest.skipUnless(os.name != 'nt', 'Unix mode bits do not make Windows folders unwritable')
    def test_probe_folder_writable_raises_on_unwritable_folder(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'locked'
            dest.mkdir()
            dest.chmod(0o555)
            try:
                with self.assertRaises(PermissionError):
                    stages.probe_folder_writable(dest)
            finally:
                dest.chmod(0o755)

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

    def test_index_increments_across_separate_file_results_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.source(root, 'a_c.png')
            second = self.source(root, 'b_c.png')
            with patch.object(stages, '_settings', return_value={
                'filename_template': 'scan_{index}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                out1 = stages.file_results([first], [png('blue')], 'restore')[0]
                out2 = stages.file_results([second], [png('green')], 'restore')[0]
            self.assertEqual(out1.name, 'scan_001.png')
            self.assertEqual(out2.name, 'scan_002.png')

    def test_index_increments_across_separate_crop_calls(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        root = Path(self.folder.name)
        photo1 = root / 'photo1.png'
        photo2 = root / 'photo2.png'
        Image.new('RGB', (16, 16), 'red').save(photo1)
        Image.new('RGB', (16, 16), 'blue').save(photo2)
        app_settings.save_settings({
            'filename_template': 'scan_{index}{ext}',
            'conflict_policy': 'skip',
            'replace_original': False,
            'backup_originals': True,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]

        def crop(path):
            request = Request(
                f'http://127.0.0.1:{http.server_port}/api/crop',
                json.dumps({'path': str(path), 'points': corners, 'rotation': 0}).encode(),
                {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'},
            )
            with urlopen(request) as response:
                return json.load(response)

        first = crop(photo1)
        second = crop(photo2)
        self.assertEqual(first['name'], 'scan_001.png')
        self.assertEqual(second['name'], 'scan_002.png')
        queue = root / stages.CROP_QUEUE
        self.assertTrue((queue / 'scan_001.png').is_file())
        self.assertTrue((queue / 'scan_002.png').is_file())
        self.assertNotEqual(first['path'], second['path'])

    def _non_stage_settings(self, **overrides):
        data = {
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip', 'replace_original': False,
            'backup_originals': True, 'output_folder': '',
        }
        data.update(overrides)
        return data

    def test_non_stage_suffix_restore_and_colorize(self):
        name = '[Scan]_20261027_0004 [B&W].jpg'
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / name
            Image.new('RGB', (16, 16), 'red').save(source)
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                restored = stages.file_results([source], [png('blue')], 'restore')[0]
                coloured = stages.file_results([source], [png('green')], 'colorize')[0]
            self.assertTrue(stages.same_path(restored, photos / '[Scan]_20261027_0004 [B&W]_ai.png'))
            self.assertTrue(stages.same_path(coloured, photos / '[Scan]_20261027_0004 [B&W]_col.png'))
            self.assertEqual(restored.read_bytes(), png('blue'))
            self.assertEqual(coloured.read_bytes(), png('green'))
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((photos / stages.AI_QUEUE).exists())
            self.assertFalse((photos / stages.COLOR).exists())

    def test_non_stage_copy_mode_and_png_source_uses_conflict_policy(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            jpg = photos / 'plain copy.jpg'
            Image.new('RGB', (16, 16), 'red').save(jpg)
            original_jpg = jpg.read_bytes()
            colour_jpg = photos / 'colour copy.jpg'
            Image.new('RGB', (16, 16), 'red').save(colour_jpg)
            copy_settings = self._non_stage_settings(filename_template='{name}{ext}')
            with patch.object(stages, '_settings', return_value=copy_settings):
                output = stages.file_results([jpg], [png('blue')], 'restore')[0]
                coloured = stages.file_results([colour_jpg], [png('green')], 'colorize')[0]
            self.assertTrue(stages.same_path(output, photos / 'plain copy.png'))
            self.assertEqual(output.read_bytes(), png('blue'))
            self.assertTrue(stages.same_path(coloured, photos / 'colour copy.png'))
            self.assertEqual(coloured.read_bytes(), png('green'))
            self.assertTrue(jpg.exists())
            self.assertEqual(jpg.read_bytes(), original_jpg)
            self.assertTrue(colour_jpg.exists())

            png_source = photos / 'scan & copy.png'
            png_source.write_bytes(png('red'))
            original_png = png_source.read_bytes()
            with patch.object(stages, '_settings', return_value=copy_settings):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    stages.file_results([png_source], [png('blue')], 'restore')
            self.assertEqual(png_source.read_bytes(), original_png)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(
                    filename_template='{name}{ext}', conflict_policy='overwrite')):
                output = stages.file_results([png_source], [png('blue')], 'restore')[0]
            self.assertEqual(output.name, 'scan & copy-2.png')
            self.assertEqual(output.read_bytes(), png('blue'))
            self.assertEqual(png_source.read_bytes(), original_png)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(
                    filename_template='{name}{ext}', conflict_policy='auto-suffix')):
                output = stages.file_results([png_source], [png('green')], 'restore')[0]
            self.assertEqual(output.name, 'scan & copy-3.png')
            self.assertEqual(png_source.read_bytes(), original_png)

            with patch.object(stages, '_settings', return_value=self._non_stage_settings(
                    filename_template='{name}{ext}', conflict_policy='overwrite')):
                plans = stages.prepare_ai_plans(png_source, 'restore')
            self.assertEqual(plans[0]['output'].name, 'scan & copy-4.png')
            self.assertFalse(plans[0]['overwrite'])
            self.assertEqual(png_source.read_bytes(), original_png)
            stages.release_outputs([plans[0]['output']])

    def test_non_stage_new_folder_restore_and_colorize(self):
        name = '[Scan]_20261027_0004 [B&W].jpg'
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            dest = Path(tmp) / 'restored'
            photos.mkdir()
            source = photos / name
            Image.new('RGB', (16, 16), 'red').save(source)
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(output_folder=str(dest))):
                restored = stages.file_results([source], [png('blue')], 'restore')[0]
                coloured = stages.file_results([source], [png('green')], 'colorize')[0]
            self.assertTrue(stages.same_path(restored, dest / '[Scan]_20261027_0004 [B&W]_ai.png'))
            self.assertTrue(stages.same_path(coloured, dest / '[Scan]_20261027_0004 [B&W]_col.png'))
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((photos / stages.AI_QUEUE).exists())

    def test_non_stage_suffix_conflict_policy(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'clash.jpg'
            source.write_bytes(png())
            existing = photos / 'clash_ai.png'
            existing.write_bytes(b'existing')
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    stages.file_results([source], [png('blue')], 'restore')
            self.assertEqual(existing.read_bytes(), b'existing')
            self.assertTrue(source.exists())
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(conflict_policy='overwrite')):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
            self.assertEqual(output.read_bytes(), png('blue'))
            self.assertTrue(source.exists())
            taken = photos / 'other_ai.png'
            taken.write_bytes(b'existing')
            other = photos / 'other.jpg'
            other.write_bytes(png())
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(conflict_policy='auto-suffix')):
                output = stages.file_results([other], [png('blue')], 'restore')[0]
            self.assertEqual(output.name, 'other_ai-2.png')
            self.assertEqual(taken.read_bytes(), b'existing')
            self.assertTrue(other.exists())

    def test_non_stage_replace_keeps_backup_until_verified(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'replace me & copy.jpg'
            source.write_bytes(png())
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(replace_original=True)):
                output = stages.file_results([source], [png('yellow')], 'restore')[0]
            self.assertTrue(stages.same_path(output, source))
            self.assertEqual(source.read_bytes(), png('yellow'))
            backup = photos / stages.BACKUPS / source.name
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_bytes(), original)

            png_source = photos / 'keep original.png'
            png_source.write_bytes(png('red'))
            original_png = png_source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(
                    filename_template='{name}{ext}', replace_original=True)):
                replaced = stages.file_results([png_source], [png('blue')], 'restore')[0]
            self.assertTrue(stages.same_path(replaced, png_source))
            self.assertEqual(png_source.read_bytes(), png('blue'))
            png_backup = photos / stages.BACKUPS / png_source.name
            self.assertTrue(png_backup.is_file())
            self.assertEqual(png_backup.read_bytes(), original_png)

    def test_non_stage_unwritable_output_fails_before_ai_call(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        folder = Path(self.folder.name) / 'scans'
        dest = Path(self.folder.name) / 'locked-out'
        folder.mkdir()
        dest.mkdir()
        source = folder / '[Scan]_20261027_0004 [B&W].jpg'
        Image.new('RGB', (16, 16), 'gray').save(source)
        calls = []

        def edit(path, **kwargs):
            calls.append(path)
            return png('blue')

        def boom(*args, **kwargs):
            error = PermissionError('denied')
            error.filename = str(dest)
            raise error

        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip',
            'replace_original': False,
            'backup_originals': True,
            'output_folder': str(dest),
        })
        with patch.object(server, 'read_links', return_value=[]), \
             patch.object(server, 'edit_front', side_effect=edit), \
             patch.object(stages, 'probe_folder_writable', side_effect=boom):
            status, body = self._json(http.server_port, '/api/chatgpt-generate', {
                'front_path': str(source), 'operation': 'restore',
            })
        self.assertEqual(status, 403)
        self.assertEqual(body['code'], 'permission_denied')
        self.assertIn('Choose another output folder', body['error'])
        self.assertEqual(calls, [])
        self.assertTrue(source.exists())
        self.assertFalse(list(dest.glob('*.png')))

    def test_non_stage_replace_http_keeps_backup_until_verified(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        folder = Path(self.folder.name) / 'scans'
        folder.mkdir()
        source = folder / 'replace me & copy.jpg'
        Image.new('RGB', (16, 16), 'gray').save(source)
        original = source.read_bytes()
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip',
            'replace_original': True,
            'backup_originals': True,
            'output_folder': '',
        })

        def edit(path, **kwargs):
            return png('yellow')

        with patch.object(server, 'read_links', return_value=[]), \
             patch.object(server, 'edit_front', side_effect=edit):
            status, record = self._json(http.server_port, '/api/chatgpt-generate', {
                'front_path': str(source), 'operation': 'restore',
            }, tab=tab)
            self.assertEqual(status, 200)
            status, body = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
            }, tab=tab)
            self.assertEqual(status, 409)
            self.assertEqual(body['code'], 'replace_confirmation_required')
            status, accepted = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
                'confirm_replace': True,
                'confirmation_token': body['confirmation_token'],
            }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(source.read_bytes(), png('yellow'))
        backup = folder / stages.BACKUPS / source.name
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), original)

    def test_restore_in_stage_folder_still_uses_flat_routing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value={
                'filename_template': '{name}{suffix}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
            self.assertTrue(stages.same_path(output, root / stages.AI_QUEUE / 'photo_c_ai.png'))
            self.assertEqual(output.read_bytes(), png('blue'))
            self.assertEqual((root / stages.CROPPED / source.name).read_bytes(), original)
            self.assertEqual((root / stages.SKIP_ENHANCE / source.name).read_bytes(), original)
            self.assertFalse(source.exists())

    def test_ai_restore_accepts_non_stage_photo_with_special_filename(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        folder = Path(self.folder.name) / 'scans'
        folder.mkdir()
        source = folder / '[Scan]_20261027_0004 [B&W].jpg'
        Image.new('RGB', (16, 16), 'gray').save(source)
        original = source.read_bytes()

        def edit(path, **kwargs):
            return png('blue')

        with patch.object(server, 'read_links', return_value=[]), \
             patch.object(server, 'edit_front', side_effect=edit), \
             patch.object(stages, '_settings', return_value={
                 'filename_template': '{name}{suffix}{ext}',
                 'conflict_policy': 'skip', 'replace_original': False,
                 'backup_originals': True, 'output_folder': '',
             }):
            status, record = self._json(http.server_port, '/api/chatgpt-generate', {
                'front_path': str(source), 'operation': 'restore',
            })
            self.assertEqual(status, 200)
            status, accepted = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
            })
        self.assertEqual(status, 200)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), original)
        saved = folder / '[Scan]_20261027_0004 [B&W]_ai.png'
        self.assertTrue(saved.is_file())
        self.assertFalse((folder / stages.AI_QUEUE).exists())
        self.assertFalse((folder / stages.CROPPED).exists())

    def test_non_stage_crop_is_not_blocked(self):
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'plain copy.jpg'
            Image.new('RGB', (16, 16), 'red').save(source)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                cropped, _ = server.save_image(source, corners, {}, 0)
            self.assertTrue(cropped.is_file())

    def test_non_stage_enhance_finish_and_merge(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'plain copy.jpg'
            Image.new('RGB', (16, 16), 'red').save(source)
            original = source.read_bytes()
            dusty = photos / 'dusty.png'
            pixels = np.full((70, 90, 3), 120, dtype=np.uint8)
            pixels[30:33, 40:43] = 245
            Image.fromarray(pixels).save(dusty)
            dusty_bytes = dusty.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                enhanced, _, _, archived = server.save_enhanced_image(source, {})
                restored, _, _, restore_archived = server.save_restored_image(dusty, 'dust')
                finished = stages.plan(source, 'finish')
                stages.commit([finished], [None])
            self.assertTrue(enhanced.is_file())
            self.assertIsNone(archived)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(restored.is_file())
            self.assertIsNone(restore_archived)
            self.assertTrue(dusty.exists())
            self.assertEqual(dusty.read_bytes(), dusty_bytes)
            self.assertTrue(finished.get('keep_source'))
            self.assertIsNone(finished.get('output'))
            self.assertFalse((photos / stages.ENHANCE_QUEUE).exists())

            front = photos / 'front.jpg'
            back = photos / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                merged, _ = server.save_merged_image(front, back, {}, 'front', 0)
            self.assertEqual(merged.name, 'front_front-back_c.jpg')
            self.assertTrue(front.exists())
            self.assertTrue(back.exists())

    def test_crop_from_later_folder_and_photo_link_in_non_stage_folder(self):
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        with TemporaryDirectory() as tmp:
            later = Path(tmp) / stages.CROPPED
            later.mkdir()
            source = later / 'photo.png'
            source.write_bytes(png())
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                output, _ = server.save_image(source, corners, {}, 0)
            self.assertTrue(source.exists())
            self.assertTrue(stages.same_path(output, later / 'photo_c.png'))
            self.assertFalse((later / stages.CROP_QUEUE).exists())

            front = Path(tmp) / 'scans' / 'a.jpg'
            combined = Path(tmp) / 'scans' / 'a-back.jpg'
            front.parent.mkdir()
            Image.new('RGB', (8, 8), 'red').save(front)
            Image.new('RGB', (8, 8), 'blue').save(combined)
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                link = photo_pairs.approve_link(front, combined, 50, 'right')
            self.assertTrue(stages.same_path(link['front_path'], front))

    def test_crop_in_non_tbp_folder_uses_output_settings(self):
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        with TemporaryDirectory() as tmp:
            later = Path(tmp) / stages.ENHANCE_QUEUE
            later.mkdir()
            source = later / 'photo.png'
            source.write_bytes(png())
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                output, _ = server.save_image(source, corners, {}, 0)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(stages.same_path(output, later / 'photo_c.png'))
            self.assertFalse((later / stages.CROP_QUEUE).exists())
            self.assertFalse((Path(tmp) / stages.ORIGINAL).exists())

    def test_cross_folder_pair_and_merge(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'batch'
            fronts = allowed / 'fronts'
            backs = allowed / 'backs'
            fronts.mkdir(parents=True)
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            front_bytes, back_bytes = front.read_bytes(), back.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                merged, _ = server.save_merged_image(front, back, {}, 'front', 0, root=allowed)
            self.assertTrue(stages.same_path(merged.parent, fronts))
            self.assertEqual(merged.name, 'front_front-back_c.jpg')
            self.assertTrue(front.exists())
            self.assertTrue(back.exists())
            self.assertEqual(front.read_bytes(), front_bytes)
            self.assertEqual(back.read_bytes(), back_bytes)
            self.assertFalse((backs / merged.name).exists())
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                link = photo_pairs.approve_link(front, back, 50, 'right', root=allowed)
            self.assertTrue(stages.same_path(link['front_path'], front))
            self.assertTrue(stages.same_path(link['composite_path'], back))

    def test_read_only_back_folder_still_merges(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'batch'
            fronts = allowed / 'fronts'
            backs = allowed / 'backs'
            fronts.mkdir(parents=True)
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            original = back.read_bytes()
            before = {path.name for path in backs.iterdir()}
            if os.name != 'nt':
                backs.chmod(0o555)
            try:
                with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                    merged, _ = server.save_merged_image(front, back, {}, 'front', 0, root=allowed)
            finally:
                if os.name != 'nt':
                    backs.chmod(0o755)
            self.assertTrue(merged.is_file())
            self.assertTrue(stages.same_path(merged.parent, fronts))
            self.assertEqual(back.read_bytes(), original)
            self.assertEqual({path.name for path in backs.iterdir()}, before)

    def test_missing_back_is_a_clear_error(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'batch'
            fronts = allowed / 'fronts'
            fronts.mkdir(parents=True)
            front = fronts / 'front.jpg'
            Image.new('RGB', (8, 8), 'red').save(front)
            missing = fronts / 'gone.jpg'
            with self.assertRaisesRegex(ValueError, 'Back image not found'):
                server.save_merged_image(front, missing, {}, 'front', 0, root=allowed)
            with self.assertRaisesRegex(ValueError, 'Back image not found'):
                server.save_merged_image(front, '', {}, 'front', 0, root=allowed)
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                with self.assertRaisesRegex(ValueError, 'Back image not found'):
                    photo_pairs.approve_link(front, '', 50, 'right', root=allowed)

            http = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            status, body = self._json(http.server_port, '/api/merge', {
                'folder': str(fronts), 'front_path': str(front), 'back_path': str(missing),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            })
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'Back image not found')

    def test_back_outside_allowed_root_or_traversal_is_refused(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'batch'
            fronts = allowed / 'fronts'
            outside = Path(tmp) / 'outside'
            fronts.mkdir(parents=True)
            outside.mkdir()
            front = fronts / 'front.jpg'
            secret = outside / 'secret.jpg'
            Image.new('RGB', (8, 8), 'red').save(front)
            Image.new('RGB', (8, 8), 'blue').save(secret)
            traversed = fronts / '..' / 'outside' / 'secret.jpg'
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                server.save_merged_image(front, secret, {}, 'front', 0, root=fronts)
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                server.save_merged_image(front, traversed, {}, 'front', 0, root=fronts)
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                    photo_pairs.approve_link(front, secret, 50, 'right', root=fronts)
                with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                    photo_pairs.approve_link(front, traversed, 50, 'right', root=fronts)
            self.assertFalse(any(fronts.glob('*front-back*')))

            http = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            status, body = self._json(http.server_port, '/api/merge', {
                'folder': str(fronts), 'front_path': str(front), 'back_path': str(traversed),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            })
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')

    def test_read_only_output_folder_fails_before_merge_write(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'batch'
            fronts = allowed / 'fronts'
            backs = allowed / 'backs'
            fronts.mkdir(parents=True)
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            error = PermissionError('denied')
            error.filename = str(fronts)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()), \
                 patch.object(stages, 'probe_folder_writable', side_effect=error):
                with self.assertRaises(PermissionError):
                    server.save_merged_image(front, back, {}, 'front', 0, root=allowed)
            self.assertEqual({path.name for path in fronts.iterdir()}, {front.name})
            self.assertEqual({path.name for path in backs.iterdir()}, {back.name})

    def test_copy_mode_overwrite_reports_protected_original(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'keep original.png'
            source.write_bytes(png('red'))
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(
                    filename_template='{name}{ext}', conflict_policy='overwrite')):
                output = stages.file_results([source], [png('blue')], 'restore')[0]
            self.assertEqual(output.name, 'keep original-2.png')
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(stages.saved_protected(output))
            self.assertEqual(stages.protected_original_message(output.name),
                             'Saved as keep original-2.png to protect the original')

        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        folder = Path(self.folder.name) / 'scans'
        folder.mkdir()
        png_source = folder / 'keep original.png'
        png_source.write_bytes(png('red'))
        app_settings.save_settings({
            'filename_template': '{name}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
            'backup_originals': True,
            'output_folder': '',
        })
        with patch.object(server, 'read_links', return_value=[]), \
             patch.object(server, 'edit_front', return_value=png('blue')):
            status, record = self._json(http.server_port, '/api/chatgpt-generate', {
                'front_path': str(png_source), 'operation': 'restore',
            })
            self.assertEqual(status, 200)
            status, accepted = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
            })
        self.assertEqual(status, 200)
        self.assertTrue(accepted.get('protected_original'))
        self.assertEqual(accepted.get('protected_message'),
                         'Saved as keep original-2.png to protect the original')
        self.assertTrue(png_source.exists())
        self.assertTrue((folder / 'keep original-2.png').is_file())

    def test_next_free_index_counts_finish_snapshot_names(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / stages.CROP_QUEUE
            cropped = root / stages.CROPPED
            skipped = root / stages.SKIP_AI
            queue.mkdir(parents=True)
            cropped.mkdir(parents=True)
            skipped.mkdir(parents=True)
            (cropped / 'scan_001.png').write_bytes(png())
            (skipped / 'scan_002.png').write_bytes(png())
            source = root / 'photo.png'
            source.write_bytes(png())
            with patch.object(stages, '_settings', return_value={
                'filename_template': 'scan_{index}{ext}',
                'conflict_policy': 'skip', 'replace_original': False,
                'backup_originals': True, 'output_folder': '',
            }):
                self.assertEqual(stages.next_free_index(source, 'crop', queue), 3)

    def test_index_increments_after_finish_uses_cropped_names(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        root = Path(self.folder.name)
        photo1 = root / 'photo1.png'
        photo2 = root / 'photo2.png'
        Image.new('RGB', (16, 16), 'red').save(photo1)
        Image.new('RGB', (16, 16), 'blue').save(photo2)
        app_settings.save_settings({
            'filename_template': 'scan_{index}{ext}',
            'conflict_policy': 'skip',
            'replace_original': False,
            'backup_originals': True,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        first = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
        })[1]
        self.assertEqual(first['name'], 'scan_001.png')
        status, finished = self._json(http.server_port, '/api/stage-finish', {
            'paths': [first['path']], 'dry_run': False,
        })
        self.assertEqual(status, 200)
        self.assertTrue((root / stages.CROPPED / 'scan_001.png').is_file())
        self.assertFalse((root / stages.CROP_QUEUE / 'scan_001.png').exists())
        status, second = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
        })
        self.assertEqual(status, 200)
        self.assertEqual(second['name'], 'scan_002.png')
        self.assertTrue((root / stages.CROP_QUEUE / 'scan_002.png').is_file())

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

    def _tab(self, tab_id='tabid-test-000001'):
        server.register_tab(tab_id)
        return tab_id

    def _json(self, port, path, body, tab=None):
        headers = {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'}
        if tab:
            headers['X-Cropper-Tab'] = tab
        request = Request(
            f'http://127.0.0.1:{port}{path}',
            json.dumps(body).encode(),
            headers,
        )
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            payload = json.loads(exc.read().decode())
            exc.close()
            return exc.code, payload

    def test_overwrite_confirmation_rejects_replay_and_uncovered_files(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.assertFalse(hasattr(server, 'OVERWRITE_SKIP_SESSION'))
        tab = self._tab()
        other_tab = self._tab('tabid-test-000002')
        queue = Path(self.folder.name) / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        source = queue / 'photo_c.png'
        Image.new('RGB', (8, 8), 'gray').save(source)
        existing = Path(self.folder.name) / stages.AI_QUEUE / 'photo_c_ai.png'
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b'existing')
        other_dir = TemporaryDirectory()
        self.addCleanup(other_dir.cleanup)
        other_root = Path(other_dir.name)
        other_queue = other_root / stages.CROP_QUEUE
        other_queue.mkdir(parents=True)
        other_source = other_queue / 'other_c.png'
        Image.new('RGB', (8, 8), 'gray').save(other_source)
        other_existing = other_root / stages.AI_QUEUE / 'other_c_ai.png'
        other_existing.parent.mkdir(parents=True)
        other_existing.write_bytes(b'existing')
        app_settings.save_settings({'conflict_policy': 'overwrite', 'replace_original': False})

        status, registered = self._json(http.server_port, '/api/tab-register', {'tab_id': tab}, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(registered['registered'])
        status, preview = self._json(http.server_port, '/api/overwrite-preview',
                                     {'path': str(source), 'operation': 'restore'}, tab=tab)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(preview['count'], 1)
        self.assertIn('photo_c_ai.png', preview['names'])
        self.assertTrue(preview['confirmation_token'])
        self.assertNotIn('batch_id', preview)
        self.assertEqual(preview.get('replace_count', 0), 0)

        with patch.object(stages, '_settings', return_value={
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite', 'replace_original': False,
            'backup_originals': True, 'output_folder': '',
        }):
            plans = [stages.plan(source, 'restore')]
            other_plans = [stages.plan(other_source, 'restore')]
            refusal = server.overwrite_response(plans, {}, tab_id=tab)
            self.assertEqual(refusal['code'], 'overwrite_confirmation_required')
            token = refusal['confirmation_token']
            computed = stages.batch_id_for(plans)
            self.assertNotEqual(token, computed)
            self.assertIsNotNone(server.overwrite_response(plans, {'confirm_overwrite': True}, tab_id=tab))
            self.assertIsNotNone(server.overwrite_response(plans, {'skip_overwrite_prompt': True}, tab_id=tab))
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'batch_id': computed, 'confirmation_token': computed,
            }, tab_id=tab))
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': 'forged-token',
            }, tab_id=tab))
            other_session = secrets.token_urlsafe(32)
            server.register_tab(tab, other_session)
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, session=other_session, tab_id=tab))
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id=other_tab))
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id='tabid-unregistered01'))
            self.assertIsNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id=tab))
            other_refusal = server.overwrite_response(other_plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id=tab)
            self.assertIsNotNone(other_refusal)
            self.assertTrue(other_refusal['confirmation_token'])
            self.assertNotEqual(other_refusal['confirmation_token'], token)

            expired = server.overwrite_response(plans, {}, tab_id=tab)
            expired_token = expired['confirmation_token']
            server.CONFIRMATIONS[expired_token]['expires'] = time.time() - 1
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': expired_token,
            }, tab_id=tab))

            restart_token = server.overwrite_response(plans, {}, tab_id=tab)['confirmation_token']
            server.reset_confirmations()
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': restart_token,
            }, tab_id=tab))

        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        crop_source = Path(self.folder.name) / 'photo.png'
        other_crop = Path(self.folder.name) / 'photo2.png'
        Image.new('RGB', (16, 16), 'red').save(crop_source)
        Image.new('RGB', (16, 16), 'blue').save(other_crop)
        crop_existing = Path(self.folder.name) / stages.CROP_QUEUE / 'photo_c.png'
        other_existing_crop = Path(self.folder.name) / stages.CROP_QUEUE / 'photo2_c.png'
        crop_existing.parent.mkdir(parents=True, exist_ok=True)
        crop_existing.write_bytes(b'existing-crop')
        other_existing_crop.write_bytes(b'existing-crop-2')
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            status, body = self._json(http.server_port, '/api/crop', {
                'path': str(crop_source), 'points': corners, 'rotation': 0,
                'skip_overwrite_prompt': True,
            }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'overwrite_confirmation_required')
        crop_token = body['confirmation_token']
        self.assertNotIn(crop_token, stdout.getvalue())
        computed = hashlib.sha256(
            stages.path_key(Path(self.folder.name)).encode('utf-8')
        ).hexdigest()[:32]
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(crop_source), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': computed, 'batch_id': computed,
        }, tab=tab)
        self.assertEqual(status, 409)
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(crop_source), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': crop_token,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(result['name'], 'photo_c.png')
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(other_crop), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': crop_token,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(result['name'], 'photo2_c.png')

        third = Path(self.folder.name) / 'photo3.png'
        fourth = Path(self.folder.name) / 'photo4.png'
        Image.new('RGB', (16, 16), 'green').save(third)
        Image.new('RGB', (16, 16), 'yellow').save(fourth)
        (Path(self.folder.name) / stages.CROP_QUEUE / 'photo3_c.png').write_bytes(b'existing-crop-3')
        (Path(self.folder.name) / stages.CROP_QUEUE / 'photo4_c.png').write_bytes(b'existing-crop-4')
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(third), 'points': corners, 'rotation': 0,
            'confirm_overwrite': True,
        }, tab=tab)
        self.assertEqual(status, 409)
        file_token = body['confirmation_token']
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(third), 'points': corners, 'rotation': 0,
            'confirm_overwrite': True, 'confirmation_token': file_token,
        }, tab=tab)
        self.assertEqual(status, 200)
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            'confirm_overwrite': True, 'confirmation_token': file_token,
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'overwrite_confirmation_required')
        self.assertNotEqual(body['confirmation_token'], file_token)
        photo4_token = body['confirmation_token']
        status, replay = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': crop_token,
        }, tab=other_tab)
        self.assertEqual(status, 409)
        status, unregistered = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': crop_token,
        }, tab='tabid-unregistered01')
        self.assertEqual(status, 409)
        self.assertEqual(unregistered['code'], 'overwrite_confirmation_required')
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            'confirm_overwrite': True, 'confirmation_token': photo4_token,
        }, tab=tab)
        self.assertEqual(status, 200)

    def test_replace_confirmation_once_per_batch_independent_of_conflict_policy(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        other_tab = self._tab('tabid-test-000002')
        root = Path(self.folder.name)
        photo1 = root / 'keep_me.png'
        photo2 = root / 'keep_me_too.png'
        Image.new('RGB', (16, 16), 'red').save(photo1)
        Image.new('RGB', (16, 16), 'blue').save(photo2)
        original1 = photo1.read_bytes()
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip',
            'replace_original': True,
            'backup_originals': True,
        })
        with patch.object(stages, '_settings', return_value={
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'skip', 'replace_original': True,
            'backup_originals': True, 'output_folder': '',
        }):
            plans = [stages.plan(photo1, 'crop')]
            self.assertTrue(plans[0]['replace'])
            self.assertIsNone(server.overwrite_response(plans, {}, tab_id=tab))
            refusal = server.replace_response(plans, {}, tab_id=tab)
            self.assertEqual(refusal['code'], 'replace_confirmation_required')
            token = refusal['confirmation_token']
            self.assertIsNotNone(server.replace_response(plans, {
                'confirm_overwrite': True, 'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id=tab))
            self.assertIsNotNone(server.replace_response(plans, {
                'confirm_replace': True, 'confirmation_token': 'forged-token',
            }, tab_id=tab))
            self.assertIsNotNone(server.replace_response(plans, {
                'confirm_replace': True, 'confirmation_token': token,
            }, tab_id=other_tab))
            self.assertIsNone(server.replace_response(plans, {
                'confirm_replace': True, 'confirmation_token': token,
            }, tab_id=tab))
            other_dir = TemporaryDirectory()
            self.addCleanup(other_dir.cleanup)
            other_photo = Path(other_dir.name) / 'other.png'
            Image.new('RGB', (16, 16), 'green').save(other_photo)
            other_plans = [stages.plan(other_photo, 'crop')]
            second = server.replace_response(other_plans, {
                'confirm_replace': True, 'confirmation_token': token,
            }, tab_id=tab)
            self.assertIsNotNone(second)
            self.assertEqual(second['code'], 'replace_confirmation_required')

        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'replace_confirmation_required')
        token = body['confirmation_token']
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': token,
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'replace_confirmation_required')
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            'confirm_replace': True, 'confirmation_token': token,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(photo1.exists())
        self.assertNotEqual(photo1.read_bytes(), original1)
        self.assertEqual((root / stages.BACKUPS / photo1.name).read_bytes(), original1)
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
            'confirm_replace': True, 'confirmation_token': token,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue((root / stages.BACKUPS / photo2.name).is_file())


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
