"""DeepSeek features supplied by a second configured provider.

A session can talk to another vendor and still keep OCR, speech, and the
standalone Search API: those are plain HTTP calls to ``api.deepseek.com``, so any
DeepSeek block in the settings file can run them. The request-side features
(server tools, documents, safety) ride on the chat request and stay tied to the
chat provider. These tests pin that split down, including which block wins.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from minimalcode.config import load_settings, write_settings_document
from minimalcode.deepseek import feature_source, features, is_deepseek_api

_MEDIA = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "ocr_path": "v1/tools/ocr",
    "asr_path": "v1/tools/asr",
    "tts_path": "v1/tools/tts",
    "files_path": "files",
}
_WEB_TOOLS = {
    "enabled": True,
    "base_url": "https://api.deepseek.com/v1/tools",
    "search_path": "search",
    "fetch_path": "fetch",
    "timeout_seconds": 30,
    "default_count": 5,
    "max_count": 50,
    "max_content_chars": 1000,
    "default_need_content": False,
    "min_rerank_score": 0,
    "headers": {},
}


def _deepseek(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "protocol": "openai_chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret",
        "server_tools": {"enabled": True, "web_search": True, "read_url": True},
        "web_tools": dict(_WEB_TOOLS),
        "media": dict(_MEDIA),
        "safety_check": {"level": "high"},
        "session_title": {"enabled": True},
    }
    config.update(overrides)
    return config


class FeatureSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        # The bundled presets read DEEPSEEK_API_KEY; a keyless block is skipped
        # by design, so the tests that expect a source set one.
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)

    def _settings(self, document: dict[str, Any]) -> Any:
        write_settings_document(document, self.home)
        return load_settings(self.home)

    def test_the_active_deepseek_provider_is_preferred(self) -> None:
        settings = self._settings({"provider": "deepseek", "model": "deepseek-flash"})
        source = feature_source(settings)
        self.assertIsNotNone(source)
        self.assertEqual(source[0], "deepseek")

    def test_another_chat_provider_still_finds_the_deepseek_block(self) -> None:
        settings = self._settings({"provider": "ollama", "model": "qwen"})
        source = feature_source(settings, "ollama")
        self.assertIsNotNone(source)
        self.assertEqual(source[0], "deepseek")
        self.assertTrue(is_deepseek_api(source[1]))

    def test_a_block_with_an_unset_placeholder_is_skipped(self) -> None:
        """The host decides, and a block that cannot be expanded is not it."""
        missing = "${MISSING_DEEPSEEK_URL}"
        settings = self._settings(
            {
                "provider": "ollama",
                "model": "qwen",
                "providers": {
                    "deepseek": {"base_url": missing},
                    "deepseek-anthropic": {"base_url": missing},
                    "gateway": {
                        "protocol": "openai_chat",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key": "gateway-secret",
                    },
                },
            }
        )
        source = feature_source(settings, "ollama")
        self.assertIsNotNone(source)
        # The name says nothing: the documented host is what counts.
        self.assertEqual(source[0], "gateway")

    def test_no_deepseek_block_means_no_features(self) -> None:
        settings = self._settings(
            {
                "provider": "ollama",
                "model": "qwen",
                "providers": {
                    "deepseek": {"base_url": "https://gateway.example.com/v1"},
                    "deepseek-anthropic": {"base_url": "https://example.com/v1"},
                },
            }
        )
        self.assertIsNone(feature_source(settings, "ollama"))

    def test_a_block_without_its_key_is_skipped(self) -> None:
        """Advertising OCR on a keyless block would only fail on the first call."""
        settings = self._settings({"provider": "ollama", "model": "qwen"})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertIsNone(feature_source(settings, "ollama"))

    def test_a_keyless_block_that_does_not_require_one_is_used(self) -> None:
        settings = self._settings(
            {
                "provider": "ollama",
                "model": "qwen",
                "providers": {
                    "deepseek": {
                        "base_url": "https://api.deepseek.com/v1",
                        "require_api_key": False,
                    },
                    "deepseek-anthropic": {
                        "base_url": "https://example.com/v1",
                        "require_api_key": False,
                    },
                },
            }
        )
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            source = feature_source(settings, "ollama")
        self.assertIsNotNone(source)
        self.assertEqual(source[0], "deepseek")


class SplitFeatureTests(unittest.TestCase):
    def test_the_request_side_needs_the_chat_provider(self) -> None:
        result = features(
            _deepseek(base_url="https://api.openai.com/v1"),
            source=_deepseek(),
            source_name="deepseek",
        )
        self.assertTrue(result["available"])
        self.assertFalse(result["active"])
        self.assertEqual(result["provider"], "deepseek")
        self.assertTrue(result["ocr"])
        self.assertTrue(result["asr"])
        self.assertTrue(result["tts"])
        self.assertTrue(result["search_api"])
        self.assertFalse(result["documents"])
        self.assertFalse(result["web_search"])
        self.assertFalse(result["read_url"])
        self.assertFalse(result["safety"])
        self.assertIn("DeepSeek", result["active_reason"])

    def test_one_provider_keeps_the_original_answer(self) -> None:
        result = features(_deepseek())
        self.assertTrue(result["active"])
        self.assertTrue(result["documents"])
        self.assertTrue(result["web_search"])
        self.assertTrue(result["safety"])
        self.assertEqual(result["active_reason"], "")

    def test_the_web_app_protocol_explains_itself(self) -> None:
        result = features(
            _deepseek(protocol="deepseek_web", base_url="https://chat.deepseek.com")
        )
        self.assertFalse(result["active"])
        self.assertIn("web-app", result["active_reason"])


if __name__ == "__main__":
    unittest.main()
