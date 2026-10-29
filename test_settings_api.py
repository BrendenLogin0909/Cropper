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
        dumped = json.dumps(body)
        self.assertNotIn("sk-live-secret-9999", dumped)
        self.assertNotIn("api_key", body)
        self.assertEqual(body.get("keyring_error", ""), "")
        saved = (app_settings.data_dir() / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-live-secret-9999", saved)
        cleared = self.post("/api/settings/clear-key", {})
        self.assertFalse(cleared["has_key"])
        self.assertEqual(cleared["key_masked"], "")

    def test_keyring_failure_is_returned_to_the_ui(self):
        with patch.object(app_settings, "_keyring") as ring:
            ring.side_effect = RuntimeError("backend missing")
            request = Request(self.base + "/api/settings", headers=self.headers)
            with urlopen(request) as response:
                body = json.load(response)
        self.assertFalse(body["has_key"])
        self.assertEqual(body["key_masked"], "")
        self.assertIn("credential store", body["keyring_error"].lower())
        self.assertNotIn("backend missing", json.dumps(body))

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

    def test_settings_access_log_omits_request_body_and_key(self):
        secret = "sk-live-secret-log-4242"
        printed = []

        def capturing(*args, **kwargs):
            printed.append(" ".join(str(item) for item in args))

        with patch("builtins.print", capturing):
            self.post("/api/settings", {
                "api_key": secret,
                "model": "gpt-image-2.5-sunburst",
                "quality": "medium",
            })
            dummy = type("Dummy", (), {
                "path": "/api/settings",
                "command": "POST",
                "address_string": lambda self: "127.0.0.1",
            })()
            server.Handler.log_message(dummy, "%s", json.dumps({"api_key": secret, "photo_note": "secret note"}))
        blob = "\n".join(printed)
        self.assertNotIn(secret, blob)
        self.assertNotIn("api_key", blob)
        self.assertNotIn("photo_note", blob)
        self.assertNotIn("secret note", blob)
        self.assertIn("/api/settings", blob)
