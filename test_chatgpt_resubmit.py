import io
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
import app_settings
import server
import chatgpt_pending as pending


class ResubmitTests(unittest.TestCase):
    def test_attempts_preserved_history_links_routing_and_accept_cleanup(self):
        for operation in ('restore', 'colorize'):
            with self.subTest(operation=operation), TemporaryDirectory() as directory, ExitStack() as stack:
                app_settings.configure_runtime(directory)
                root = Path(directory)/'1.5. TBP Cropped'; root.mkdir(); store = Path(directory) / 'pending'
                source = root / 'front.png'; composite = root / 'front-back.png'
                image = io.BytesIO(); Image.new('RGB', (10, 10), 'gray').save(image, 'PNG')
                source.write_bytes(image.getvalue()); Image.new('RGB', (20, 10), 'green').save(composite)
                link = {'front_path': str(source), 'composite_path': str(composite), 'split': 50, 'layout': 'right'}
                first = pending.save_pending(source, image.getvalue(), store, stage_routing=False,
                                             revision={'operation': operation, 'link': link})
                for name, function in {
                    'get_pending': lambda ident: pending.get_pending(ident, store),
                    'save_pending': lambda source, png, **kw: pending.save_pending(source, png, store, **kw),
                    'list_pending': lambda folder: pending.list_pending(folder, store),
                    'list_recovered': lambda: pending.list_recovered(store),
                    'remove_pending': lambda ident: pending.remove_pending(ident, store),
                }.items(): stack.enter_context(patch.object(server, name, side_effect=function))
                edit = stack.enter_context(patch.object(server, 'edit_front', return_value=image.getvalue()))
                http = server.LocalServer(('127.0.0.1', 0), server.Handler)
                threading.Thread(target=http.serve_forever, daemon=True).start()
                stack.callback(http.server_close); stack.callback(http.shutdown)

                def post(route, data):
                    request = Request(f'http://127.0.0.1:{http.server_port}{route}', json.dumps(data).encode(),
                                      {'X-Cropper-Token': server.TOKEN, 'Content-Type': 'application/json'})
                    with urlopen(request) as response: return json.load(response)

                with self.assertRaises(HTTPError): post('/api/chatgpt-resubmit', {'id': first['id'], 'comment': '  '})
                self.assertEqual(edit.call_count, 0)
                second = post('/api/chatgpt-resubmit', {'id': first['id'], 'comment': 'White moustache.'})
                self.assertIsNone(edit.call_args.kwargs['previous'])
                self.assertEqual(second['attempt'], 2)
                self.assertEqual(second['link'], link)
                self.assertIs(second['stage_routing'], True)
                third = post('/api/chatgpt-resubmit', {'id': second['id'], 'comment': 'Keep the eyes unchanged.', 'include_previous': True})
                self.assertEqual(third['comments'], ['White moustache.', 'Keep the eyes unchanged.'])
                self.assertEqual(edit.call_args.kwargs['operation'], operation)
                self.assertEqual(edit.call_args.kwargs['previous'].read_bytes(), image.getvalue())
                self.assertEqual(len(pending.list_pending(root, store)), 3)
                self.assertTrue(source.exists())
                edit.side_effect = RuntimeError('mock failure')
                with self.assertRaises(HTTPError): post('/api/chatgpt-resubmit', {'id': third['id'], 'comment': 'Correction'})
                self.assertEqual(len(pending.list_pending(root, store)), 3)
                post('/api/chatgpt-reject', {'id': third['id']})
                self.assertEqual(len(pending.list_pending(root, store)), 2)
                result = post('/api/chatgpt-accept', {'id': second['id']})
                self.assertCountEqual(result['removed_ids'], [first['id'], second['id']])
                self.assertEqual(pending.list_pending(root, store), [])
                self.assertFalse(source.exists())
                self.assertEqual(len(result['paths']), 2)
                with Image.open(result['paths'][1]) as rebuilt:
                    self.assertEqual(rebuilt.getpixel((15, 5)), (0, 128, 0))

    def test_competing_action_is_blocked_on_same_source(self):
        with server.review_lock(Path('front.png')):
            with self.assertRaises(ValueError):
                with server.review_lock(Path('front.png')): pass
            with server.review_lock(Path('other.png')): pass

