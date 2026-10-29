import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from chatgpt_pending import (
    cleanup_expired, default_pending_root, discard_recovered, get_pending,
    keep_recovered, list_pending, list_recovered, remove_pending, save_pending,
)


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(stream, 'PNG')
    return stream.getvalue()


class PendingTests(unittest.TestCase):
    def test_multiple_results_survive_independent_reads(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source_folder = root / "photos"
            source_folder.mkdir()
            pending_root = root / "pending"
            first = save_pending(source_folder / "a.jpg", png('red'), pending_root)
            second = save_pending(source_folder / "b.jpg", png('blue'), pending_root)
            self.assertEqual([item["id"] for item in list_pending(source_folder, pending_root)], [first["id"], second["id"]])
            self.assertEqual(get_pending(first["id"], pending_root)[1].read_bytes(), png('red'))
            self.assertEqual(get_pending(second["id"], pending_root)[1].read_bytes(), png('blue'))
            remove_pending(first["id"], pending_root)
            self.assertEqual(len(list_pending(source_folder, pending_root)), 1)

    def test_invalid_id_cannot_escape_pending_root(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                get_pending("../outside", Path(folder))

    def test_colorize_operation_is_written_once(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "photos"; source.mkdir()
            pending = root / "pending"
            writes = []
            original = save_pending.__globals__['_atomic_write_json']

            def counting(path, record):
                writes.append(dict(record))
                return original(path, record)

            with patch('chatgpt_pending._atomic_write_json', counting):
                record = save_pending(source / "mono.png", png('gray'), pending, operation='colorize')
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]['operation'], 'colorize')
            self.assertEqual(record['operation'], 'colorize')
            loaded, _ = get_pending(record['id'], pending)
            self.assertEqual(loaded['operation'], 'colorize')

    def test_truncated_json_is_recovered_not_dropped(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "photos"; photos.mkdir()
            pending = root / "pending"
            source = photos / "front.png"
            record = save_pending(source, png('green'), pending, operation='restore',
                                  revision={'comments': ['Keep the white moustache.']})
            metadata = pending / f"{record['id']}.json"
            metadata.write_text(metadata.read_text(encoding='utf-8') + ',', encoding='utf-8')
            self.assertEqual(list_pending(photos, pending), [])
            recovered = list_recovered(pending)
            self.assertEqual(len(recovered), 1)
            self.assertTrue(recovered[0]['image_readable'])
            self.assertTrue(recovered[0]['can_keep'])
            self.assertEqual(recovered[0]['reason'], 'truncated metadata')
            self.assertIn('Keep the white moustache.', recovered[0]['notes'])

    def test_damaged_image_is_discard_only_and_notes_follow_discard(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "photos"; photos.mkdir()
            pending = root / "pending"
            record = save_pending(photos / "front.png", png('green'), pending,
                                  revision={'comments': ['Keep the white moustache.']})
            image = pending / f"{record['id']}.png"
            image.write_bytes(b'not-an-image')
            recovered = list_recovered(pending)
            self.assertEqual(len(recovered), 1)
            self.assertFalse(recovered[0]['image_readable'])
            self.assertFalse(recovered[0]['can_keep'])
            self.assertEqual(recovered[0]['reason'], 'damaged image')
            self.assertIn('Keep the white moustache.', recovered[0]['notes'])
            with self.assertRaisesRegex(ValueError, 'damaged'):
                keep_recovered(record['id'], pending)
            discard_recovered(record['id'], pending)
            self.assertFalse((pending / f"{record['id']}.png").exists())
            self.assertFalse((pending / f"{record['id']}.json").exists())

    def test_keep_readable_image_preserves_guidance_notes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "photos"; photos.mkdir()
            pending = root / "pending"
            record = save_pending(photos / "front.png", png('green'), pending, operation='restore',
                                  revision={'comments': ['Keep the white moustache.']})
            metadata = pending / f"{record['id']}.json"
            metadata.write_text(metadata.read_text(encoding='utf-8') + ',', encoding='utf-8')
            kept = keep_recovered(record['id'], pending)
            self.assertEqual(kept['comments'], ['Keep the white moustache.'])
            self.assertEqual(list_recovered(pending), [])
            pending_items = list_pending(photos, pending)
            self.assertEqual(len(pending_items), 1)
            self.assertEqual(pending_items[0]['comments'], ['Keep the white moustache.'])

    def test_recovery_logs_omit_guidance_text(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "photos"; photos.mkdir()
            pending = root / "pending"
            record = save_pending(photos / "front.png", png('green'), pending,
                                  revision={'comments': ['Keep the white moustache.']})
            (pending / f"{record['id']}.json").write_text('{', encoding='utf-8')
            with self.assertLogs('cropper.pending', level='WARNING') as captured:
                list_recovered(pending)
            text = '\n'.join(captured.output)
            self.assertIn(record['id'], text)
            self.assertNotIn('moustache', text.lower())
            self.assertNotIn('guidance', text.lower())
            self.assertNotIn('Keep the white', text)

    def test_expiry_leaves_recovered_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "photos"; photos.mkdir()
            pending = root / "pending"
            fresh = save_pending(photos / "fresh.png", png('red'), pending)
            stale = save_pending(photos / "stale.png", png('blue'), pending)
            damaged = save_pending(photos / "damaged.png", png('green'), pending)
            stale_meta = pending / f"{stale['id']}.json"
            data = json.loads(stale_meta.read_text(encoding='utf-8'))
            data['created'] = 1
            stale_meta.write_text(json.dumps(data), encoding='utf-8')
            (pending / f"{damaged['id']}.json").write_text('{', encoding='utf-8')
            removed = cleanup_expired(pending, now=time.time(), ttl=60)
            self.assertIn(stale['id'], removed)
            self.assertNotIn(fresh['id'], removed)
            self.assertNotIn(damaged['id'], removed)
            self.assertTrue((pending / f"{damaged['id']}.png").is_file())
            self.assertEqual(len(list_pending(photos, pending)), 1)

    def test_pending_root_is_not_system_temp(self):
        with TemporaryDirectory() as folder:
            import app_settings
            previous = app_settings._data_dir
            try:
                app_settings.configure_runtime(folder)
                root = default_pending_root()
                self.assertEqual(root, Path(folder).resolve() / 'pending')
                self.assertNotEqual(root.resolve(), (Path(tempfile.gettempdir()) / 'pending').resolve())
                self.assertTrue(str(root).startswith(str(Path(folder).resolve())))
            finally:
                app_settings._data_dir = previous


if __name__ == '__main__':
    unittest.main()
