import io
import json
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from crop_detection import detection_preview, predict_corners

GOLDEN_PATH = Path(__file__).resolve().parent / 'test_crop_golden.json'


def synthetic_print(width, height, seed=0):
    image = Image.new('RGB', (width, height), '#c8b79a')
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 11):
        shade = 180 + (y * 3 + seed) % 25
        draw.line((0, y, width, y + 6), fill=(shade, shade - 20, shade - 40), width=2)
    for x in range(0, width, 29):
        draw.line((x, 0, x + 4, height), fill='#b7a488', width=1)
    cx, cy = width * 0.5, height * 0.5
    pw, ph = width * 0.62, height * 0.58
    tilt = 18
    outer = [
        (cx - pw / 2 + tilt, cy - ph / 2),
        (cx + pw / 2 + tilt, cy - ph / 2 + 8),
        (cx + pw / 2 - tilt, cy + ph / 2),
        (cx - pw / 2 - tilt, cy + ph / 2 - 8),
    ]
    inset = min(width, height) * 0.035
    mx = sum(p[0] for p in outer) / 4
    my = sum(p[1] for p in outer) / 4
    inner = []
    for x, y in outer:
        dx, dy = mx - x, my - y
        n = (dx * dx + dy * dy) ** 0.5 or 1
        inner.append((x + dx / n * inset, y + dy / n * inset))
    draw.polygon(outer, fill='white')
    draw.polygon(inner, fill='#2a4a86')
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=92)
    buf.seek(0)
    return Image.open(buf).convert('RGB')


class CropDetectionTests(unittest.TestCase):
    def test_textured_background_and_perspective_print(self):
        image=Image.new('RGB',(500,400),'#cbb99a')
        draw=ImageDraw.Draw(image)
        for y in range(0,400,13):
            draw.line((0,y,500,y+4),fill='#bda985',width=2)
        expected=[(90,80),(420,60),(430,310),(70,330)]
        draw.polygon(expected,fill='#254080')
        result=predict_corners(image)
        self.assertTrue(result['candidates'])
        points=result['candidates'][0]['points']
        for x,y in expected:
            self.assertLess(min(((a-x)**2+(b-y)**2)**.5 for a,b in points),12)

    def test_target_point_selects_requested_print(self):
        image=Image.new('RGB',(600,400),'#cbb99a');draw=ImageDraw.Draw(image)
        draw.rectangle((35,80,265,320),fill='#254080')
        draw.rectangle((335,65,565,330),fill='#804025')
        for target,left in [([130,180],True),([450,180],False)]:
            result=predict_corners(image,target)
            self.assertTrue(result['candidates'])
            for candidate in result['candidates']:
                xs=[p[0] for p in candidate['points']]
                self.assertTrue(max(xs)<300 if left else min(xs)>300)

    def test_blank_image_does_not_invent_corners(self):
        self.assertEqual(predict_corners(Image.new('RGB',(500,400),'white'))['candidates'],[])

    def test_returns_original_resolution_coordinates(self):
        image=Image.new('RGB',(2000,1600),'#cbb99a');ImageDraw.Draw(image).rectangle((400,300,1700,1300),fill='#254080')
        result=predict_corners(image)
        self.assertTrue(result['candidates'])
        self.assertGreater(max(p[0] for p in result['candidates'][0]['points']),1600)

    def test_detection_preview_does_not_mutate_rgb_source(self):
        image = Image.new('RGB', (400, 300), '#224466')
        before = image.tobytes()
        preview = detection_preview(image)
        self.assertEqual(image.tobytes(), before)
        self.assertLessEqual(max(preview.size), 960)

    def test_golden_synthetic_prints_match_recorded_corners(self):
        goldens = json.loads(GOLDEN_PATH.read_text())
        self.assertGreaterEqual(len(goldens), 4)
        for case in goldens:
            width, height = case['size']
            image = synthetic_print(width, height)
            result = predict_corners(image, case['target'])
            got, expected = result['candidates'], case['candidates']
            self.assertEqual(len(got), len(expected), msg=f'{width}x{height} count')
            for actual, want in zip(got, expected):
                self.assertLessEqual(abs(actual['score'] - want['score']), 0.001)
                for point, origin in zip(actual['points'], want['points']):
                    self.assertEqual(point[0] - origin[0], 0.0)
                    self.assertEqual(point[1] - origin[1], 0.0)


if __name__=='__main__':unittest.main()
