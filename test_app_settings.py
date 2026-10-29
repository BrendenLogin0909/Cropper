import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_settings


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        app_settings.set_data_dir(self.folder.name)
        app_settings.install_memory_keyring()
        self._env = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._env
        app_settings._data_dir = None

    def test_key_is_masked_and_not_written_to_settings_json(self):
        app_settings.set_stored_api_key("sk-test-secret-key-1234")
        public = app_settings.public_settings()
        self.assertEqual(public["key_masked"], "****1234")
        self.assertTrue(public["has_key"])
        self.assertEqual(public["key_source"], "stored")
        text = Path(self.folder.name, "settings.json").read_text(encoding="utf-8") if Path(self.folder.name, "settings.json").exists() else "{}"
        self.assertNotIn("sk-test-secret-key-1234", text)
        self.assertNotIn("sk-test", app_settings.mask_key("sk-test-secret-key-1234"))

    def test_env_var_wins_over_stored_key(self):
        app_settings.set_stored_api_key("sk-stored-xxxx")
        os.environ["OPENAI_API_KEY"] = "sk-env-ABCD"
        self.assertEqual(app_settings.resolved_api_key(), "sk-env-ABCD")
        self.assertEqual(app_settings.public_settings()["key_source"], "env")
        self.assertEqual(app_settings.public_settings()["key_masked"], "****ABCD")

    def test_clear_key_removes_store_not_env(self):
        app_settings.set_stored_api_key("sk-stored-9876")
        app_settings.clear_stored_api_key()
        self.assertEqual(app_settings.stored_api_key(), "")
        os.environ["OPENAI_API_KEY"] = "sk-env-keep"
        app_settings.clear_stored_api_key()
        self.assertEqual(app_settings.resolved_api_key(), "sk-env-keep")

    def test_default_model_and_quality(self):
        data = app_settings.load_settings()
        self.assertEqual(data["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(data["quality"], "medium")
        self.assertEqual(app_settings.image_quality(preview=True), "low")
        app_settings.save_settings({"quality": "high", "model": "gpt-image-2.5-sunburst"})
        self.assertEqual(app_settings.load_settings()["quality"], "high")

    def test_data_dir_flag(self):
        other = TemporaryDirectory()
        self.addCleanup(other.cleanup)
        root = app_settings.configure_runtime(other.name)
        self.assertEqual(root, Path(other.name).resolve())
        app_settings.save_settings({"quality": "low"})
        self.assertTrue((root / "settings.json").is_file())
        self.assertNotIn("sk-", (root / "settings.json").read_text(encoding="utf-8"))


class ApiKeySourceTests(unittest.TestCase):
    def test_restore_module_does_not_read_plaintext_key_files(self):
        import chatgpt_restore
        text = Path(chatgpt_restore.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".env.local", text)
        self.assertNotRegex(text, r"C:[\\/]Users")
        self.assertNotRegex(text, r"(?i)bre" + r"nd")
        with patch.object(app_settings, "resolved_api_key", return_value=""):
            with self.assertRaisesRegex(ValueError, "Settings"):
                chatgpt_restore.api_key()
