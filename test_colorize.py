import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from PIL import Image
from colorize import classify, candidates, colour_only
from chatgpt_restore import edit_front, COLORIZATION_PROMPT
import photo_pairs
import stage_files as stages

def png(image):
    stream=io.BytesIO();image.save(stream,'PNG');return stream.getvalue()

class ColorizationTests(unittest.TestCase):
    def test_detect_monochrome_sepia_and_colour(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'1.5. TBP Cropped';root.mkdir()
            grey=np.tile(np.linspace(40,210,100).astype(np.uint8),(100,1))
            Image.fromarray(grey).convert('RGB').save(root/'bw.png')
            sepia=np.stack([grey, (grey*.8).astype(np.uint8),(grey*.55).astype(np.uint8)],axis=2)
            Image.fromarray(sepia).save(root/'sepia.png')
            colour=Image.new('RGB',(100,100),'blue');colour.paste('green',(0,0,50,100));colour.save(root/'colour.png')
            Image.fromarray(grey).convert('RGB').save(root/'front-back.png')
            self.assertEqual(classify(root/'bw.png'),'black-and-white')
            self.assertIn('sepia',classify(root/'sepia.png'))
            self.assertEqual(classify(root/'colour.png'),'already colour / uncertain')
            selected={Path(item['path']).name for item in candidates(root,[]) if item['suggested']}
            self.assertEqual(selected,{'bw.png','sepia.png'})

    def test_luminance_detail_source_size_and_prompt(self):
        with TemporaryDirectory() as directory:
            source=Path(directory)/'front.png'
            grey=np.tile(np.linspace(50,200,64).astype(np.uint8),(40,1))
            grey[8:18,20:30]=90
            original=Image.fromarray(grey).convert('RGB');original.save(source)
            ai=Image.new('RGB',(128,80),(170,135,110))
            client=Mock();client.images.edit.return_value.data=[Mock(b64_json=base64.b64encode(png(ai)).decode())]
            result=edit_front(source,client,operation='colorize')
            self.assertEqual(client.images.edit.call_count,1)
            self.assertEqual(client.images.edit.call_args.kwargs['prompt'],COLORIZATION_PROMPT)
            with Image.open(io.BytesIO(result)) as output:
                self.assertEqual(output.size,original.size)
                before=cv2.cvtColor(np.asarray(original).astype(np.float32)/255,cv2.COLOR_RGB2LAB)[:,:,0]
                after=cv2.cvtColor(np.asarray(output).astype(np.float32)/255,cv2.COLOR_RGB2LAB)[:,:,0]
                self.assertLess(np.abs(before-after).max(),.5)
            self.assertEqual(source.read_bytes(),png(original))

    def test_reject_changed_aspect_ratio(self):
        with TemporaryDirectory() as directory:
            source=Path(directory)/'a.png';Image.new('RGB',(40,60)).save(source)
            with self.assertRaises(ValueError):colour_only(source,png(Image.new('RGB',(100,30))))

    def test_colorized_pair_filing_preserves_back_and_carries_link(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'1.5. TBP Cropped';root.mkdir();front=root/'front.png';composite=root/'combined.png'
            Image.new('RGB',(20,30),'gray').save(front)
            combined=Image.new('RGB',(40,30),'ivory');combined.save(composite)
            record={'front_path':str(front),'operation':'colorize','link':{'composite_path':str(composite),'split':50,'layout':'right'}}
            with patch.object(photo_pairs,'ROOT',root/'store'):
                outputs=photo_pairs.accept_linked(record,png(Image.new('RGB',(20,30),'red')),allowed_roots=[root])
                self.assertTrue(stages.same_path(photo_pairs.read_links(outputs[0].parent)[0]['composite_path'], outputs[1]))
            self.assertTrue(all(path.parent.name=='4.1.1. AI Colourised' for path in outputs))
            self.assertTrue((root.parent/'2. Cropped'/'front.png').exists())
            self.assertTrue((root.parent/'2. Cropped'/'combined.png').exists())
            with Image.open(outputs[1]) as output:self.assertTrue(np.array_equal(np.asarray(output)[:,20:],np.asarray(combined)[:,20:]))

if __name__=='__main__':unittest.main()
