from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from chatgpt_pending import get_pending, list_pending, remove_pending, save_pending


class PendingTests(unittest.TestCase):
    def test_multiple_results_survive_independent_reads(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source_folder = root / "photos"
            source_folder.mkdir()
            pending_root = root / "pending"
            first = save_pending(source_folder / "a.jpg", b"first", pending_root)
            second = save_pending(source_folder / "b.jpg", b"second", pending_root)
            self.assertEqual([item["id"] for item in list_pending(source_folder, pending_root)], [first["id"], second["id"]])
            self.assertEqual(get_pending(first["id"], pending_root)[1].read_bytes(), b"first")
            self.assertEqual(get_pending(second["id"], pending_root)[1].read_bytes(), b"second")
            remove_pending(first["id"], pending_root)
            self.assertEqual(len(list_pending(source_folder, pending_root)), 1)

    def test_invalid_id_cannot_escape_pending_root(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                get_pending("../outside", Path(folder))
