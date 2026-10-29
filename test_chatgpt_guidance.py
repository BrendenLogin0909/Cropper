import io
import json
import logging
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

import app_settings
import chatgpt_pending as pending
import server
from chatgpt_restore import (
    COLORIZATION_PROMPT, GUIDANCE_LIMIT, OVERRIDE_SENTENCE, PROMPT, build_edit_prompt,
    edit_front, grapheme_clusters, grapheme_count, sanitize_guidance, strip_guidance_controls,
)


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(stream, 'PNG')
    return stream.getvalue()


FAMILY = '👨‍👩‍👧'
FLAG = '🇬🇧'
ACCENT = 'e\u0301'


class GuidanceSanitizeTests(unittest.TestCase):
    def test_grapheme_clusters_and_control_stripping(self):
        self.assertEqual(grapheme_count('hello'), 5)
        self.assertEqual(grapheme_count(ACCENT), 1)
        self.assertEqual(grapheme_count('é'), 1)
        self.assertEqual(grapheme_count(FLAG), 1)
        self.assertEqual(grapheme_count(FAMILY), 1)
        self.assertEqual(grapheme_count(ACCENT * GUIDANCE_LIMIT), GUIDANCE_LIMIT)
        self.assertEqual(len(grapheme_clusters(ACCENT * (GUIDANCE_LIMIT + 1))), GUIDANCE_LIMIT + 1)
        self.assertEqual(strip_guidance_controls('a\x00b\x07c'), 'abc')
        self.assertEqual(strip_guidance_controls('line1\nline2'), 'line1\nline2')
        self.assertEqual(strip_guidance_controls('a\tb\rc'), 'abc')

    def test_sanitize_trims_caps_and_treats_whitespace_as_empty(self):
        self.assertEqual(sanitize_guidance('  sailor hats  '), 'sailor hats')
        self.assertEqual(sanitize_guidance(' \n\t '), '')
        self.assertEqual(sanitize_guidance('\x00\x01'), '')
        self.assertEqual(sanitize_guidance(None), '')
        self.assertEqual(sanitize_guidance(12), '')
        self.assertEqual(sanitize_guidance('a' * (GUIDANCE_LIMIT + 25)), 'a' * GUIDANCE_LIMIT)
        self.assertEqual(sanitize_guidance(ACCENT * (GUIDANCE_LIMIT + 1)), ACCENT * GUIDANCE_LIMIT)
        self.assertEqual(sanitize_guidance(FAMILY * (GUIDANCE_LIMIT + 1)), FAMILY * GUIDANCE_LIMIT)
        self.assertEqual(sanitize_guidance('keep\nnewlines\x00please'), 'keep\nnewlinesplease')


