import json
import threading
import unittest
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app_settings
import server


class SessionOriginTests(unittest.TestCase):
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

    def test_session_requires_localhost_origin_or_host(self):
        with urlopen(self.base + "/api/session") as response:
            body = json.load(response)
        self.assertEqual(body["app"], "Cropper")
        self.assertTrue(body["token"])
        self.assertIn("has_key", body)

        request = Request(self.base + "/api/session", headers={"Origin": "http://evil.example"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        with caught.exception as response:
            payload = json.load(response)
        self.assertNotIn("token", payload)
        self.assertIn("localhost", payload["error"].lower())

        local = Request(self.base + "/api/session", headers={"Origin": self.base})
        with urlopen(local) as response:
            self.assertEqual(json.load(response)["app"], "Cropper")

    def test_data_dir_flag_creates_settings_outside_temp_by_choice(self):
        settings = app_settings.public_settings()
        self.assertEqual(settings["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(settings["quality"], "medium")
        self.assertFalse(settings["has_key"])
