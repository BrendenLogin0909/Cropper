import tempfile
import unittest
from pathlib import Path

import numpy as np
import cv2
from PIL import Image

from enhance import auto_enhance
from server import save_enhanced_image


class EnhanceTests(unittest.TestCase):
    def test_reduces_print_cast_without_darkening_middle(self):
        gray = np.tile(np.linspace(85, 190, 256, dtype=np.uint8), (256, 1))
        cast = np.stack([np.clip(gray.astype(int) + 35, 0, 255),
                         np.clip(gray.astype(int) + 12, 0, 255), gray], axis=2).astype(np.uint8)
        improved, changes = auto_enhance(Image.fromarray(cast))
        result = np.asarray(improved).astype(float)
        middle = result[:, 128].mean(axis=0)
        self.assertIn("Color cast reduced", changes)
        self.assertLess(middle.max() - middle.min(), 35)
        self.assertGreater(middle.mean(), 105)

    def test_monochrome_stays_monochrome(self):
        gray = np.tile(np.linspace(30, 210, 256, dtype=np.uint8), (256, 1))
        image = Image.fromarray(np.stack([gray] * 3, axis=2))
        improved, _ = auto_enhance(image)
        channels = np.asarray(improved)
        self.assertLess(int(np.max(np.abs(channels[:, :, 0].astype(int) - channels[:, :, 1].astype(int)))), 3)
        self.assertLess(int(np.max(np.abs(channels[:, :, 1].astype(int) - channels[:, :, 2].astype(int)))), 3)

    def test_bright_photo_keeps_its_exposure_and_highlights(self):
        bright = np.tile(np.linspace(175, 245, 256, dtype=np.uint8), (256, 1))
        image = Image.fromarray(np.stack([bright] * 3, axis=2))
        improved, changes = auto_enhance(image)
        original_l = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2LAB)[:, :, 0]
        improved_l = cv2.cvtColor(np.asarray(improved), cv2.COLOR_RGB2LAB)[:, :, 0]
        self.assertIn("Contrast refined; highlights protected", changes)
        self.assertLessEqual(abs(float(np.median(improved_l)) - float(np.median(original_l))), 2)
        self.assertLessEqual(float(np.percentile(improved_l, 95)) - float(np.percentile(original_l, 95)), 6)
        self.assertEqual(int((improved_l == 255).sum()), 0)

    def test_balanced_white_is_not_modified(self):
        image = Image.new("RGB", (100, 80), "white")
        improved, changes = auto_enhance(image)
        self.assertEqual(changes, ["No safe adjustment detected"])
        self.assertEqual(improved.tobytes(), image.tobytes())

    def test_saves_unique_copies_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "old.jpg"
            Image.new("RGB", (80, 60), (170, 150, 120)).save(source)
            original = source.read_bytes()
            settings = {"mode": "subfolder", "subfolder": "Enhanced", "suffix": "_enhanced"}
            first, size, _ = save_enhanced_image(source, settings)
            second, _, _ = save_enhanced_image(source, settings)
            self.assertEqual(size, (80, 60))
            self.assertEqual(first.name, "old_enhanced.jpg")
            self.assertEqual(second.name, "old_enhanced_2.jpg")
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
