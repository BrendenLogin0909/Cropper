import io
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
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
        with self.assertRaisesRegex(ValueError, 'Filename template supports'):
            stages.validate_template('{name}{unknown}{ext}')

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
                self.assertEqual(output, existing)
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
                self.assertEqual(output, source)
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
            original_open = Path.open

            def unlink_then_fail(self, missing_ok=False):
                original_unlink(self, missing_ok=missing_ok)
                if self.resolve() == source_key:
                    unlinked['done'] = True
                    raise OSError('fail after unlink')

            def fail_restore(self, *args, **kwargs):
                mode = args[0] if args else kwargs.get('mode', 'r')
                if unlinked['done'] and self.resolve() == source_key and mode == 'xb':
                    raise OSError('cannot restore source')
                return original_open(self, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink_then_fail), patch.object(Path, 'open', fail_restore):
                with self.assertRaises(OSError):
                    stages.file_results([source], [png('blue')], 'restore')
            recovered = source.with_name(source.name + '.cropper-recovered')
            self.assertTrue((root / stages.CROPPED / source.name).is_file())
            self.assertTrue((root / stages.AI_QUEUE / 'photo_c_ai.png').is_file())
            self.assertTrue(recovered.is_file() or not source.exists())

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
        self.assertIn('Choose another output folder', Path(server.__file__).read_text(encoding='utf-8'))
        self.assertIn('choose-output-folder', Path(__file__).with_name('index.html').read_text(encoding='utf-8'))
        self.assertIn('offerChooseOutputFolder', Path(__file__).with_name('app.js').read_text(encoding='utf-8'))


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
