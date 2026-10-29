import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PIL import Image
import stage_files as stages
import photo_pairs
from server import save_image, save_enhanced_image, save_merged_image
from chatgpt_restore import file_accepted


def png(colour='red'):
    stream = io.BytesIO()
    Image.new('RGB', (16, 16), colour).save(stream, 'PNG')
    return stream.getvalue()


class StageFilesTests(unittest.TestCase):
    def source(self, root, folder, name='photo_c.png'):
        source = root / folder / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(png())
        return source

    def test_full_pipeline_is_flat_and_preserves_each_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'photo.png'; source.write_bytes(png())
            cropped, _ = save_image(source, [[0,0],[15,0],[15,15],[0,15]], {'stage_routing':True}, 0)
            self.assertEqual(cropped,root/stages.CROP_QUEUE/'photo_c.png')
            self.assertFalse(source.exists())
            self.assertEqual((root/stages.ORIGINAL/'photo.png').read_bytes(),png())
            crop_bytes=cropped.read_bytes()
            enhanced,_,_,archive=save_enhanced_image(cropped,{'stage_routing':True})
            self.assertEqual(enhanced,root/stages.ENHANCE_QUEUE/'photo_c_e.png')
            self.assertEqual(archive.read_bytes(),crop_bytes)
            enhanced_bytes=enhanced.read_bytes()
            ai=file_accepted(enhanced,png('blue'),stage_routing=True)[0]
            self.assertEqual(ai,root/stages.AI_QUEUE/'photo_c_e_ai.png')
            self.assertEqual((root/stages.ENHANCED/enhanced.name).read_bytes(),enhanced_bytes)
            colour=file_accepted(ai,png('green'),operation='colorize',stage_routing=True)[0]
            self.assertEqual(colour,root/stages.COLOR/'photo_c_e_ai_col.png')
            self.assertEqual((root/stages.AI/ai.name).read_bytes(),png('blue'))
            for folder in stages.STAGES:
                self.assertFalse(list((root/folder).iterdir()))

    def test_bypass_auto_and_ai_keeps_unchanged_snapshots(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            output=file_accepted(source,png('blue'),operation='colorize',stage_routing=True)[0]
            self.assertEqual(output.name,'photo_c_col.png')
            for folder in [stages.CROPPED,stages.SKIP_ENHANCE,stages.SKIP_AI]:
                self.assertEqual((root/folder/source.name).read_bytes(),png())
            self.assertFalse(source.exists())

    def test_crop_to_ai_and_finish_unchanged(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            ai=file_accepted(source,png('blue'),stage_routing=True)[0]
            self.assertEqual((root/stages.SKIP_ENHANCE/source.name).read_bytes(),png())
            stages.commit([stages.plan(ai,'finish')],[None])
            self.assertFalse(ai.exists())
            self.assertEqual((root/stages.AI/ai.name).read_bytes(),png('blue'))

    def test_finish_cropped_creates_all_skipped_snapshots_without_suffix_changes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            stages.commit([stages.plan(source,'finish')],[None])
            for folder in [stages.CROPPED,stages.SKIP_ENHANCE,stages.SKIP_AI]:
                self.assertEqual((root/folder/source.name).read_bytes(),png())
            self.assertFalse(source.exists())

    def test_collision_keeps_source_and_existing_result(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            output=root/stages.AI_QUEUE/'photo_c_ai.png';output.parent.mkdir();output.write_bytes(b'existing')
            with self.assertRaisesRegex(ValueError,'already exists'):
                file_accepted(source,png('blue'),stage_routing=True)
            self.assertEqual(source.read_bytes(),png());self.assertEqual(output.read_bytes(),b'existing')
            self.assertFalse((root/stages.CROPPED).exists())

    def test_identical_snapshot_reused_but_different_snapshot_refused(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            snapshot=self.source(root,stages.CROPPED)
            file_accepted(source,png('blue'),stage_routing=True)
            self.assertEqual(snapshot.read_bytes(),png())
            source=self.source(root,stages.CROP_QUEUE);source.write_bytes(png('yellow'))
            with self.assertRaisesRegex(ValueError,'different snapshot'):
                stages.commit([stages.plan(source,'finish')],[None])
            self.assertTrue(source.exists())

    def test_pair_collision_rolls_back_both_before_moving(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);front=self.source(root,stages.ENHANCE_QUEUE,'front_c_e.png')
            back=self.source(root,stages.ENHANCE_QUEUE,'front-back_c_e.png')
            existing=root/stages.COLOR/'front-back_c_e_col.png';existing.parent.mkdir();existing.write_bytes(b'existing')
            with self.assertRaises(ValueError):
                file_accepted(front,png('blue'),back,png('green'),operation='colorize',stage_routing=True)
            self.assertTrue(front.exists());self.assertTrue(back.exists());self.assertEqual(existing.read_bytes(),b'existing')

    def test_write_failure_removes_partial_outputs_and_keeps_sources(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE)
            original_open=Path.open
            def fail_output(path,*args,**kwargs):
                if path.parent.name==stages.AI_QUEUE and args and args[0]=='xb':
                    raise OSError('simulated disk error')
                return original_open(path,*args,**kwargs)
            with patch.object(Path,'open',fail_output):
                with self.assertRaises(OSError): file_accepted(source,png('blue'),stage_routing=True)
            self.assertTrue(source.exists())
            self.assertFalse((root/stages.CROPPED/source.name).exists())

    def test_original_archive_is_never_removed_when_recropping(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.ORIGINAL,'photo.png')
            save_image(source,[[0,0],[15,0],[15,15],[0,15]],{'stage_routing':True},0)
            self.assertEqual(source.read_bytes(),png())

    def test_merge_keeps_standalone_front_and_front_back_name(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);front=self.source(root,stages.CROP_QUEUE,'front_c.png')
            back=self.source(root,stages.CROP_QUEUE,'back_c.png')
            output,_=save_merged_image(front,back,{'stage_routing':True},'front',0)
            self.assertEqual(output.name,'front_front-back_c.png')
            self.assertEqual(output.parent,front.parent)
            self.assertTrue(front.exists());self.assertTrue(back.exists())

    def test_invalid_stage_refused(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,'unrecognised')
            with self.assertRaisesRegex(ValueError,'TBP stage'): stages.plan(source,'restore')
            source=self.source(root,stages.AI_QUEUE)
            with self.assertRaisesRegex(ValueError,'already completed'): stages.plan(source,'restore')

    def test_comment_cannot_replace_archive_filename(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,stages.CROP_QUEUE,'2026-10-13 10.17.33_c.jpg')
            planned=stages.plan(source,'restore')
            planned['snapshots'][1]=root/stages.SKIP_ENHANCE/'The man has a white moustache.jpg'
            with self.assertRaisesRegex(ValueError,'Archive filename must match'):
                stages.commit([planned],[png('blue')])
            self.assertEqual(source.read_bytes(),png())
            self.assertFalse((root/stages.CROPPED).exists())
            self.assertFalse((root/stages.SKIP_ENHANCE).exists())
            self.assertFalse(planned['output'].exists())

    def test_linked_auto_improve_preserves_back_and_carries_link(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);front=self.source(root,stages.CROP_QUEUE,'front_c.png')
            composite=self.source(root,stages.CROP_QUEUE,'front-back_c.png')
            image=Image.new('RGB',(32,16),'green');image.paste(Image.new('RGB',(16,16),'red'),(0,0));image.save(composite)
            with patch.object(photo_pairs,'ROOT',root/'links'):
                photo_pairs.approve_link(front,composite,50,'right')
                save_enhanced_image(composite,{'stage_routing':True})
                links=photo_pairs.read_links(root/stages.ENHANCE_QUEUE)
                self.assertEqual(len(links),1)
            output=root/stages.ENHANCE_QUEUE/'front-back_c_e.png'
            with Image.open(output) as check:self.assertEqual(check.getpixel((25,4)),(0,128,0))
            self.assertFalse(front.exists());self.assertFalse(composite.exists())


if __name__=='__main__': unittest.main()
