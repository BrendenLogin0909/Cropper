import unittest
from PIL import Image, ImageDraw
from crop_detection import predict_corners


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


if __name__=='__main__':unittest.main()
