"""Bundled provider presets and per-provider defaults.

A preset may pin an effort level (``thinking``) for an endpoint that only works
well at one setting. It is a *default*: ``--thinking`` and ``/effort`` still win,
and every other provider keeps the global ``thinking`` value.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minimalcode.config import (
    DEFAULT_SETTINGS,
    ConfigError,
    load_settings,
    write_settings_document,
)
from minimalcode.provider import create_provider


class NinferPresetTests(unittest.TestCase):
    """``ninfer``: a keyless local endpoint on port 8080 with a 262k window."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.settings = load_settings(self.home)

    def test_the_preset_is_keyless_and_local(self) -> None:
        preset = DEFAULT_SETTINGS["providers"]["ninfer"]
        self.assertEqual(preset["protocol"], "openai_chat")
        self.assertEqual(preset["base_url"], "http://localhost:8080/v1")
        self.assertIs(preset["require_api_key"], False)
        self.assertEqual(preset["context_window"], 262144)
        self.assertEqual(preset["thinking"], "xhigh")

    def test_it_builds_without_a_credential(self) -> None:
        provider, name, model = create_provider(
            self.settings, provider_name="ninfer", model="local-model"
        )
        try:
            self.assertEqual(name, "ninfer")
            self.assertEqual(model, "local-model")
            self.assertEqual(provider.thinking, "xhigh")
            self.assertEqual(provider.context_window, 262144)
            # `require_api_key: false` must not leave a stray auth header.
            self.assertNotIn("authorization", {key.lower() for key in provider.headers})
        finally:
            provider.close()

    def test_the_loopback_endpoint_ignores_the_proxy_environment(self) -> None:
        """A local server behind Clash would otherwise be proxied away."""
        provider, _, _ = create_provider(
            self.settings, provider_name="ninfer", model="m"
        )
        try:
            self.assertEqual(provider.base_url, "http://localhost:8080/v1")
        finally:
            provider.close()

    def test_an_explicit_effort_beats_the_preset(self) -> None:
        provider, _, _ = create_provider(
            self.settings, provider_name="ninfer", model="m", thinking="low"
        )
        try:
            self.assertEqual(provider.thinking, "low")
        finally:
            provider.close()

    def test_other_providers_keep_the_global_effort(self) -> None:
        provider, _, _ = create_provider(
            self.settings, provider_name="ollama", model="m"
        )
        try:
            self.assertEqual(provider.thinking, self.settings.get("thinking"))
        finally:
            provider.close()

    def test_a_bad_preset_effort_is_rejected(self) -> None:
        document = {"providers": {"ninfer": {"thinking": "turbo"}}}
        with self.assertRaisesRegex(ConfigError, "thinking must be one of"):
            write_settings_document(document, self.home)

    def test_a_preset_effort_is_case_insensitive(self) -> None:
        document = {"providers": {"ninfer": {"thinking": " XHIGH "}}}
        write_settings_document(document, self.home)
        reloaded = load_settings(self.home)
        self.assertEqual(
            reloaded.data["providers"]["ninfer"]["thinking"],
            "xhigh",
        )


if __name__ == "__main__":
    unittest.main()
