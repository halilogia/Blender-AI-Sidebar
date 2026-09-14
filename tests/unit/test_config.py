"""Unit tests for configuration management (M2.1)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.config import (
    Config,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    is_local_endpoint,
    is_network_allowed,
    load_config,
    mask_api_key,
    save_config,
)
from core.logging_utils import LOG_FILENAME, get_log_path


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        # Clean up relevant env vars
        self.old_env = {}
        for var in ["BLENDER_AI_API_KEY", "BLENDER_AI_BASE_URL", "BLENDER_AI_MODEL", "BLENDER_AI_TIMEOUT", "BLENDER_AI_LOG_DIR", "OPENAI_API_KEY", "OPENAI_BASE_URL"]:
            if var in os.environ:
                self.old_env[var] = os.environ.pop(var)

    def tearDown(self):
        for var in ["BLENDER_AI_API_KEY", "BLENDER_AI_BASE_URL", "BLENDER_AI_MODEL", "BLENDER_AI_TIMEOUT", "BLENDER_AI_LOG_DIR", "OPENAI_API_KEY", "OPENAI_BASE_URL"]:
            if var in os.environ:
                del os.environ[var]
        os.environ.update(self.old_env)
        self.temp_dir.cleanup()

    def test_default_config(self):
        cfg = Config()
        self.assertEqual(cfg.base_url, DEFAULT_BASE_URL)
        self.assertEqual(cfg.model, DEFAULT_MODEL)
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)

    def test_log_directory_override_is_opt_in(self):
        self.assertEqual(Path(get_log_path()).parent, Path(tempfile.gettempdir()))
        project_log_dir = Path(self.temp_dir.name) / "logs"
        os.environ["BLENDER_AI_LOG_DIR"] = str(project_log_dir)
        self.assertEqual(Path(get_log_path()), project_log_dir / LOG_FILENAME)
        self.assertTrue(project_log_dir.is_dir())

    def test_save_and_load_config(self):
        cfg = Config(
            base_url="https://api.openai.com/v1",
            api_key="sk-test1234567890",
            model="gpt-4o",
            timeout_seconds=45.0,
        )
        save_config(cfg, self.config_path)
        loaded_cfg, warning = load_config(self.config_path)
        self.assertIsNone(warning)
        self.assertEqual(loaded_cfg.base_url, "https://api.openai.com/v1")
        self.assertEqual(loaded_cfg.api_key, "sk-test1234567890")
        self.assertEqual(loaded_cfg.model, "gpt-4o")
        self.assertEqual(loaded_cfg.timeout_seconds, 45.0)

    def test_load_corrupted_config_falls_back_to_defaults(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("{corrupt_json: true, invalid")

        loaded_cfg, warning = load_config(self.config_path)
        self.assertIsNotNone(warning)
        self.assertEqual(loaded_cfg.base_url, DEFAULT_BASE_URL)
        self.assertEqual(loaded_cfg.model, DEFAULT_MODEL)

    def test_env_override_priority(self):
        # Save a config first
        cfg = Config(base_url="https://file-url.com", api_key="file-key", model="file-model")
        save_config(cfg, self.config_path)

        # Set environment overrides
        os.environ["BLENDER_AI_BASE_URL"] = "https://env-url.com"
        os.environ["BLENDER_AI_API_KEY"] = "env-key"
        os.environ["BLENDER_AI_MODEL"] = "env-model"
        os.environ["BLENDER_AI_TIMEOUT"] = "60"

        loaded_cfg, _ = load_config(self.config_path)
        self.assertEqual(loaded_cfg.base_url, "https://env-url.com")
        self.assertEqual(loaded_cfg.api_key, "env-key")
        self.assertEqual(loaded_cfg.model, "env-model")
        self.assertEqual(loaded_cfg.timeout_seconds, 60.0)

    def test_mask_api_key(self):
        self.assertEqual(mask_api_key(""), "")
        self.assertEqual(mask_api_key(None), "")
        self.assertEqual(mask_api_key("short"), "********")
        self.assertEqual(mask_api_key("sk-abcdefgh12345678"), "sk-a...5678")

    def test_is_local_endpoint(self):
        self.assertTrue(is_local_endpoint("http://localhost:11434/v1"))
        self.assertTrue(is_local_endpoint("http://127.0.0.1:1234/v1"))
        self.assertTrue(is_local_endpoint("http://[::1]:8080/v1"))
        self.assertFalse(is_local_endpoint("https://api.openai.com/v1"))
        self.assertFalse(is_local_endpoint("https://openrouter.ai/api/v1"))

    def test_is_network_allowed(self):
        # Localhost allowed regardless of online_access
        allowed, _ = is_network_allowed("http://localhost:11434/v1", online_access_enabled=False)
        self.assertTrue(allowed)

        # Remote rejected if online_access is False
        allowed, msg = is_network_allowed("https://api.openai.com/v1", online_access_enabled=False)
        self.assertFalse(allowed)
        self.assertIn("online access is disabled", msg)

        # Remote allowed if online_access is True
        allowed, _ = is_network_allowed("https://api.openai.com/v1", online_access_enabled=True)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
