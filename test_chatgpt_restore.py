import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from PIL import Image
from chatgpt_restore import edit_front, file_accepted, reassemble


def png(color, size=(10, 10)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def jpeg(color, size=(10, 10)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "JPEG")
    return stream.getvalue()


class ChatgptRestoreTests(unittest.TestCase):
    def test_corrections_with_optional_previous_reference(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'front.png'; source.write_bytes(png('red'))
            previous = Path(folder) / 'previous.png'; previous.write_bytes(png('green'))
            client = Mock()
            client.images.edit.return_value.data = [Mock(b64_json=base64.b64encode(png('blue')).decode())]
            edit_front(source, client, comments=['The man has a white moustache.'])
            args = client.images.edit.call_args.kwargs
            self.assertEqual(args['image'].getvalue(), png('red'))
            self.assertIn('white moustache', args['prompt'])
            edit_front(source, client, operation='colorize', comments=['White moustache.'], previous=previous)
            args = client.images.edit.call_args.kwargs
            self.assertEqual(len(args['image']), 2)
            self.assertEqual(args['image'][1].getvalue(), png('green'))
            self.assertIn('ONLY colourise', args['prompt'])
            self.assertIn('authoritative photographic evidence', args['prompt'])

    def test_single_api_call(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "front.png"
            source.write_bytes(png("red"))
            client = Mock()
            client.images.edit.return_value.data = [Mock(b64_json=base64.b64encode(png("blue")).decode())]
            self.assertEqual(edit_front(source, client), png("blue"))
            self.assertEqual(client.images.edit.call_count, 1)
            self.assertEqual(source.read_bytes(), png("red"))

    def test_back_preserved_and_accepted_pair_filed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)/"1.5. TBP Cropped"; root.mkdir()
            front = root / "front.jpg"
            combined = root / "front-back.png"
            front_bytes = jpeg("red")
            front.write_bytes(front_bytes)
            image = Image.new("RGB", (20, 10), "green")
            image.paste(Image.new("RGB", (10, 10), "red"), (0, 0))
            image.save(combined)
            result = reassemble(png("blue"), combined, 50, "right")
            with Image.open(io.BytesIO(result)) as check:
                self.assertEqual(check.getpixel((15, 2)), (0, 128, 0))
                self.assertEqual(check.getpixel((2, 2)), (0, 0, 255))
            outputs = file_accepted(front, png("blue"), combined, result)
            self.assertEqual(len(outputs), 2)
            self.assertTrue(all(path.is_file() for path in outputs))
            self.assertEqual((root.parent / "2. Cropped" / "front.jpg").read_bytes(), front_bytes)

    def test_collision_leaves_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)/"1.5. TBP Cropped"; root.mkdir()
            source = root / "front.jpg"
            source.write_bytes(b"source")
            existing = root.parent / "1.7. TBP AI-Improved" / "front_ai.png"
            existing.parent.mkdir()
            existing.write_bytes(b"existing")
            with self.assertRaises(ValueError):
                file_accepted(source, png("blue"))
            self.assertEqual(source.read_bytes(), b"source")
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_same_stem_pair_files_both_colorized_results(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)/"1.5. TBP Cropped"; root.mkdir()
            front = root / "photo.jpg"
            combined = root / "photo.png"
            front_bytes = jpeg("red")
            combined_bytes = png("yellow")
            front.write_bytes(front_bytes)
            combined.write_bytes(combined_bytes)

            outputs = file_accepted(front, png("blue"), combined, png("green"), operation="colorize")

            self.assertEqual([path.name for path in outputs], ["photo_col.png", "photo_front-back_col.png"])
            self.assertEqual(outputs[0].read_bytes(), png("blue"))
            self.assertEqual(outputs[1].read_bytes(), png("green"))
            self.assertEqual((root.parent / "2. Cropped" / "photo.jpg").read_bytes(), front_bytes)
            self.assertEqual((root.parent / "2. Cropped" / "photo.png").read_bytes(), combined_bytes)
