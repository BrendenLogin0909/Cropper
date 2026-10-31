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


def pick_via_dialog(port, folder, tab, extra=None):
    headers = {'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': tab, 'Content-Type': 'application/json'}
    with patch.object(server, 'choose_folder_dialog', return_value=Path(folder) if folder is not None else None):
        request = Request(
            f'http://127.0.0.1:{port}/api/pick-folder',
            json.dumps(extra or {}).encode(),
            headers,
        )
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            payload = json.loads(exc.read().decode())
            exc.close()
            return exc.code, payload


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
        tab = self._tab()
        queue = Path(self.folder.name) / stages.CROP_QUEUE
        queue.mkdir()
        first = queue / 'photo1.jpg'
        second = queue / 'photo1.png'
        Image.new('RGB', (8, 8), 'gray').save(first)
        Image.new('RGB', (8, 8), 'gray').save(second)
        self._open_folder(http.server_port, Path(self.folder.name), tab)
        calls = []

        def edit(path, **kwargs):
            calls.append(path)
            return png('blue')

        def post(payload):
            request = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-generate',
                              json.dumps(payload).encode(),
                              {'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': tab, 'Content-Type': 'application/json'})
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
                created = stages._atomic_create(target, png(), overwrite=False)
            self.assertTrue(created)
            self.assertEqual(target.read_bytes(), png())
            replaced.assert_called()
            with self.assertRaises(FileExistsError):
                stages._atomic_create(target, png('blue'), overwrite=False)
            self.assertEqual(target.read_bytes(), png())
            stages._atomic_create(target, png('yellow'), overwrite=True)
            self.assertEqual(target.read_bytes(), png('yellow'))

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
        tab = self._tab()
        self._open_folder(http.server_port, root, tab)

        def crop(path):
            request = Request(
                f'http://127.0.0.1:{http.server_port}/api/crop',
                json.dumps({'path': str(path), 'points': corners, 'rotation': 0}).encode(),
                {'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': tab, 'Content-Type': 'application/json'},
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
            Image.new('RGB', (16, 16), 'red').save(source, 'JPEG')
            original = source.read_bytes()
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(replace_original=True)):
                output = stages.file_results([source], [png('yellow')], 'restore')[0]
            self.assertTrue(stages.same_path(output, source))
            self.assertTrue(source.read_bytes().startswith(b'\xff\xd8'))
            with Image.open(source) as result:
                self.assertEqual(result.format, 'JPEG')
            self.assertNotEqual(source.read_bytes(), png('yellow'))
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
        tab = self._tab()
        folder = Path(self.folder.name) / 'scans'
        dest = Path(self.folder.name) / 'locked-out'
        folder.mkdir()
        dest.mkdir()
        source = folder / '[Scan]_20261027_0004 [B&W].jpg'
        Image.new('RGB', (16, 16), 'gray').save(source)
        self._open_folder(http.server_port, Path(self.folder.name), tab)
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
            }, tab=tab)
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
        self._open_folder(http.server_port, folder, tab)
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
                'batch_id': body['batch_id'],
            }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(source.read_bytes().startswith(b'\xff\xd8'))
        with Image.open(source) as result:
            self.assertEqual(result.format, 'JPEG')
        self.assertNotEqual(source.read_bytes(), png('yellow'))
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
        tab = self._tab()
        folder = Path(self.folder.name) / 'scans'
        folder.mkdir()
        source = folder / '[Scan]_20261027_0004 [B&W].jpg'
        Image.new('RGB', (16, 16), 'gray').save(source)
        self._open_folder(http.server_port, folder, tab)
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
            }, tab=tab)
            self.assertEqual(status, 200)
            status, accepted = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
            }, tab=tab)
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
                merged, _ = server.save_merged_image(front, back, {}, 'front', 0, allowed_roots=[photos])
            self.assertEqual(merged.name, 'front_front-back.jpg')
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
                link = photo_pairs.approve_link(front, combined, 50, 'right', allowed_roots=[front.parent])
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
                merged, _ = server.save_merged_image(
                    front, back, {}, 'front', 0, allowed_roots=[fronts, backs])
            self.assertTrue(stages.same_path(merged.parent, fronts))
            self.assertEqual(merged.name, 'front_front-back.jpg')
            self.assertTrue(front.exists())
            self.assertTrue(back.exists())
            self.assertEqual(front.read_bytes(), front_bytes)
            self.assertEqual(back.read_bytes(), back_bytes)
            self.assertFalse((backs / merged.name).exists())
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                link = photo_pairs.approve_link(
                    front, back, 50, 'right', allowed_roots=[fronts, backs])
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
                    merged, _ = server.save_merged_image(
                        front, back, {}, 'front', 0, allowed_roots=[fronts, backs])
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
                server.save_merged_image(front, missing, {}, 'front', 0, allowed_roots=[fronts])
            with self.assertRaisesRegex(ValueError, 'Back image not found'):
                server.save_merged_image(front, '', {}, 'front', 0, allowed_roots=[fronts])
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                with self.assertRaisesRegex(ValueError, 'Back image not found'):
                    photo_pairs.approve_link(front, '', 50, 'right', allowed_roots=[fronts])

            http = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            tab = self._tab()
            self._open_folder(http.server_port, fronts, tab)
            status, body = self._json(http.server_port, '/api/merge', {
                'folder': str(fronts), 'front_path': str(front), 'back_path': str(missing),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            }, tab=tab)
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
                server.save_merged_image(front, secret, {}, 'front', 0, allowed_roots=[fronts])
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                server.save_merged_image(front, traversed, {}, 'front', 0, allowed_roots=[fronts])
            with patch.object(photo_pairs, 'ROOT', Path(tmp) / 'links'):
                with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                    photo_pairs.approve_link(front, secret, 50, 'right', allowed_roots=[fronts])
                with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                    photo_pairs.approve_link(front, traversed, 50, 'right', allowed_roots=[fronts])
            self.assertFalse(any(fronts.glob('*front-back*')))

            http = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            tab = self._tab()
            self._open_folder(http.server_port, fronts, tab)
            status, body = self._json(http.server_port, '/api/merge', {
                'folder': str(fronts), 'front_path': str(front), 'back_path': str(traversed),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            }, tab=tab)
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
                    server.save_merged_image(front, back, {}, 'front', 0, allowed_roots=[fronts, backs])
            self.assertEqual({path.name for path in fronts.iterdir()}, {front.name})
            self.assertEqual({path.name for path in backs.iterdir()}, {back.name})

    def test_merge_name_from_back_does_not_use_crop_suffix(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'scans'
            folder.mkdir()
            front = folder / 'x front.jpg'
            back = folder / 'x back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                merged, _ = server.save_merged_image(
                    front, back, {}, 'back', 0, allowed_roots=[folder])
            self.assertEqual(merged.name, 'x back_front-back.jpg')
            self.assertNotIn('_c', merged.stem)

    def test_picked_second_folder_back_is_accepted(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        with TemporaryDirectory() as tmp:
            fronts = Path(tmp) / 'fronts'
            backs = Path(tmp) / 'backs'
            fronts.mkdir()
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            self._open_folder(http.server_port, fronts, tab)
            self._open_folder(http.server_port, backs, tab)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                }, tab=tab)
            self.assertEqual(status, 200)
            self.assertEqual(body['name'], 'front_front-back.jpg')

    def test_unpicked_folder_and_client_folder_field_are_rejected(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        with TemporaryDirectory() as tmp:
            fronts = Path(tmp) / 'fronts'
            backs = Path(tmp) / 'backs'
            fronts.mkdir()
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            self._open_folder(http.server_port, fronts, tab)
            payload = {
                'front_path': str(front), 'back_path': str(back),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            }
            status, body = self._json(http.server_port, '/api/merge', payload, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            status, body = self._json(http.server_port, '/api/merge', {
                **payload, 'folder': str(backs),
            }, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            status, body = self._json(http.server_port, '/api/merge', {
                **payload, 'folder': '/',
            }, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            browse = self._get(http.server_port, f'/api/folders?path={quote(str(backs))}', tab=tab)
            self.assertEqual(browse[0], 200)
            status, body = self._json(http.server_port, '/api/merge', payload, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')

    def test_missing_folder_field_and_slash_cannot_grant_unpicked_back(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        with TemporaryDirectory() as tmp:
            fronts = Path(tmp) / 'fronts'
            backs = Path(tmp) / 'backs'
            fronts.mkdir()
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (8, 8), 'red').save(front)
            Image.new('RGB', (8, 8), 'blue').save(back)
            self._open_folder(http.server_port, fronts, tab)
            for extra in ({}, {'folder': '/'}):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                    **extra,
                }, tab=tab)
                self.assertEqual(status, 400)
                self.assertRegex(body['error'], 'outside the allowed folder')

    def test_symlink_escape_through_resolve_in_root_is_refused(self):
        with TemporaryDirectory() as tmp:
            fronts = Path(tmp) / 'fronts'
            outside = Path(tmp) / 'outside'
            fronts.mkdir()
            outside.mkdir()
            front = fronts / 'front.jpg'
            secret = outside / 'secret.jpg'
            Image.new('RGB', (8, 8), 'red').save(front)
            Image.new('RGB', (8, 8), 'blue').save(secret)
            link = fronts / 'escape.jpg'
            try:
                link.symlink_to(secret)
            except OSError:
                self.skipTest('symlinks are not available')
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                stages.resolve_in_root(link, fronts, label='Back image')
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                server.save_merged_image(front, link, {}, 'front', 0, allowed_roots=[fronts])

    def test_allowed_root_registered_on_one_tab_cannot_be_used_from_another(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        other = self._tab('tabid-test-000002')
        with TemporaryDirectory() as tmp:
            fronts = Path(tmp) / 'fronts'
            backs = Path(tmp) / 'backs'
            fronts.mkdir()
            backs.mkdir()
            front = fronts / 'front.jpg'
            back = backs / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            self._open_folder(http.server_port, fronts, tab)
            self._open_folder(http.server_port, backs, tab)
            self._open_folder(http.server_port, fronts, other)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                    'folder': str(backs),
                }, tab=other)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                }, tab=tab)
            self.assertEqual(status, 200)
            self.assertEqual(body['name'], 'front_front-back.jpg')

    def test_merge_overwrite_requires_tab_bound_confirmation(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        other = self._tab('tabid-test-000002')
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'scans'
            folder.mkdir()
            front = folder / 'front.jpg'
            back = folder / 'back.jpg'
            Image.new('RGB', (16, 20), 'red').save(front)
            Image.new('RGB', (16, 20), 'blue').save(back)
            existing = folder / 'front_front-back.jpg'
            existing.write_bytes(b'existing-merge')
            self._open_folder(http.server_port, folder, tab)
            self._open_folder(http.server_port, folder, other)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(conflict_policy='overwrite')):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                }, tab=tab)
            self.assertEqual(status, 409)
            self.assertEqual(body['code'], 'overwrite_confirmation_required')
            token = body['confirmation_token']
            batch_id = body['batch_id']
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(conflict_policy='overwrite')):
                status, other_body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                    'confirm_overwrite': True, 'confirmation_token': token,
                    'batch_id': batch_id,
                }, tab=other)
            self.assertEqual(status, 409)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(conflict_policy='overwrite')):
                status, body = self._json(http.server_port, '/api/merge', {
                    'front_path': str(front), 'back_path': str(back),
                    'name_source': 'front', 'back_rotation': 0, 'settings': {},
                    'confirm_overwrite': True, 'confirmation_token': token,
                    'batch_id': batch_id,
                }, tab=tab)
            self.assertEqual(status, 200)
            self.assertEqual(body['name'], 'front_front-back.jpg')
            self.assertNotEqual(existing.read_bytes(), b'existing-merge')

    def test_probe_does_not_create_missing_stage_folder(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / stages.AI_QUEUE
            stages.probe_folder_writable(missing)
            self.assertFalse(missing.exists())
            self.assertEqual([path.name for path in Path(tmp).iterdir() if path.name.startswith('.cropper-preflight-')], [])

    def test_failed_ai_generate_does_not_leave_empty_stage_folder(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        queue = Path(self.folder.name) / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        source = queue / 'photo_c.png'
        source.write_bytes(png())
        self._open_folder(http.server_port, Path(self.folder.name), tab)
        with patch.object(server, 'edit_front', side_effect=RuntimeError('paid call failed')):
            status, body = self._json(http.server_port, '/api/chatgpt-generate', {
                'front_path': str(source), 'operation': 'restore',
            }, tab=tab)
        self.assertNotEqual(status, 200)
        self.assertFalse((Path(self.folder.name) / stages.AI_QUEUE).exists())

    def test_replace_alpha_result_writes_png_and_keeps_backup(self):
        with TemporaryDirectory() as tmp:
            photos = Path(tmp) / 'scans'
            photos.mkdir()
            source = photos / 'photo.jpg'
            Image.new('RGB', (16, 16), 'red').save(source, 'JPEG')
            original = source.read_bytes()
            payload = io.BytesIO()
            Image.new('RGBA', (16, 16), (0, 255, 0, 80)).save(payload, 'PNG')
            with patch.object(stages, '_settings', return_value=self._non_stage_settings(replace_original=True)):
                output = stages.file_results([source], [payload.getvalue()], 'restore')[0]
            self.assertEqual(output.suffix.lower(), '.png')
            self.assertEqual(output.name, 'photo.png')
            self.assertTrue((photos / stages.BACKUPS / 'photo.jpg').is_file())
            self.assertEqual((photos / stages.BACKUPS / 'photo.jpg').read_bytes(), original)
            with Image.open(output) as result:
                self.assertEqual(result.format, 'PNG')
                self.assertIn('A', result.mode)

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
        tab = self._tab()
        folder = Path(self.folder.name) / 'scans'
        folder.mkdir()
        png_source = folder / 'keep original.png'
        png_source.write_bytes(png('red'))
        self._open_folder(http.server_port, folder, tab)
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
            }, tab=tab)
            self.assertEqual(status, 200)
            status, accepted = self._json(http.server_port, '/api/chatgpt-accept', {
                'id': record['id'],
            }, tab=tab)
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
        tab = self._tab()
        self._open_folder(http.server_port, root, tab)
        first = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
        }, tab=tab)[1]
        self.assertEqual(first['name'], 'scan_001.png')
        status, finished = self._json(http.server_port, '/api/stage-finish', {
            'paths': [first['path']], 'dry_run': False,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue((root / stages.CROPPED / 'scan_001.png').is_file())
        self.assertFalse((root / stages.CROP_QUEUE / 'scan_001.png').exists())
        status, second = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
        }, tab=tab)
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

    def _ack(self, body, **flags):
        ack = {
            'confirmation_token': body['confirmation_token'],
            'batch_id': body['batch_id'],
        }
        ack.update(flags)
        return ack

    def _get(self, port, path, tab=None):
        headers = {'X-Cropper-Token': server.TOKEN}
        if tab:
            headers['X-Cropper-Tab'] = tab
        request = Request(f'http://127.0.0.1:{port}{path}', headers=headers)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            payload = json.loads(exc.read().decode())
            exc.close()
            return exc.code, payload

    def _pick(self, port, folder, tab, extra=None):
        status, body = pick_via_dialog(port, folder, tab, extra)
        return status, body

    def _open_folder(self, port, folder, tab):
        status, body = self._pick(port, folder, tab)
        self.assertEqual(status, 200)
        self.assertFalse(body.get('cancelled'))
        return body

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
        self._open_folder(http.server_port, Path(self.folder.name), tab)
        self._open_folder(http.server_port, Path(self.folder.name), other_tab)
        status, preview = self._json(http.server_port, '/api/overwrite-preview',
                                     {'path': str(source), 'operation': 'restore'}, tab=tab)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(preview['count'], 1)
        self.assertIn('photo_c_ai.png', preview['names'])
        self.assertTrue(preview['confirmation_token'])
        self.assertTrue(preview.get('batch_id'))
        self.assertNotEqual(preview['batch_id'], stages.batch_id_for([stages.plan(source, 'restore')]))
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
            self.assertIsNotNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
            }, tab_id=tab))
            self.assertIsNone(server.overwrite_response(plans, {
                'skip_overwrite_prompt': True, 'confirmation_token': token,
                'batch_id': refusal['batch_id'],
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
            status, first_409 = self._json(http.server_port, '/api/crop', {
                'path': str(crop_source), 'points': corners, 'rotation': 0,
                'skip_overwrite_prompt': True,
            }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(first_409['code'], 'overwrite_confirmation_required')
        crop_token = first_409['confirmation_token']
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
            **self._ack(first_409, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(result['name'], 'photo_c.png')
        self.assertFalse(result.get('confirmation_token'))
        status, reused = self._json(http.server_port, '/api/crop', {
            'path': str(other_crop), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': crop_token,
            'batch_id': first_409['batch_id'],
        }, tab=tab)
        self.assertEqual(status, 409)

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
        file_batch = body['batch_id']
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(third), 'points': corners, 'rotation': 0,
            **self._ack(body, confirm_overwrite=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            'confirm_overwrite': True, 'confirmation_token': file_token,
            'batch_id': file_batch,
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
        self.assertEqual(status, 400)
        self.assertRegex(unregistered['error'], 'outside the allowed folder')
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(fourth), 'points': corners, 'rotation': 0,
            **self._ack(body, confirm_overwrite=True),
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
        self._open_folder(http.server_port, root, tab)
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
            self.assertIsNotNone(server.replace_response(plans, {
                'confirm_replace': True, 'confirmation_token': token,
            }, tab_id=tab))
            self.assertIsNone(server.replace_response(plans, {
                'confirm_replace': True, 'confirmation_token': token,
                'batch_id': refusal['batch_id'],
            }, tab_id=tab))
            other_dir = TemporaryDirectory()
            self.addCleanup(other_dir.cleanup)
            other_photo = Path(other_dir.name) / 'other.png'
            Image.new('RGB', (16, 16), 'green').save(other_photo)
            other_plans = [stages.plan(other_photo, 'crop')]
            second = server.replace_response(other_plans, {
                'confirm_replace': True, 'confirmation_token': token,
                'batch_id': refusal['batch_id'],
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
        replace_batch = body['batch_id']
        status, skipped = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            'skip_overwrite_prompt': True, 'confirmation_token': token,
            'batch_id': replace_batch,
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(skipped['code'], 'replace_confirmation_required')
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            'confirm_replace': True, 'confirmation_token': token,
            'batch_id': replace_batch,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(photo1.exists())
        self.assertNotEqual(photo1.read_bytes(), original1)
        self.assertEqual((root / stages.BACKUPS / photo1.name).read_bytes(), original1)
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
            'confirm_replace': True, 'confirmation_token': token,
            'batch_id': replace_batch,
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue((root / stages.BACKUPS / photo2.name).is_file())

    def test_confirm_overwrite_batch_issues_batch_token(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        root = Path(self.folder.name)
        photo1 = root / 'photo.png'
        photo2 = root / 'photo2.png'
        Image.new('RGB', (16, 16), 'red').save(photo1)
        Image.new('RGB', (16, 16), 'blue').save(photo2)
        queue = root / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        (queue / 'photo_c.png').write_bytes(png('gray'))
        (queue / 'photo2_c.png').write_bytes(png('gray'))
        self._open_folder(http.server_port, root, tab)
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        status, preview = self._json(http.server_port, '/api/overwrite-preview', {
            'paths': [str(photo1), str(photo2)], 'operation': 'crop',
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(preview['count'], 2)
        status, confirmed = self._json(http.server_port, '/api/confirm-overwrite-batch', {
            **self._ack(preview, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(confirmed['confirmation_token'])
        self.assertNotEqual(confirmed['confirmation_token'], preview['confirmation_token'])
        self.assertEqual(confirmed['batch_id'], preview['batch_id'])
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, first = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            **self._ack(confirmed, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(first['name'], 'photo_c.png')
        status, second = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
            **self._ack(confirmed, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertEqual(second['name'], 'photo2_c.png')

    def test_batch_token_rejected_for_unlisted_file(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        root = Path(self.folder.name)
        listed = root / 'listed.png'
        extra = root / 'extra.png'
        Image.new('RGB', (16, 16), 'red').save(listed)
        Image.new('RGB', (16, 16), 'blue').save(extra)
        queue = root / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        (queue / 'listed_c.png').write_bytes(png('gray'))
        (queue / 'extra_c.png').write_bytes(png('gray'))
        self._open_folder(http.server_port, root, tab)
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, refusal = self._json(http.server_port, '/api/crop', {
            'path': str(listed), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 409)
        status, confirmed = self._json(http.server_port, '/api/confirm-overwrite-batch', {
            **self._ack(refusal, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(extra), 'points': corners, 'rotation': 0,
            **self._ack(confirmed, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'overwrite_confirmation_required')

    def test_file_token_plus_skip_never_widens(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        root = Path(self.folder.name)
        photo1 = root / 'photo.png'
        photo2 = root / 'photo2.png'
        Image.new('RGB', (16, 16), 'red').save(photo1)
        Image.new('RGB', (16, 16), 'blue').save(photo2)
        queue = root / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        (queue / 'photo_c.png').write_bytes(png('gray'))
        (queue / 'photo2_c.png').write_bytes(png('gray'))
        self._open_folder(http.server_port, root, tab)
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, refusal = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 409)
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo1), 'points': corners, 'rotation': 0,
            **self._ack(refusal, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertFalse(result.get('confirmation_token'))
        status, second = self._json(http.server_port, '/api/crop', {
            'path': str(photo2), 'points': corners, 'rotation': 0,
            **self._ack(refusal, skip_overwrite_prompt=True),
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(second['code'], 'overwrite_confirmation_required')

    def test_bogus_batch_id_is_rejected(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        root = Path(self.folder.name)
        photo = root / 'photo.png'
        Image.new('RGB', (16, 16), 'red').save(photo)
        queue = root / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        (queue / 'photo_c.png').write_bytes(png('gray'))
        self._open_folder(http.server_port, root, tab)
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, refusal = self._json(http.server_port, '/api/crop', {
            'path': str(photo), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 409)
        computed = stages.batch_id_for([stages.plan(photo, 'crop')])
        for bogus in ('', 'WRONG', 'forged-batch', computed, refusal['confirmation_token']):
            status, body = self._json(http.server_port, '/api/crop', {
                'path': str(photo), 'points': corners, 'rotation': 0,
                'skip_overwrite_prompt': True,
                'confirmation_token': refusal['confirmation_token'],
                'batch_id': bogus,
            }, tab=tab)
            self.assertEqual(status, 409, bogus)
            self.assertEqual(body['code'], 'overwrite_confirmation_required')
            status, confirmed = self._json(http.server_port, '/api/confirm-overwrite-batch', {
                'skip_overwrite_prompt': True,
                'confirmation_token': refusal['confirmation_token'],
                'batch_id': bogus,
            }, tab=tab)
            self.assertEqual(status, 400, bogus)

    def test_file_token_cannot_be_replayed(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        root = Path(self.folder.name)
        photo = root / 'photo.png'
        Image.new('RGB', (16, 16), 'red').save(photo)
        queue = root / stages.CROP_QUEUE
        queue.mkdir(parents=True)
        existing = queue / 'photo_c.png'
        existing.write_bytes(png('gray'))
        self._open_folder(http.server_port, root, tab)
        app_settings.save_settings({
            'filename_template': '{name}{suffix}{ext}',
            'conflict_policy': 'overwrite',
            'replace_original': False,
        })
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, refusal = self._json(http.server_port, '/api/crop', {
            'path': str(photo), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 409)
        status, result = self._json(http.server_port, '/api/crop', {
            'path': str(photo), 'points': corners, 'rotation': 0,
            **self._ack(refusal, confirm_overwrite=True),
        }, tab=tab)
        self.assertEqual(status, 200)
        Image.new('RGB', (16, 16), 'red').save(photo)
        existing.write_bytes(png('gray'))
        status, replay = self._json(http.server_port, '/api/crop', {
            'path': str(photo), 'points': corners, 'rotation': 0,
            **self._ack(refusal, confirm_overwrite=True),
        }, tab=tab)
        self.assertEqual(status, 409)
        self.assertEqual(replay['code'], 'overwrite_confirmation_required')

    def test_enhance_rejects_stored_link_outside_tab_roots(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        allowed = Path(self.folder.name) / 'allowed'
        outside = Path(self.folder.name) / 'outside'
        allowed.mkdir()
        outside.mkdir()
        front = allowed / 'front.png'
        secret = outside / 'secret.png'
        Image.new('RGB', (16, 16), 'red').save(front)
        Image.new('RGB', (32, 16), 'blue').save(secret)
        self._open_folder(http.server_port, allowed, tab)
        store = Path(self.folder.name) / 'links'
        store.mkdir()
        (store / 'photo-links.json').write_text(json.dumps({
            str(front): {
                'front_path': str(front),
                'composite_path': str(secret),
                'split': 50,
                'layout': 'right',
            },
        }), encoding='utf-8')
        with patch.object(photo_pairs, 'ROOT', store):
            status, body = self._json(http.server_port, '/api/enhance', {
                'path': str(front), 'settings': {},
            }, tab=tab)
        self.assertEqual(status, 400)
        self.assertRegex(body['error'], 'outside the allowed folder')

    def test_accept_rejects_stored_link_outside_tab_roots(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        allowed = Path(self.folder.name) / 'allowed'
        outside = Path(self.folder.name) / 'outside'
        allowed.mkdir()
        outside.mkdir()
        front = allowed / 'front.png'
        secret = outside / 'secret.png'
        Image.new('RGB', (16, 16), 'red').save(front)
        Image.new('RGB', (32, 16), 'blue').save(secret)
        self._open_folder(http.server_port, allowed, tab)
        record = chatgpt_pending.save_pending(front, png('yellow'))
        chatgpt_pending.attach_link(record['id'], {
            'front_path': str(front),
            'composite_path': str(secret),
            'split': 50,
            'layout': 'right',
        })
        status, body = self._json(http.server_port, '/api/chatgpt-accept', {
            'id': record['id'],
        }, tab=tab)
        self.assertEqual(status, 400)
        self.assertRegex(body['error'], 'outside the allowed folder')

    def test_dialog_pick_is_registered_and_cancel_list_browse_forged_path_are_not(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        with TemporaryDirectory() as tmp:
            chosen = Path(tmp) / 'picked'
            other = Path(tmp) / 'other'
            chosen.mkdir()
            other.mkdir()
            front = chosen / 'front.jpg'
            back = other / 'back.jpg'
            Image.new('RGB', (8, 8), 'red').save(front)
            Image.new('RGB', (8, 8), 'blue').save(back)
            status, body = self._pick(http.server_port, chosen, tab, extra={'path': str(other)})
            self.assertEqual(status, 200)
            self.assertFalse(body.get('cancelled'))
            self.assertTrue(stages.same_path(body['path'], chosen))
            listed = self._get(http.server_port, f'/api/list?path={quote(str(other))}', tab=tab)
            self.assertEqual(listed[0], 200)
            browsed = self._get(http.server_port, f'/api/folders?path={quote(str(other))}', tab=tab)
            self.assertEqual(browsed[0], 200)
            payload = {
                'front_path': str(front), 'back_path': str(back),
                'name_source': 'front', 'back_rotation': 0, 'settings': {},
            }
            status, body = self._json(http.server_port, '/api/merge', payload, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            status, cancelled = self._pick(http.server_port, None, tab)
            self.assertEqual(status, 200)
            self.assertTrue(cancelled.get('cancelled'))
            status, body = self._json(http.server_port, '/api/merge', payload, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'outside the allowed folder')
            self._open_folder(http.server_port, other, tab)
            with patch.object(stages, '_settings', return_value=self._non_stage_settings()):
                status, body = self._json(http.server_port, '/api/merge', payload, tab=tab)
            self.assertEqual(status, 200)

    def test_drive_root_and_parent_of_root_are_refused(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        with TemporaryDirectory() as tmp:
            child = Path(tmp) / 'batch'
            child.mkdir()
            self._open_folder(http.server_port, child, tab)
            status, body = self._pick(http.server_port, Path(tmp), tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'parent')
            drive = Path(tmp)
            while drive.parent != drive:
                drive = drive.parent
            status, body = self._pick(http.server_port, drive, tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'root')

    def test_tab_registrations_are_capped_and_idle_tabs_expire(self):
        folder = Path(self.folder.name)
        first_tab = self._tab()
        server.register_allowed_root(folder, first_tab)
        self.addCleanup(server.reset_tabs)
        self.assertTrue(server.allowed_roots_for(first_tab))
        now = time.time()
        first = (server.TOKEN, first_tab)
        server.TABS[first] = now - 100
        for index in range(31):
            tid = f'tabid-cap-{index:08d}'
            server.register_tab(tid)
            server.TABS[(server.TOKEN, tid)] = now - 50 + index
        server.register_tab('tabid-cap-overflow01')
        self.assertTrue(server.tab_is_registered('tabid-cap-overflow01'))
        self.assertFalse(server.tab_is_registered(first_tab))
        self.assertEqual(server.allowed_roots_for(first_tab), [])
        expired = (server.TOKEN, 'tabid-cap-00000000')
        server.TABS[expired] = time.time() - server.TAB_IDLE_SECONDS - 1
        self.assertFalse(server.tab_is_registered('tabid-cap-00000000'))

    def test_atomic_create_reencodes_or_refuses_mismatched_bytes(self):
        with TemporaryDirectory() as tmp:
            jpg = Path(tmp) / 'photo.jpg'
            stages._atomic_create(jpg, png('red'), overwrite=False)
            self.assertTrue(jpg.read_bytes().startswith(b'\xff\xd8'))
            with Image.open(jpg) as image:
                self.assertEqual(image.format, 'JPEG')
            alpha = io.BytesIO()
            Image.new('RGBA', (8, 8), (0, 255, 0, 80)).save(alpha, 'PNG')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                stages._atomic_create(Path(tmp) / 'alpha.jpg', alpha.getvalue(), overwrite=False)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                stages._atomic_create(Path(tmp) / 'broken.jpg', b'not-an-image', overwrite=False)

    def test_finish_overwrite_crop_and_enhance_reject_paths_outside_tab_roots(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        allowed = Path(self.folder.name) / 'allowed'
        outside = Path(self.folder.name) / 'outside'
        allowed.mkdir()
        outside.mkdir()
        inside = allowed / 'inside.png'
        secret = outside / 'secret.png'
        Image.new('RGB', (16, 16), 'red').save(inside)
        Image.new('RGB', (16, 16), 'blue').save(secret)
        self._open_folder(http.server_port, allowed, tab)
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        for endpoint, payload in (
            ('/api/crop', {'path': str(secret), 'points': corners, 'rotation': 0}),
            ('/api/enhance', {'path': str(secret), 'settings': {}}),
            ('/api/overwrite-preview', {'path': str(secret), 'operation': 'crop'}),
            ('/api/stage-finish', {'paths': [str(secret)], 'dry_run': True}),
        ):
            status, body = self._json(http.server_port, endpoint, payload, tab=tab)
            self.assertEqual(status, 400, endpoint)
            self.assertRegex(body['error'], 'outside the allowed folder')

    def test_output_folder_setting_must_stay_inside_allowed_roots(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        allowed = Path(self.folder.name) / 'allowed'
        outside = Path(self.folder.name) / 'outside'
        allowed.mkdir()
        outside.mkdir()
        self._open_folder(http.server_port, allowed, tab)
        status, body = self._json(http.server_port, '/api/settings', {
            'output_folder': str(outside),
        }, tab=tab)
        self.assertEqual(status, 400)
        self.assertRegex(body['error'], 'outside the allowed folder')
        status, body = self._json(http.server_port, '/api/settings', {
            'output_folder': str(allowed),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertTrue(stages.same_path(body['output_folder'], allowed))

    def test_production_dialog_has_no_env_stub(self):
        text = Path(server.__file__).read_text(encoding='utf-8')
        self.assertNotIn('CROPPER_TEST_DIALOG_FOLDER', text)

    def test_request_log_drops_query_strings(self):
        printed = []

        def capturing(*args, **kwargs):
            printed.append(' '.join(str(item) for item in args))

        dummy = type('Dummy', (), {
            'path': '/api/image?path=/secret/photo.png&token=leak-token&tab=raw-tab-id',
            'command': 'GET',
            'address_string': lambda self: '127.0.0.1',
        })()
        with patch('builtins.print', capturing):
            server.Handler.log_message(dummy, '%s', 'should-not-appear')
        blob = '\n'.join(printed)
        self.assertIn('/api/image', blob)
        self.assertNotIn('?', blob)
        self.assertNotIn('path=', blob)
        self.assertNotIn('token=', blob)
        self.assertNotIn('secret', blob)
        self.assertNotIn('leak-token', blob)
        self.assertNotIn('raw-tab-id', blob)
        self.assertNotIn('should-not-appear', blob)

    def _bytes(self, port, path, tab=None, token=True):
        headers = {}
        if token:
            headers['X-Cropper-Token'] = server.TOKEN
        if tab:
            headers['X-Cropper-Tab'] = tab
        request = Request(f'http://127.0.0.1:{port}{path}', headers=headers)
        try:
            with urlopen(request) as response:
                return response.status, response.read(), response.headers.get('Content-Type', '')
        except HTTPError as exc:
            payload = exc.read()
            ctype = exc.headers.get('Content-Type', '') if exc.headers else ''
            exc.close()
            return exc.code, payload, ctype

    def test_images_require_tab_header_and_render(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        folder = Path(self.folder.name) / 'photos'
        folder.mkdir()
        photo = folder / 'tile.png'
        Image.new('RGB', (32, 32), 'red').save(photo)
        self._open_folder(http.server_port, folder, tab)
        quoted = quote(str(photo))
        status, body, ctype = self._bytes(http.server_port, f'/api/thumb?path={quoted}', tab=tab)
        self.assertEqual(status, 200)
        self.assertIn('image/jpeg', ctype)
        self.assertTrue(body.startswith(b'\xff\xd8'))
        Image.open(io.BytesIO(body)).verify()
        status, body, ctype = self._bytes(http.server_port, f'/api/image?path={quoted}', tab=tab)
        self.assertEqual(status, 200)
        self.assertIn('image/jpeg', ctype)
        self.assertTrue(body.startswith(b'\xff\xd8'))
        Image.open(io.BytesIO(body)).verify()
        status, body, _ = self._bytes(http.server_port, f'/api/image?path={quoted}')
        self.assertEqual(status, 400)
        status, body, _ = self._bytes(
            http.server_port, f'/api/image?path={quoted}&tab={tab}')
        self.assertEqual(status, 400)

    def test_foreign_tab_output_folder_is_refused_before_write(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab_a = self._tab('tabid-test-root-aaa1')
        tab_c = self._tab('tabid-test-root-ccc1')
        root = Path(self.folder.name)
        folder_a = root / 'A'
        folder_c = root / 'C'
        out = folder_a / 'out'
        folder_a.mkdir()
        folder_c.mkdir()
        out.mkdir()
        photo_c = folder_c / 'photo9.jpg'
        Image.new('RGB', (16, 16), 'red').save(photo_c)
        original = photo_c.read_bytes()
        self._open_folder(http.server_port, folder_a, tab_a)
        status, body = self._json(http.server_port, '/api/settings', {
            'output_folder': str(out),
        }, tab=tab_a)
        self.assertEqual(status, 200)
        self._open_folder(http.server_port, folder_c, tab_c)
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(photo_c), 'points': corners, 'rotation': 0,
        }, tab=tab_c)
        self.assertEqual(status, 400)
        self.assertRegex(body['error'], 'outside')
        self.assertEqual(photo_c.read_bytes(), original)
        self.assertEqual(list(out.iterdir()), [])

    def test_new_output_subfolder_is_created_on_first_write(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        folder = Path(self.folder.name) / 'A'
        folder.mkdir()
        photo = folder / 'photo.jpg'
        Image.new('RGB', (16, 16), 'red').save(photo)
        dest = folder / 'new-out'
        self.assertFalse(dest.exists())
        self._open_folder(http.server_port, folder, tab)
        status, body = self._json(http.server_port, '/api/settings', {
            'output_folder': str(dest),
        }, tab=tab)
        self.assertEqual(status, 200)
        self.assertFalse(dest.exists())
        corners = [[0, 0], [15, 0], [15, 15], [0, 15]]
        status, body = self._json(http.server_port, '/api/crop', {
            'path': str(photo), 'points': corners, 'rotation': 0,
        }, tab=tab)
        self.assertEqual(status, 200, body)
        self.assertTrue(dest.is_dir())
        self.assertTrue(Path(body['path']).is_file())
        self.assertTrue(stages.contained_in(body['path'], dest))

    def test_pick_folder_validates_session_and_tab_before_dialog(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        opened = []

        def fake():
            opened.append(True)
            return Path(self.folder.name)

        with patch.object(server, 'choose_folder_dialog', side_effect=fake):
            request = Request(
                f'http://127.0.0.1:{http.server_port}/api/pick-folder',
                b'{}',
                {'Content-Type': 'application/json', 'X-Cropper-Tab': 'tabid-test-000001'},
            )
            try:
                with urlopen(request) as response:
                    status = response.status
            except HTTPError as exc:
                status = exc.code
                exc.close()
            self.assertEqual(status, 403)
            self.assertEqual(opened, [])
            status, body = self._json(http.server_port, '/api/pick-folder', {})
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'register')
            self.assertEqual(opened, [])
            status, body = self._json(
                http.server_port, '/api/pick-folder', {}, tab='tabid-unregistered01')
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'register')
            self.assertEqual(opened, [])

    def test_pick_folder_refuses_second_dialog_while_open(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        started = threading.Event()
        release = threading.Event()

        def hang(result, errors, cancel):
            started.set()
            release.wait(10)
            result.append(str(Path(self.folder.name)))

        with patch.object(server, '_run_folder_dialog', side_effect=hang):
            first = {}

            def opener():
                first['status'], first['body'] = self._json(
                    http.server_port, '/api/pick-folder', {}, tab=tab)

            thread = threading.Thread(target=opener)
            thread.start()
            self.assertTrue(started.wait(5))
            status, body = self._json(http.server_port, '/api/pick-folder', {}, tab=tab)
            self.assertEqual(status, 400)
            self.assertRegex(body['error'], 'already open')
            release.set()
            thread.join(10)
        self.assertEqual(first.get('status'), 200)

    def test_pick_folder_timeout_cancels_and_does_not_open_another(self):
        http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tab = self._tab()
        cancel_seen = []

        def hang(result, errors, cancel):
            if not cancel.wait(5):
                errors.append(RuntimeError('cancel was not set'))
                return
            cancel_seen.append(True)

        with patch.object(server, 'DIALOG_TIMEOUT_SECONDS', 0.2), \
             patch.object(server, '_run_folder_dialog', side_effect=hang):
            status, body = self._json(http.server_port, '/api/pick-folder', {}, tab=tab)
        self.assertEqual(status, 400)
        self.assertRegex(body['error'], 'timed out')
        self.assertEqual(cancel_seen, [True])

        def quick(result, errors, cancel):
            result.append(str(Path(self.folder.name)))

        with patch.object(server, '_run_folder_dialog', side_effect=quick):
            status, body = self._json(http.server_port, '/api/pick-folder', {}, tab=tab)
        self.assertEqual(status, 200)
        self.assertFalse(body.get('cancelled'))


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

    def test_windows_junction_escape_is_refused(self):
        with TemporaryDirectory() as tmp:
            allowed = Path(tmp) / 'allowed'
            outside = Path(tmp) / 'outside'
            allowed.mkdir()
            outside.mkdir()
            secret = outside / 'secret.jpg'
            Image.new('RGB', (8, 8), 'red').save(secret)
            link = allowed / 'escape'
            _create_windows_junction(link, outside)
            self.assertTrue(link.is_junction())
            self.assertTrue(stages.is_link(link))
            with self.assertRaisesRegex(ValueError, 'outside the allowed folder'):
                stages.resolve_in_root(link / 'secret.jpg', allowed, label='That image')

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


def _create_windows_junction(link: Path, target: Path) -> None:
    import subprocess
    completed = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(link), str(target)],
        capture_output=True, text=True,
    )
    if completed.returncode == 0:
        return
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except Exception as exc:
        raise AssertionError(
            f'could not create a junction ({completed.stderr or completed.stdout or exc})'
        ) from exc


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
            tab = 'tabid-test-000001'
            server.reset_tabs(); server.reset_confirmations()
            server.register_tab(tab)
            status, picked = pick_via_dialog(http.server_port, photos, tab)
            self.assertEqual(status, 200)
            request = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-pending?path={quote(str(photos))}',
                              headers={'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': tab})
            with urlopen(request) as response:
                body = json.load(response)
            self.assertEqual(body['items'], [])
            self.assertEqual(len(body['recovered']), 1)
            self.assertTrue(body['recovered'][0]['can_keep'])
            keep = Request(f'http://127.0.0.1:{http.server_port}/api/chatgpt-recovered-keep',
                           json.dumps({'id': record['id']}).encode(),
                           {'X-Cropper-Token': server.TOKEN, 'X-Cropper-Tab': tab, 'Content-Type': 'application/json'})
            with urlopen(keep) as response:
                kept = json.load(response)
            self.assertEqual(kept['id'], record['id'])
            with urlopen(request) as response:
                body = json.load(response)
            self.assertEqual(len(body['items']), 1)
            self.assertEqual(body['recovered'], [])


if __name__ == '__main__':
    unittest.main()
