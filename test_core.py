import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from server import choose_output, merge_front_and_back, order_corners, save_image, save_merged_image, warp


class CropperCoreTests(unittest.TestCase):
    def test_four_clicks_in_any_order_produce_same_crop(self):
        image = Image.new("RGB", (500, 380), "#242c40")
        draw = ImageDraw.Draw(image)
        draw.polygon([(80, 70), (420, 45), (450, 300), (55, 330)], fill="#e7e1ca")
        corners = [[80, 70], [420, 45], [450, 300], [55, 330]]
        first = warp(image, corners)
        second = warp(image, [corners[2], corners[0], corners[3], corners[1]])
        self.assertEqual(first.size, second.size)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertGreater(first.width, first.height)
        self.assertEqual(warp(image, corners, 90).size, first.size[::-1])

    def test_rejects_non_quadrilateral(self):
        with self.assertRaisesRegex(ValueError, "four-sided"):
            order_corners([[0, 0], [100, 0], [100, 100], [50, 50]])

    def test_requested_proportions_keep_content_and_correct_shape(self):
        image = Image.new("RGB", (400, 500), "#786e61")
        corners = [[40, 25], [330, 30], [335, 425], [45, 430]]
        measured = warp(image, corners)
        corrected = warp(image, corners, aspect_ratio=2 / 3)
        self.assertAlmostEqual(corrected.width / corrected.height, 2 / 3, places=3)
        self.assertGreaterEqual(corrected.width, measured.width)
        self.assertGreaterEqual(corrected.height, measured.height)
        with self.assertRaisesRegex(ValueError, "valid width-to-height"):
            warp(image, corners, aspect_ratio=0)

    def test_copy_names_do_not_overwrite_and_replace_updates_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "photo.jpg"
            Image.new("RGB", (200, 150), "#aa3322").save(source)
            points = [[10, 10], [180, 10], [180, 130], [10, 130]]
            settings = {"mode": "subfolder", "subfolder": "Cropped", "suffix": "_fixed"}
            first, size = save_image(source, points, settings, 0)
            second, _ = save_image(source, points, settings, 0)
            self.assertEqual(size, (170, 120))
            self.assertEqual(first.name, "photo_fixed.jpg")
            self.assertEqual(second.name, "photo_fixed_2.jpg")
            self.assertTrue(source.exists())
            original_size = source.stat().st_size
            replaced, _ = save_image(source, points, {"mode": "replace"}, 0)
            self.assertEqual(replaced, source)
            self.assertNotEqual(source.stat().st_size, original_size)
            with Image.open(source) as replaced_image:
                self.assertEqual(replaced_image.size, size)

    def test_beside_requires_distinct_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "photo.png"
            source.touch()
            with self.assertRaisesRegex(ValueError, "prefix or suffix"):
                choose_output(source, {"mode": "beside"})

    def test_front_and_back_layout_and_back_filename(self):
        portrait = Image.new("RGB", (120, 200), "#24445b")
        landscape = Image.new("RGB", (240, 120), "#24445b")
        back = Image.new("RGB", (160, 100), "#d2c0a3")
        beside_portrait = merge_front_and_back(portrait, back)
        below_landscape = merge_front_and_back(landscape, back)
        self.assertEqual(beside_portrait.height, portrait.height)
        self.assertGreater(beside_portrait.width, portrait.width)
        self.assertEqual(below_landscape.width, landscape.width)
        self.assertGreater(below_landscape.height, landscape.height)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            front_path, back_path = folder / "front.jpg", folder / "back.jpg"
            portrait.save(front_path); back.save(back_path)
            destination, _ = save_merged_image(front_path, back_path, {"mode": "subfolder", "subfolder": "Front and Back", "suffix": "_paired"}, "back", 0)
            self.assertEqual(destination.name, "back_paired.jpg")
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
