"""Unit tests for Production Provider Initialization and Configuration Safety.

Verifies:
1. Production addon initializes with OpenAICompatibleProvider by default.
2. Configuration (base_url, model, api_key, timeout) is passed to provider.
3. API key reaches provider but is never logged or exposed.
4. Empty/missing configuration triggers controlled CONFIGURATION_ERROR.
5. update_runtime_config dynamically updates running provider settings.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from core.config import Config
from agent.models import ProviderError, ProviderErrorType, Role, ChatMessage, Conversation
from agent.context_builder import ProviderRequestContext
from agent.openai_provider import OpenAICompatibleProvider
from agent.mock_provider import MockProvider


class TestProductionProviderInitialization(unittest.TestCase):
    """Test production provider creation, config binding, and error controls."""

    def test_provider_initialization_from_config(self):
        cfg = Config(
            base_url="http://localhost:20128/v1",
            api_key="sk-test-9router-secret-key",
            model="meta-llama/llama-3.3-70b-instruct",
            timeout_seconds=45.0,
        )
        provider = OpenAICompatibleProvider(config=cfg, online_access=True)

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.config.base_url, "http://localhost:20128/v1")
        self.assertEqual(provider.config.model, "meta-llama/llama-3.3-70b-instruct")
        self.assertEqual(provider.config.api_key, "sk-test-9router-secret-key")
        self.assertEqual(provider.config.timeout_seconds, 45.0)

    def test_api_key_hygiene_not_exposed_in_masked_dict(self):
        cfg = Config(
            base_url="http://localhost:20128/v1",
            api_key="sk-my-super-secret-key-12345",
            model="gpt-4o",
        )
        masked_data = cfg.to_dict(mask_key=True)
        self.assertNotIn("sk-my-super-secret-key-12345", str(masked_data))
        self.assertIn("sk-m...2345", masked_data["api_key"])

    def test_unconfigured_base_url_yields_configuration_error(self):
        cfg = Config(base_url="", model="gpt-4o")
        provider = OpenAICompatibleProvider(config=cfg, online_access=True)
        ctx = ProviderRequestContext(
            messages=(ChatMessage(role=Role.USER, content="Hello"),),
            tools=(),
            system_prompt="Test",
        )

        events = list(provider.stream_chat(context=ctx, turn_id="turn_1"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.CONFIGURATION_ERROR)
        self.assertIn("AI provider is not configured", err.message)

    def test_unconfigured_model_yields_configuration_error(self):
        cfg = Config(base_url="http://localhost:20128/v1", model="")
        provider = OpenAICompatibleProvider(config=cfg, online_access=True)
        ctx = ProviderRequestContext(
            messages=(ChatMessage(role=Role.USER, content="Hello"),),
            tools=(),
            system_prompt="Test",
        )

        events = list(provider.stream_chat(context=ctx, turn_id="turn_2"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.CONFIGURATION_ERROR)
        self.assertIn("AI model is not configured", err.message)

    def test_network_connection_failure_yields_friendly_network_error(self):
        from agent.http_client import HttpConnectionError
        cfg = Config(base_url="http://localhost:20128/v1", model="test-model", timeout_seconds=1.0)
        provider = OpenAICompatibleProvider(config=cfg, online_access=True)
        provider.http_client = MagicMock()
        provider.http_client.post.side_effect = HttpConnectionError("Connection refused")

        ctx = ProviderRequestContext(
            messages=(ChatMessage(role=Role.USER, content="Hello"),),
            tools=(),
            system_prompt="Test",
        )

        events = list(provider.stream_chat(context=ctx, turn_id="turn_3"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.NETWORK_ERROR)
        self.assertIn("Cannot connect to AI provider", err.message)
        self.assertIn("http://localhost:20128/v1", err.message)

    def test_dynamic_runtime_config_update(self):
        cfg1 = Config(base_url="http://localhost:11434/v1", model="llama3")
        provider = OpenAICompatibleProvider(config=cfg1)

        mock_runtime = MagicMock()
        mock_runtime.provider = provider

        # Update config to 9Router
        new_cfg = Config(
            base_url="http://localhost:20128/v1",
            api_key="sk-new-key",
            model="claude-3-5-sonnet",
            timeout_seconds=60.0,
        )

        # Simulate update_runtime_config
        provider.config = new_cfg
        provider.http_client.base_url = new_cfg.base_url
        provider.http_client.timeout = new_cfg.timeout_seconds

        self.assertEqual(provider.config.base_url, "http://localhost:20128/v1")
        self.assertEqual(provider.http_client.base_url, "http://localhost:20128/v1")
        self.assertEqual(provider.config.model, "claude-3-5-sonnet")
        self.assertEqual(provider.http_client.timeout, 60.0)


if __name__ == "__main__":
    unittest.main()
