import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from types import SimpleNamespace

from PIL import Image
import app_settings
import server
import chatgpt_pending
import photo_pairs
import stage_files as stages


class ServerPendingTests(unittest.TestCase):
    def test_generation_preserves_account_errors_and_rate_limit_headers(self):
        with TemporaryDirectory() as folder:
            queue=Path(folder)/'1.5. TBP Cropped';queue.mkdir()
            source = queue / 'front.png'
            Image.new('RGB', (8, 8), 'gray').save(source)
            app_settings.configure_runtime(folder)
            local_server = server.LocalServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=local_server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{local_server.server_port}'
            cases = [
                (429, 'rate_limit_exceeded', 'rate_limit_error', 429),
                (429, 'billing_not_active', 'insufficient_quota', 429),
                (401, 'invalid_api_key', 'invalid_request_error', 401),
                (403, 'permission_denied', 'invalid_request_error', 403),
                (400, 'content_policy_violation', 'invalid_request_error', 400),
                (404, 'model_not_found', 'invalid_request_error', 404),
                (413, 'image_too_large', 'invalid_request_error', 413),
                (500, 'server_error', 'server_error', 502),
            ]
            try:
                with patch.object(server, 'read_links', return_value=[]), \
                     patch.object(server, 'list_pending', return_value=[]), \
                     patch.object(server, 'list_recovered', return_value=[]), \
                     patch.object(server, 'save_pending') as save:
                    for status, code, kind, expected in cases:
                        with self.subTest(status=status, code=code):
                            error = RuntimeError('Private upstream error text must not be exposed')
                            error.status_code = status
                            error.code = code
                            error.body = {'type': kind}
                            error.response = SimpleNamespace(headers={'retry-after': '300', 'x-request-id': 'req_example'})
                            request = Request(base + '/api/chatgpt-generate',
                                json.dumps({'front_path': str(source)}).encode(),
                                {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'})
                            with patch.object(server, 'edit_front', side_effect=error), \
                                 self.assertRaises(HTTPError) as caught:
                                urlopen(request)
                            self.assertEqual(caught.exception.code, expected)
                            with caught.exception as response:
                                body = json.load(response)
                            self.assertEqual(body['code'], code)
                            self.assertEqual(body['type'], kind)
                            self.assertNotIn('Private upstream', body['error'])
                            self.assertEqual(body['request_id'], 'req_example')
                            if kind == 'insufficient_quota':
                                self.assertEqual(body['error'], 'your OpenAI account is out of credit')
                                self.assertNotIn('retry_after', body)
                            else:
                                self.assertIn('request req_example', body['error'])
                            if status == 429 and kind != 'insufficient_quota':
                                self.assertEqual(body['retry_after'], '300')
                            save.assert_not_called()
                            self.assertTrue(source.exists())
            finally:
                local_server.shutdown()
                local_server.server_close()

    def test_generate_two_then_review_and_file_independently(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)/"1.5. TBP Cropped";root.mkdir()
            pending = root / "pending"
            sources = [root / "a.png", root / "b.png"]
            image = io.BytesIO()
            Image.new("RGB", (8, 8), "red").save(image, "PNG")
            for source in sources:
                source.write_bytes(image.getvalue())
            composite = root / 'combined.png'
            Image.new('RGB', (16, 8), 'ivory').save(composite)
            app_settings.configure_runtime(folder)
            local_server = server.LocalServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=local_server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{local_server.server_port}"
            tab = 'tabid-test-000001'
            server.register_tab(tab)
            headers = {"X-Cropper-Token": server.TOKEN, "X-Cropper-Tab": tab}

            def post(endpoint, payload):
                request = Request(base + endpoint, json.dumps(payload).encode(), {**headers, "Content-Type": "application/json"})
                with urlopen(request) as response:
                    return json.load(response)

            def open_folder(path):
                request = Request(base + "/api/list?path=" + quote(str(path)), headers=headers)
                with urlopen(request) as response:
                    return json.load(response)

            try:
                with patch.object(photo_pairs, 'ROOT', root / 'links'), \
                     patch.object(server, 'attach_link', side_effect=lambda ident, link: chatgpt_pending.attach_link(ident, link, pending)), \
                     patch.object(server, 'set_operation', side_effect=lambda ident, operation: chatgpt_pending.set_operation(ident, operation, pending)), \
                     patch.object(server, "edit_front", return_value=image.getvalue()) as edit, \
                     patch.object(server, "save_pending", side_effect=lambda source, png, **kw: chatgpt_pending.save_pending(source, png, pending, **kw)), \
                     patch.object(server, "get_pending", side_effect=lambda ident: chatgpt_pending.get_pending(ident, pending)), \
                     patch.object(server, "list_pending", side_effect=lambda location: chatgpt_pending.list_pending(location, pending)), \
                     patch.object(server, "list_recovered", side_effect=lambda: chatgpt_pending.list_recovered(pending)), \
                     patch.object(server, "remove_pending", side_effect=lambda ident: chatgpt_pending.remove_pending(ident, pending)):
                    open_folder(root)
                    post('/api/photo-link', {'front_path':str(sources[0]), 'composite_path':str(composite), 'split':50, 'layout':'right'})
                    first = post("/api/chatgpt-generate", {"front_path": str(sources[0])})
                    self.assertTrue(stages.same_path(first['link']['composite_path'], composite))
                    second = post("/api/chatgpt-generate", {"front_path": str(sources[1])})
                    self.assertNotEqual(first["id"], second["id"])
                    self.assertTrue(all(source.is_file() for source in sources))
                    with urlopen(Request(base + "/api/chatgpt-pending?path=" + str(root).replace(" ", "%20"), headers=headers)) as response:
                        self.assertEqual(len(json.load(response)["items"]), 2)
                    post("/api/chatgpt-accept", {"id": first["id"]})
                    post("/api/chatgpt-reject", {"id": second["id"]})
                    self.assertTrue((root.parent / "1.7. TBP AI-Improved" / "a_ai.png").is_file())
                    self.assertTrue((root.parent / "2. Cropped" / "a.png").is_file())
                    self.assertTrue((root.parent / '2. Cropped' / 'combined.png').is_file())
                    self.assertTrue((root.parent / '1.7. TBP AI-Improved' / 'combined_ai.png').is_file())
                    self.assertEqual(edit.call_count, 2)  # one call for linked front; one for standalone b
                    self.assertTrue(sources[1].is_file())
                    self.assertFalse((root.parent / "1.7. TBP AI-Improved" / "b_ai.png").exists())
                    monochrome=root/'mono.png';Image.new('RGB',(8,8),'gray').save(monochrome)
                    detection=post('/api/colorization-candidates',{'path':str(root)})
                    self.assertTrue(any(stages.same_path(item['path'], monochrome) and item['suggested'] for item in detection['items']))
                    colored=post('/api/chatgpt-generate',{'front_path':str(monochrome),'operation':'colorize'})
                    self.assertEqual(colored['operation'],'colorize')
                    self.assertEqual(edit.call_args.kwargs,{'operation':'colorize'})
                    post('/api/chatgpt-accept',{'id':colored['id']})
                    self.assertTrue((root.parent/'4.1.1. AI Colourised'/'mono_col.png').exists())
                    self.assertTrue((root.parent/'2. Cropped'/'mono.png').exists())
            finally:
                local_server.shutdown()
                local_server.server_close()
