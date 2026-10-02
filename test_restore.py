import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from restore import preview_dust_marks, repair_dust, reduce_noise, sharpen_detail, useful_change
from server import save_restored_image


class RestorationTests(unittest.TestCase):
    def test_dust_repair_removes_small_mark_and_keeps_real_edge(self):
        pixels = np.full((220, 220, 3), 120, dtype=np.uint8)
        pixels[:, 140:] = 65
        pixels[80:83, 60:63] = 245
        improved, changes = repair_dust(Image.fromarray(pixels))
        result = np.asarray(improved)
        self.assertIn("Isolated dust and short scratches repaired", changes)
        self.assertLess(int(result[81, 61, 0]), 150)
        self.assertLessEqual(abs(int(result[110, 139, 0]) - 120), 2)
        self.assertLessEqual(abs(int(result[110, 140, 0]) - 65), 2)

    def test_marked_long_scratch_can_be_repaired_and_previewed(self):
        pixels = np.full((220, 220, 3), 100, dtype=np.uint8)
        pixels[110, 45:176] = 255
        stroke = [[[45 / 219, 110 / 219], [175 / 219, 110 / 219]]]
        image = Image.fromarray(pixels)
        marked, _ = preview_dust_marks(image, stroke)
        repaired, changes = repair_dust(image, stroke)
        self.assertGreater(int(np.asarray(marked)[110, 100, 0]), int(np.asarray(marked)[110, 100, 1]) + 100)
        self.assertLess(int(np.asarray(repaired)[110, 100, 0]), 125)
        self.assertIn("Detected dust and marked scratches repaired", changes)
        with self.assertRaises(ValueError):
            repair_dust(image, [[[1.4, 0.5]]])

    def test_faded_monochrome_print_gets_pale_flecks_repaired(self):
        pixels = np.full((220, 220, 3), 120, dtype=np.uint8)
        pixels[80:82, 60:62] = 151
        pixels[120:122, 160:162] = 151
        repaired, changes = repair_dust(Image.fromarray(pixels))
        self.assertTrue(useful_change(changes))
        self.assertLess(int(np.asarray(repaired)[80, 60, 0]), 140)

    def test_noise_reduction_skips_clean_image_and_smooths_flat_grain(self):
        clean = Image.new("RGB", (180, 180), (130, 130, 130))
        untouched, changes = reduce_noise(clean)
        self.assertEqual(changes, ["No significant fine noise detected"])
        self.assertEqual(untouched.tobytes(), clean.tobytes())

        rng = np.random.default_rng(42)
        noisy = np.clip(130 + rng.normal(0, 14, (180, 180, 3)), 0, 255).astype(np.uint8)
        improved, changes = reduce_noise(Image.fromarray(noisy))
        result = np.asarray(improved)
        self.assertIn("Fine grain and color speckles reduced", changes)
        self.assertLess(float(result[:, :, 0].std()), float(noisy[:, :, 0].std()) * 0.75)
        self.assertLess(abs(float(result[:, :, 0].mean()) - 130), 3)

    def test_sharpening_adds_edge_contrast_without_clipping(self):
        gray = np.full((180, 180), 65, dtype=np.uint8)
        gray[:, 90:] = 185
        softened = cv2.GaussianBlur(gray, (0, 0), 2)
        image = Image.fromarray(cv2.cvtColor(softened, cv2.COLOR_GRAY2RGB))
        improved, changes = sharpen_detail(image)
        result = np.asarray(improved)[:, :, 0]
        self.assertIn("Existing edges clarified", changes)
        self.assertGreater(int(result[90, 91]) - int(result[90, 88]), int(softened[90, 91]) - int(softened[90, 88]))
        self.assertLessEqual(int(result.max()), 194)
        self.assertLess(abs(int(result[90, 30]) - 65), 3)
        self.assertLess(abs(int(result[90, 150]) - 185), 3)
        self.assertLess(int(np.max(np.ptp(np.asarray(improved).astype(np.int16), axis=2))), 3)

    def test_faded_gray_photo_recovers_tonal_range(self):
        gray = np.full((300, 300), 155, dtype=np.uint8)
        gray[70:240, 80:220] = 115
        gray[95:225, 130:190] = 205
        image = Image.fromarray(cv2.cvtColor(cv2.GaussianBlur(gray, (0, 0), 2), cv2.COLOR_GRAY2RGB))
        improved, changes = sharpen_detail(image)
        result = np.asarray(improved)[:, :, 0]
        self.assertIn("Faded tonal range recovered", changes)
        self.assertGreater(int(result[150, 150]) - int(result[150, 95]), 110)
        self.assertLess(int(result[150, 95]), 105)

    def test_no_effect_does_not_create_copy_or_move_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "clean.png"
            Image.new("RGB", (90, 70), (140, 140, 140)).save(source)
            for operation in ("dust", "noise", "sharpen"):
                with self.subTest(operation=operation):
                    destination, _, changes, archived = save_restored_image(source, operation)
                    self.assertIsNone(destination)
                    self.assertIsNone(archived)
                    self.assertFalse(useful_change(changes))
                    self.assertTrue(source.exists())
                    self.assertEqual(list(Path(temporary).iterdir()), [source])

    def test_clear_photo_with_sparse_strong_edges_is_not_sharpened(self):
        pixels = np.full((480, 640, 3), (30, 40, 55), dtype=np.uint8)
        pixels[100:380, 180:460] = (230, 230, 225)
        improved, changes = sharpen_detail(Image.fromarray(pixels))
        self.assertFalse(useful_change(changes))
        self.assertTrue(np.array_equal(np.asarray(improved), pixels))

    def test_tiny_image_is_skipped_safely(self):
        image = Image.new("RGB", (8, 8), (150, 150, 150))
        improved, changes = sharpen_detail(image)
        self.assertFalse(useful_change(changes))
        self.assertEqual(improved.tobytes(), image.tobytes())

    def test_each_step_saves_in_new_folder_and_archives_source(self):
        names = {"dust": ("Dust Repaired", "Before Dust Repair"), "noise": ("Noise Reduced", "Before Noise Reduction"), "sharpen": ("Sharpened", "Before Sharpening")}
        for operation, (output_folder, archive_folder) in names.items():
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "family.png"
                pixels = np.full((70, 90, 3), 120, dtype=np.uint8)
                if operation == "dust":
                    pixels[30:33, 40:43] = 245
                elif operation == "noise":
                    pixels = np.clip(pixels + np.random.default_rng(4).normal(0, 18, pixels.shape), 0, 255).astype(np.uint8)
                else:
                    pixels[:, 45:] = 190
                    pixels = cv2.GaussianBlur(pixels, (0, 0), 2)
                Image.fromarray(pixels).save(source)
                original = source.read_bytes()
                destination, size, _, archived = save_restored_image(source, operation)
                self.assertEqual(size, (90, 70))
                self.assertEqual(destination, Path(temporary) / output_folder / "family.png")
                self.assertEqual(archived, Path(temporary) / archive_folder / "family.png")
                self.assertTrue(destination.is_file())
                self.assertEqual(archived.read_bytes(), original)
                self.assertFalse(source.exists())

    def test_failed_archive_rolls_back_new_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "family.png"
            pixels = np.full((70, 90, 3), 120, dtype=np.uint8)
            pixels[30:33, 40:43] = 245
            Image.fromarray(pixels).save(source)
            real_replace = os.replace

            def fail_archive(old, new):
                if Path(old) == source:
                    raise PermissionError("Archive is not writable")
                return real_replace(old, new)

            with patch("server.os.replace", side_effect=fail_archive):
                with self.assertRaises(PermissionError):
                    save_restored_image(source, "dust")
            self.assertTrue(source.exists())
            self.assertFalse((Path(temporary) / "Dust Repaired" / "family.png").exists())


if __name__ == "__main__":
    unittest.main()
