import base64
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from chatgpt_restore import call_images_edit, edit_front, openai_client
from image_api_errors import QUOTA_MESSAGE, image_api_error


def png(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, "PNG")
    return stream.getvalue()


def api_error(status, code=None, kind=None):
    error = RuntimeError("Private upstream text must not leak")
    error.status_code = status
    error.code = code
    error.body = {"error": {"code": code, "type": kind, "message": "Private upstream text"}}
    error.response = SimpleNamespace(headers={"retry-after": "1", "x-request-id": "req_example"})
    return error


class OpenAIRetryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        import app_settings
        app_settings.set_data_dir(self.folder.name)

    def test_rate_limit_429_is_retried_once(self):
        client = Mock()
        client.images.edit.side_effect = [
            api_error(429, "rate_limit_exceeded", "rate_limit_error"),
            SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png("blue")).decode())]),
        ]
        with patch("chatgpt_restore.time.sleep") as slept:
            response = call_images_edit(client, model="gpt-image-2.5-sunburst")
        self.assertEqual(client.images.edit.call_count, 2)
        slept.assert_called_once()
        self.assertTrue(response.data)

    def test_insufficient_quota_is_not_retried_and_uses_exact_message(self):
        client = Mock()
        error = api_error(429, "insufficient_quota", "insufficient_quota")
        client.images.edit.side_effect = error
        with patch("chatgpt_restore.time.sleep") as slept:
            with self.assertRaises(RuntimeError):
                call_images_edit(client)
        self.assertEqual(client.images.edit.call_count, 1)
        slept.assert_not_called()
        status, body = image_api_error(error)
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], QUOTA_MESSAGE)

    def test_server_and_auth_errors_are_not_retried(self):
        for status, code, kind in ((500, "server_error", "server_error"), (401, "invalid_api_key", "invalid_request_error")):
            with self.subTest(status=status):
                client = Mock()
                client.images.edit.side_effect = api_error(status, code, kind)
                with patch("chatgpt_restore.time.sleep") as slept:
                    with self.assertRaises(RuntimeError):
                        call_images_edit(client)
                self.assertEqual(client.images.edit.call_count, 1)
                slept.assert_not_called()

    def test_edit_front_quota_does_not_write_and_client_disables_sdk_retries(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "front.png"
            source.write_bytes(png())
            client = Mock()
            client.images.edit.side_effect = api_error(429, "insufficient_quota", "insufficient_quota")
            with patch("chatgpt_restore.time.sleep"):
                with self.assertRaises(RuntimeError):
                    edit_front(source, client)
            self.assertEqual(client.images.edit.call_count, 1)
            self.assertEqual(source.read_bytes(), png())
            self.assertEqual(list(Path(folder).iterdir()), [source])

        with patch("openai.OpenAI") as ctor:
            ctor.return_value = Mock()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_BASE_URL", None)
                openai_client(api_key_value="sk-test", timeout=180)
            ctor.assert_called_with(
                api_key="sk-test",
                timeout=180,
                max_retries=0,
                base_url="https://api.openai.com/v1",
            )

    def test_openai_client_honors_openai_base_url(self):
        with patch("openai.OpenAI") as ctor:
            ctor.return_value = Mock()
            with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}):
                openai_client(api_key_value="sk-test", timeout=30)
            ctor.assert_called_with(
                api_key="sk-test",
                timeout=30,
                max_retries=0,
                base_url="http://127.0.0.1:9/v1",
            )

    def test_default_quality_is_medium_and_preview_is_low(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "front.png"
            source.write_bytes(png())
            client = Mock()
            client.images.edit.return_value = SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(png("blue")).decode())]
            )
            edit_front(source, client)
            self.assertEqual(client.images.edit.call_args.kwargs["quality"], "medium")
            self.assertEqual(client.images.edit.call_args.kwargs["model"], "gpt-image-2.5-sunburst")
            edit_front(source, client, preview=True)
            self.assertEqual(client.images.edit.call_args.kwargs["quality"], "low")
