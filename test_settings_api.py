import json
import threading
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen

import app_settings
import server


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.data = TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        app_settings.configure_runtime(self.data.name)
        app_settings.install_memory_keyring()
        self.http = server.LocalServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.headers = {"X-Cropper-Token": server.TOKEN, "Content-Type": "application/json"}

    def post(self, path, payload):
        request = Request(self.base + path, json.dumps(payload).encode(), self.headers)
        with urlopen(request) as response:
            return json.load(response)

    def test_save_key_is_masked_and_settings_file_has_no_secret(self):
        body = self.post("/api/settings", {
            "api_key": "sk-live-secret-9999",
            "model": "gpt-image-2.5-sunburst",
            "quality": "high",
        })
        self.assertEqual(body["key_masked"], "****9999")
        self.assertTrue(body["has_key"])
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("sk-live-secret-9999", json.dumps(body))
        saved = (app_settings.data_dir() / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-live-secret-9999", saved)
        cleared = self.post("/api/settings/clear-key", {})
        self.assertFalse(cleared["has_key"])

    def test_test_key_classifies_without_raw_provider_text(self):
        with patch("chatgpt_restore.openai_client") as ctor:
            ctor.return_value.models.list.return_value = []
            body = self.post("/api/settings/test-key", {"api_key": "sk-test-aaaa"})
            self.assertEqual(body["status"], "valid")
            self.assertEqual(body["message"], "This API key is valid.")
            self.assertNotIn("sk-test-aaaa", json.dumps(body))
        invalid = RuntimeError("Private key fragment sk-live-secret")
        invalid.status_code = 401
        invalid.code = "invalid_api_key"
        quota = RuntimeError("Private")
        quota.status_code = 429
        quota.code = "insufficient_quota"
        quota.body = {"error": {"type": "insufficient_quota"}}
        network = type("APIConnectionError", (Exception,), {})("Private network")
        for exc, status, message in (
            (invalid, "invalid", "This API key is invalid."),
            (quota, "no_quota", "your OpenAI account is out of credit"),
            (network, "network", "Network error: Cropper could not reach OpenAI."),
        ):
            with patch("chatgpt_restore.openai_client") as ctor:
                ctor.return_value.models.list.side_effect = exc
                body = self.post("/api/settings/test-key", {"api_key": "sk-test-bbbb"})
            self.assertEqual(body["status"], status)
            self.assertEqual(body["message"], message)
            self.assertNotIn("Private", json.dumps(body))
            self.assertNotIn("sk-test-bbbb", json.dumps(body))