class GuidancePromptTests(unittest.TestCase):
    def test_empty_guidance_matches_today_prompt(self):
        self.assertEqual(build_edit_prompt('restore'), PROMPT)
        self.assertEqual(build_edit_prompt('colorize'), COLORIZATION_PROMPT)
        self.assertEqual(build_edit_prompt('restore', comments=None, batch_context='   ', photo_note='\n'), PROMPT)
        self.assertEqual(build_edit_prompt('colorize', batch_context='', photo_note=None), COLORIZATION_PROMPT)

    def test_prompt_snapshot_order_and_override_sentence(self):
        batch = '1950s wedding party, navy uniforms'
        note = "bride's bouquet is pink roses"
        correction = 'Keep the veil.'
        self.assertEqual(OVERRIDE_SENTENCE, 'If this note conflicts with the batch context, follow this note.')
        expected = (
            PROMPT
            + f'\nBatch context:\n{batch}'
            + f'\nPer-photo note:\n{note}\n{OVERRIDE_SENTENCE}'
            + '\nUser-supplied corrections, in chronological order (later corrections override earlier ones). '
              'Apply these within the preservation rules above; change only what is necessary:\n'
              '1. Keep the veil.'
        )
        prompt = build_edit_prompt('restore', comments=[correction], batch_context=batch, photo_note=note)
        self.assertEqual(prompt, expected)
        self.assertLess(prompt.find(PROMPT[:40]), prompt.find(batch))
        self.assertLess(prompt.find(batch), prompt.find(note))
        self.assertLess(prompt.find(note), prompt.find(OVERRIDE_SENTENCE))
        self.assertLess(prompt.find(OVERRIDE_SENTENCE), prompt.find(correction))
        color = build_edit_prompt('colorize', comments=[correction], batch_context=batch, photo_note=note)
        self.assertTrue(color.startswith(COLORIZATION_PROMPT))
        self.assertEqual(color[len(COLORIZATION_PROMPT):], expected[len(PROMPT):])

    def test_per_photo_note_conflict_sentence_and_rerun_last(self):
        prompt = build_edit_prompt('restore', comments=['Keep the eyes unchanged.'],
                                   batch_context='navy uniforms', photo_note='white hat')
        self.assertIn(OVERRIDE_SENTENCE, prompt)
        self.assertGreater(prompt.find('Keep the eyes unchanged.'), prompt.find(OVERRIDE_SENTENCE))
        self.assertGreater(prompt.find('white hat'), prompt.find('navy uniforms'))

    def test_edit_front_empty_guidance_is_one_call_with_today_prompt(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'front.png'
            source.write_bytes(png())
            client = Mock()
            client.images.edit.return_value.data = [Mock(b64_json=__import__('base64').b64encode(png('blue')).decode())]
            edit_front(source, client)
            self.assertEqual(client.images.edit.call_count, 1)
            self.assertEqual(client.images.edit.call_args.kwargs['prompt'], PROMPT)
            edit_front(source, client, operation='colorize', batch_context='  ', photo_note='\x00')
            self.assertEqual(client.images.edit.call_count, 2)
            self.assertEqual(client.images.edit.call_args.kwargs['prompt'], COLORIZATION_PROMPT)

    def test_edit_front_sends_guidance_on_same_comments_path(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'front.png'
            source.write_bytes(png())
            client = Mock()
            client.images.edit.return_value.data = [Mock(b64_json=__import__('base64').b64encode(png('blue')).decode())]
            edit_front(source, client, comments=['Keep the veil.'], batch_context='navy', photo_note='white hat')
            self.assertEqual(client.images.edit.call_count, 1)
            prompt = client.images.edit.call_args.kwargs['prompt']
            self.assertEqual(prompt, build_edit_prompt('restore', comments=['Keep the veil.'],
                                                       batch_context='navy', photo_note='white hat'))


class GuidancePendingTests(unittest.TestCase):
    def test_notes_persist_in_pending_json_and_are_deleted_with_entry(self):
        with TemporaryDirectory() as folder:
            photos = Path(folder) / 'photos'; photos.mkdir()
            store = Path(folder) / 'pending'
            source = photos / 'front.png'
            record = pending.save_pending(
                source, png(), store, operation='restore',
                revision={'batch_context': 'navy uniforms', 'photo_note': 'white hat',
                          'comments': ['Keep the veil.']})
            metadata = store / f"{record['id']}.json"
            loaded = json.loads(metadata.read_text(encoding='utf-8'))
            self.assertEqual(loaded['batch_context'], 'navy uniforms')
            self.assertEqual(loaded['photo_note'], 'white hat')
            self.assertEqual(loaded['comments'], ['Keep the veil.'])
            listed = pending.list_pending(photos, store)
            self.assertEqual(listed[0]['batch_context'], 'navy uniforms')
            pending.remove_pending(record['id'], store)
            self.assertFalse(metadata.exists())
            self.assertFalse((store / f"{record['id']}.png").exists())
            self.assertEqual(pending.list_pending(photos, store), [])

    def test_notes_are_never_written_to_logs(self):
        with TemporaryDirectory() as folder:
            photos = Path(folder) / 'photos'; photos.mkdir()
            store = Path(folder) / 'pending'
            secret = 'private maiden name on the bouquet'
            record = pending.save_pending(
                photos / 'front.png', png(), store,
                revision={'batch_context': secret, 'photo_note': secret, 'comments': [secret]})
            metadata = store / f"{record['id']}.json"
            metadata.write_text(metadata.read_text(encoding='utf-8') + ',', encoding='utf-8')
            with self.assertLogs('cropper.pending', level='WARNING') as captured:
                recovered = pending.list_recovered(store)
            text = '\n'.join(captured.output)
            self.assertIn(record['id'], text)
            self.assertNotIn(secret, text)
            self.assertNotIn('bouquet', text)
            self.assertIn(secret, recovered[0]['notes'])

    def test_keep_recovered_preserves_batch_and_photo_notes(self):
        with TemporaryDirectory() as folder:
            photos = Path(folder) / 'photos'; photos.mkdir()
            store = Path(folder) / 'pending'
            record = pending.save_pending(
                photos / 'front.png', png(), store, operation='restore',
                revision={'batch_context': 'navy uniforms', 'photo_note': 'white hat'})
            metadata = store / f"{record['id']}.json"
            metadata.write_text(metadata.read_text(encoding='utf-8') + ',', encoding='utf-8')
            kept = pending.keep_recovered(record['id'], store)
            self.assertEqual(kept['batch_context'], 'navy uniforms')
            self.assertEqual(kept['photo_note'], 'white hat')


class GuidanceServerTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        app_settings.configure_runtime(self.folder.name)
        self.root = Path(self.folder.name) / '1.5. TBP Cropped'
        self.root.mkdir()
        self.store = Path(self.folder.name) / 'pending'
        self.source = self.root / 'front.png'
        Image.new('RGB', (8, 8), 'gray').save(self.source)
        self.http = server.LocalServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.headers = {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'}
        self.patches = [
            patch.object(server, 'get_pending', side_effect=lambda ident: pending.get_pending(ident, self.store)),
            patch.object(server, 'save_pending', side_effect=lambda source, png, **kw: pending.save_pending(source, png, self.store, **kw)),
            patch.object(server, 'list_pending', side_effect=lambda folder: pending.list_pending(folder, self.store)),
            patch.object(server, 'list_recovered', side_effect=lambda: pending.list_recovered(self.store)),
            patch.object(server, 'remove_pending', side_effect=lambda ident: pending.remove_pending(ident, self.store)),
            patch.object(server, 'attach_link', side_effect=lambda ident, link: pending.attach_link(ident, link, self.store)),
        ]
        for item in self.patches:
            self.addCleanup(item.stop)
            item.start()

    def post(self, route, data):
        request = Request(self.base + route, json.dumps(data).encode(), self.headers)
        with urlopen(request) as response:
            return json.load(response)

    def test_generate_dry_run_shows_guidance_without_paying(self):
        with patch.object(server, 'edit_front') as edit:
            empty = self.post('/api/chatgpt-generate', {'front_path': str(self.source), 'dry_run': True})
            self.assertEqual(empty['has_guidance'], False)
            filled = self.post('/api/chatgpt-generate', {
                'front_path': str(self.source), 'dry_run': True,
                'batch_context': 'navy uniforms', 'photo_note': 'white hat',
            })
            self.assertEqual(filled['has_guidance'], True)
            self.assertEqual(filled['front_path'], str(self.source))
            edit.assert_not_called()
            self.assertEqual(pending.list_pending(self.root, self.store), [])

    def test_generate_stores_notes_and_uses_one_edit_call(self):
        with patch.object(server, 'edit_front', return_value=png('blue')) as edit:
            staged = self.post('/api/chatgpt-generate', {
                'front_path': str(self.source),
                'batch_context': 'navy uniforms',
                'photo_note': 'white hat',
            })
            self.assertEqual(edit.call_count, 1)
            self.assertEqual(edit.call_args.kwargs['batch_context'], 'navy uniforms')
            self.assertEqual(edit.call_args.kwargs['photo_note'], 'white hat')
            self.assertTrue(staged['has_guidance'])
            self.assertEqual(staged['batch_context'], 'navy uniforms')
            self.assertEqual(staged['photo_note'], 'white hat')
            on_disk = json.loads((self.store / f"{staged['id']}.json").read_text(encoding='utf-8'))
            self.assertEqual(on_disk['photo_note'], 'white hat')

    def test_resubmit_keeps_notes_outside_correction_limits_and_appends_last(self):
        first = pending.save_pending(
            self.source, png(), self.store, stage_routing=True,
            revision={'operation': 'restore', 'batch_context': 'navy uniforms',
                      'photo_note': 'white hat', 'comments': ['first'] * 19})
        with patch.object(server, 'edit_front', return_value=png('blue')) as edit:
            second = self.post('/api/chatgpt-resubmit', {
                'id': first['id'], 'comment': 'Keep the eyes unchanged.',
            })
            self.assertEqual(edit.call_count, 1)
            self.assertEqual(edit.call_args.kwargs['comments'][-1], 'Keep the eyes unchanged.')
            self.assertEqual(edit.call_args.kwargs['batch_context'], 'navy uniforms')
            self.assertEqual(edit.call_args.kwargs['photo_note'], 'white hat')
            self.assertEqual(second['batch_context'], 'navy uniforms')
            self.assertEqual(second['photo_note'], 'white hat')
            self.assertEqual(len(second['comments']), 20)
            with self.assertRaises(HTTPError):
                self.post('/api/chatgpt-resubmit', {'id': second['id'], 'comment': 'too many'})

    def test_accept_deletes_notes_with_pending_entry(self):
        first = pending.save_pending(
            self.source, png(), self.store, stage_routing=True,
            revision={'operation': 'restore', 'batch_context': 'secret-batch', 'photo_note': 'secret-note'})
        metadata = self.store / f"{first['id']}.json"
        self.assertIn('secret-note', metadata.read_text(encoding='utf-8'))
        with patch.object(server, 'edit_front', return_value=png('blue')):
            self.post('/api/chatgpt-reject', {'id': first['id']})
        self.assertFalse(metadata.exists())
        self.assertEqual(pending.list_pending(self.root, self.store), [])

    def test_colorize_uses_same_guidance_fields(self):
        with patch.object(server, 'edit_front', return_value=png('blue')) as edit:
            staged = self.post('/api/chatgpt-generate', {
                'front_path': str(self.source), 'operation': 'colorize',
                'batch_context': '1950s wedding party, navy uniforms',
                'photo_note': "bride's bouquet is pink roses",
            })
            self.assertEqual(edit.call_args.kwargs['operation'], 'colorize')
            self.assertEqual(edit.call_args.kwargs['batch_context'], '1950s wedding party, navy uniforms')
            self.assertEqual(staged['operation'], 'colorize')

    def test_server_logs_omit_guidance_text(self):
        secret = 'private address on the reverse'
        logger = logging.getLogger('cropper.pending')
        with patch.object(server, 'edit_front', return_value=png('blue')), \
             self.assertLogs(logger, level='DEBUG') as captured:
            logger.debug('pending save started')
            staged = self.post('/api/chatgpt-generate', {
                'front_path': str(self.source), 'photo_note': secret,
            })
            pending.list_recovered(self.store)
        text = '\n'.join(captured.output)
        self.assertNotIn(secret, text)
        self.assertIn(secret, json.loads((self.store / f"{staged['id']}.json").read_text(encoding='utf-8'))['photo_note'])


if __name__ == '__main__':
    unittest.main()
