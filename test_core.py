import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from server import merge_front_and_back, order_corners, save_image, save_merged_image, warp


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

    def test_old_settings_cannot_bypass_flat_crop_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'photo.jpg'
            Image.new('RGB',(200,150),'red').save(source)
            original=source.read_bytes()
            result,_=save_image(source,[[10,10],[180,10],[180,130],[10,130]],{'mode':'replace','stage_routing':False},0)
            self.assertEqual(result,root/'1.5. TBP Cropped'/'photo_c.jpg')
            self.assertEqual((root/'1. Original'/'photo.jpg').read_bytes(),original)
            self.assertFalse(source.exists())
            with self.assertRaisesRegex(ValueError,'already exists'):
                save_image(root/'1. Original'/'photo.jpg',[[10,10],[180,10],[180,130],[10,130]],{},0)

    def test_nested_stage_input_rejected_without_creating_more_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'1.5. TBP Cropped'/'old-output'/'photo.png'
            source.parent.mkdir(parents=True);Image.new('RGB',(50,50),'red').save(source)
            with self.assertRaisesRegex(ValueError,'Nested stage'):
                save_image(source,[[0,0],[49,0],[49,49],[0,49]],{},0)
            self.assertEqual(list(source.parent.iterdir()),[source])

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
            folder = Path(temporary)/"1.5. TBP Cropped"; folder.mkdir()
            front_path, back_path = folder / "front.jpg", folder / "back.jpg"
            portrait.save(front_path); back.save(back_path)
            destination, _ = save_merged_image(front_path, back_path, {"mode": "subfolder", "subfolder": "Front and Back", "suffix": "_paired"}, "back", 0)
            self.assertEqual(destination.name, "back_front-back_c.jpg")
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
