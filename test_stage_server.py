import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image
import app_settings
import server
import chatgpt_pending
import photo_pairs
import stage_files as stages


class StageServerTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(TemporaryDirectory()))
        self.pending=self.root/'pending'
        app_settings.configure_runtime(self.root)
        self.addCleanup(lambda: setattr(app_settings, '_data_dir', None))
        stream=io.BytesIO();Image.new('RGB',(8,8),'blue').save(stream,'PNG');self.png=stream.getvalue()
        self.stack.enter_context(patch.object(photo_pairs,'ROOT',self.root/'links'))
        self.edit=self.stack.enter_context(patch.object(server,'edit_front',return_value=self.png))
        self.stack.enter_context(patch.object(server,'save_pending',side_effect=lambda source,png,**kwargs:chatgpt_pending.save_pending(source,png,self.pending,**kwargs)))
        self.stack.enter_context(patch.object(server,'get_pending',side_effect=lambda ident:chatgpt_pending.get_pending(ident,self.pending)))
        self.stack.enter_context(patch.object(server,'list_pending',side_effect=lambda folder:chatgpt_pending.list_pending(folder,self.pending)))
        self.stack.enter_context(patch.object(server,'list_recovered',side_effect=lambda:chatgpt_pending.list_recovered(self.pending)))
        self.stack.enter_context(patch.object(server,'remove_pending',side_effect=lambda ident:chatgpt_pending.remove_pending(ident,self.pending)))
        self.stack.enter_context(patch.object(server,'attach_link',side_effect=lambda ident,link:chatgpt_pending.attach_link(ident,link,self.pending)))
        self.stack.enter_context(patch.object(server,'keep_recovered',side_effect=lambda ident:chatgpt_pending.keep_recovered(ident,self.pending)))
        self.stack.enter_context(patch.object(server,'discard_recovered',side_effect=lambda ident:chatgpt_pending.discard_recovered(ident,self.pending)))
        self.http=server.LocalServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.stop_server)
        self.base=f'http://127.0.0.1:{self.http.server_port}'

    def stop_server(self):
        self.http.shutdown();self.http.server_close()

    def post(self,endpoint,body):
        request=Request(self.base+endpoint,json.dumps(body).encode(),{'X-Cropper-Token':server.TOKEN,'Content-Type':'application/json'})
        with urlopen(request) as response:return json.load(response)

    def source(self,folder,name):
        source=self.root/folder/name;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(self.png);return source

    def test_new_routing_survives_pending_reload_and_is_not_changed_at_acceptance(self):
        source=self.source(stages.CROP_QUEUE,'photo_c.png')
        record=self.post('/api/chatgpt-generate',{'front_path':str(source),'stage_routing':True})
        loaded,_=chatgpt_pending.get_pending(record['id'],self.pending)
        self.assertTrue(loaded['stage_routing'])
        self.post('/api/chatgpt-accept',{'id':record['id'],'stage_routing':False})
        self.assertTrue((self.root/stages.AI_QUEUE/'photo_c_ai.png').exists())
        self.assertTrue((self.root/stages.SKIP_ENHANCE/source.name).exists())
        self.assertFalse(source.exists());self.assertEqual(self.edit.call_count,1)

    def test_invalid_input_stage_is_rejected_before_api_call(self):
        source=self.source('other','photo.png')
        for flag in [True,False,None]:
            body={'front_path':str(source)}
            if flag is not None:body['stage_routing']=flag
            with self.assertRaises(HTTPError) as caught:
                self.post('/api/chatgpt-generate',body)
            self.assertEqual(caught.exception.code,400);caught.exception.close();self.edit.assert_not_called();self.assertTrue(source.exists())

    def test_false_and_missing_flags_cannot_restore_legacy_filing(self):
        for index, flag in enumerate([False, None]):
            source=self.source(stages.CROP_QUEUE,f'old-client-{index}_c.png')
            body={'front_path':str(source)}
            if flag is not None:body['stage_routing']=flag
            record=self.post('/api/chatgpt-generate',body)
            self.assertTrue(record['stage_routing'])
            self.post('/api/chatgpt-accept',{'id':record['id'],'stage_routing':False})
            self.assertTrue((self.root/stages.AI_QUEUE/f'{source.stem}_ai.png').exists())
            self.assertEqual((self.root/stages.CROPPED/source.name).read_bytes(),self.png)
            self.assertEqual((self.root/stages.SKIP_ENHANCE/source.name).read_bytes(),self.png)
            self.assertEqual(list(source.parent.iterdir()),[])

    def test_legacy_pending_record_accepts_through_flat_workflow(self):
        source=self.source(stages.CROP_QUEUE,'legacy_c.png')
        record=chatgpt_pending.save_pending(source,self.png,self.pending,stage_routing=False)
        self.post('/api/chatgpt-accept',{'id':record['id']})
        self.assertTrue((self.root/stages.AI_QUEUE/'legacy_c_ai.png').exists())
        self.assertEqual((self.root/stages.SKIP_ENHANCE/source.name).read_bytes(),self.png)
        self.edit.assert_not_called()

    def test_resubmit_comment_preserves_original_archive_names(self):
        source=self.source(stages.CROP_QUEUE,'2026-10-13 10.17.33_c.jpg')
        original=source.read_bytes()
        first=self.post('/api/chatgpt-generate',{'front_path':str(source),'stage_routing':True})
        comment='The man has a white moustache, not a shiny upper lip. Preserve his expression and everything else.'
        revised=self.post('/api/chatgpt-resubmit',{'id':first['id'],'comment':comment,'include_previous':False})
        self.post('/api/chatgpt-accept',{'id':revised['id']})
        for folder in [stages.CROPPED,stages.SKIP_ENHANCE]:
            self.assertEqual([p.name for p in (self.root/folder).iterdir()],[source.name])
            self.assertEqual((self.root/folder/source.name).read_bytes(),original)
        self.assertTrue((self.root/stages.AI_QUEUE/'2026-10-13 10.17.33_c_ai.png').exists())
        self.assertFalse(source.exists())
        self.assertEqual(self.edit.call_count,2)

    def test_finish_preview_then_files_both_pair_members_without_api(self):
        front=self.source(stages.ENHANCE_QUEUE,'photo_c_e.png')
        combined=self.source(stages.ENHANCE_QUEUE,'photo_front-back_c_e.png')
        photo_pairs.approve_link(front,combined,50,'right')
        preview=self.post('/api/stage-finish',{'paths':[str(front)],'dry_run':True})
        self.assertEqual(len(preview['items']),2);self.assertTrue(front.exists());self.assertTrue(combined.exists())
        result=self.post('/api/stage-finish',{'paths':[str(front)],'dry_run':False})
        self.assertEqual(len(result['finished']),2);self.assertFalse(result['errors'])
        for source in [front,combined]:
            self.assertFalse(source.exists())
            self.assertEqual((self.root/stages.ENHANCED/source.name).read_bytes(),self.png)
            self.assertEqual((self.root/stages.SKIP_AI/source.name).read_bytes(),self.png)
        self.edit.assert_not_called()

    def test_finish_refuses_pending_result_and_does_not_move_source(self):
        source=self.source(stages.AI_QUEUE,'photo_c_e_ai.png')
        chatgpt_pending.save_pending(source,self.png,self.pending)
        with self.assertRaises(HTTPError) as caught:self.post('/api/stage-finish',{'paths':[str(source)],'dry_run':False})
        self.assertEqual(caught.exception.code,400);caught.exception.close();self.assertTrue(source.exists());self.edit.assert_not_called()

    def test_stylesheet_and_capability_are_served(self):
        with urlopen(self.base+'/stage-routing.css') as response:self.assertIn(b'flat-stage-routing',response.read())
        with urlopen(self.base+'/api/session') as response:self.assertEqual(json.load(response)['stage_routing_version'],2)

    def test_corner_prediction_is_read_only_and_never_calls_paid_api(self):
        source=self.source(stages.ORIGINAL,'photo.png')
        before=source.read_bytes()
        result=self.post('/api/crop-corners',{'path':str(source)})
        self.assertIn('candidates',result)
        self.assertEqual(source.read_bytes(),before)
        self.edit.assert_not_called()
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/crop-corners',{'path':str(source),'target':['invalid',0]})
        self.assertEqual(caught.exception.code,400);caught.exception.close()


if __name__=='__main__':unittest.main()
