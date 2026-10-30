import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import photo_pairs
import chatgpt_pending
import stage_files as stages

class PairTests(unittest.TestCase):
    def test_suggest_link_restore_once_and_preserve_back(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'1.5. TBP Cropped';root.mkdir()
            front=root/'front.png'; composite=root/'combined.png'
            rng=np.random.default_rng(5)
            original=Image.fromarray(rng.integers(0,256,(100,70,3),dtype=np.uint8))
            original.save(front)
            combined=Image.new('RGB',(140,100),'ivory');combined.paste(original,(0,0));combined.save(composite)
            candidates=photo_pairs.suggestions(root)
            self.assertTrue(any(stages.same_path(item['front_path'], front) and stages.same_path(item['composite_path'], composite) for item in candidates))
            with patch.object(photo_pairs,'ROOT',root/'store'):
                link=photo_pairs.approve_link(front,composite,50,'right',allowed_roots=[root])
                self.assertEqual(photo_pairs.read_links(root)[0],link)
            png=io.BytesIO();Image.new('RGB',(70,100),'red').save(png,'PNG')
            staged=chatgpt_pending.save_pending(front,png.getvalue(),root/'pending')
            staged=chatgpt_pending.attach_link(staged['id'],link,root/'pending')
            with patch.object(photo_pairs,'ROOT',root/'store'):
                outputs=photo_pairs.accept_linked(staged,png.getvalue())
                self.assertTrue(stages.same_path(photo_pairs.read_links(outputs[0].parent)[0]['front_path'], outputs[0]))
            self.assertEqual(len(outputs),2)
            with Image.open(outputs[1]) as result:
                self.assertEqual(result.getpixel((10,10)),(255,0,0))
                self.assertTrue(np.array_equal(np.asarray(result.crop((70,0,140,100))),np.asarray(combined.crop((70,0,140,100)))))
            self.assertTrue((root.parent/'2. Cropped'/'front.png').exists())
            self.assertTrue((root.parent/'2. Cropped'/'combined.png').exists())

    def test_collision_preserves_both_originals(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'1.5. TBP Cropped';root.mkdir();front=root/'front.png';composite=root/'combined.png'
            Image.new('RGB',(20,20),'red').save(front)
            Image.new('RGB',(40,20),'white').save(composite)
            out=root.parent/'1.7. TBP AI-Improved';out.mkdir();(out/'combined_ai.png').write_bytes(b'existing')
            record={'front_path':str(front),'link':{'composite_path':str(composite),'split':50,'layout':'right'}}
            with self.assertRaises(ValueError):photo_pairs.accept_linked(record,front.read_bytes())
            self.assertTrue(front.exists() and composite.exists())

if __name__=='__main__':unittest.main()
