from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.config import (
    ConfigError,
    ensure_config,
    expand_env,
    load_settings,
    redact_config,
    settings_path,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_created_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".MinimalCode"
            settings = load_settings(home)
            self.assertEqual(settings.get("provider"), "deepseek")
            self.assertFalse(settings.source.exists())

            path = ensure_config(home)
            self.assertEqual(path, settings_path(home))
            created = load_settings(home, require_file=True)
            self.assertEqual(created.get("model"), "deepseek-flash")
            self.assertTrue(created.get("output.markdown_highlight"))
            self.assertEqual(created.get("output.theme"), "dark")
            _, provider = created.provider_config()
            self.assertEqual(provider["protocol"], "openai_responses")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["max_turns"],
                1000,
            )

    def test_home_environment_override(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"MINIMALCODE_HOME": temp}),
        ):
            self.assertEqual(settings_path(), Path(temp).resolve() / "settings.json")

    def test_invalid_home_is_reported_as_config_error(self) -> None:
        with (
            patch(
                "minimalcode.config.Path.expanduser",
                side_effect=RuntimeError("no home"),
            ),
            self.assertRaisesRegex(ConfigError, "invalid home"),
        ):
            settings_path("~missing")

    def test_environment_expansion(self) -> None:
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "secret-value"}):
            self.assertEqual(expand_env("${TEST_PROVIDER_KEY}"), "secret-value")
            self.assertEqual(expand_env("${MISSING_KEY}"), "${MISSING_KEY}")

    def test_utf8_bom_settings_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))
            loaded = load_settings(home, require_file=True)
            self.assertEqual(loaded.get("model"), "deepseek-flash")

    def test_non_utf8_settings_file_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            path.write_bytes(b"\xff\xfe\x00")
            with self.assertRaisesRegex(ConfigError, "cannot read"):
                load_settings(home, require_file=True)

    def test_invalid_provider_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["provider"] = "missing"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(home, require_file=True)

    def test_invalid_output_theme_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["output"]["theme"] = "solarized"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(home, require_file=True)

    def test_non_string_output_theme_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["output"]["theme"] = ["dark"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "output.theme"):
                load_settings(home, require_file=True)

    def test_invalid_thinking_level_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["thinking"] = "maximum"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "thinking"):
                load_settings(home, require_file=True)

    def test_invalid_provider_settings_are_rejected(self) -> None:
        cases = (
            ("protocol", "native"),
            ("base_url", "not a url"),
            ("context_window", 0),
            ("timeout_seconds", 0),
            ("stream", "yes"),
            ("headers", "not-an-object"),
        )
        for key, value in cases:
            with (
                self.subTest(key=key),
                tempfile.TemporaryDirectory() as temp,
            ):
                home = Path(temp)
                path = ensure_config(home)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["providers"]["deepseek"][key] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_settings(home, require_file=True)

    def test_invalid_nested_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            original = json.loads(path.read_text(encoding="utf-8"))
            cases = (
                (("shell", "timeout_seconds"), "120"),
                (("tools", "allow_outside_workspace"), "false"),
                (("tools", "max_results"), True),
                (("context", "compact_at"), 1.5),
                (("context", "keep_messages"), 2),
                (("session", "save"), "yes"),
            )
            for keys, value in cases:
                with self.subTest(keys=keys):
                    data = json.loads(json.dumps(original))
                    target = data
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = value
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ConfigError):
                        load_settings(home, require_file=True)

    def test_unresolved_provider_environment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["providers"]["deepseek"]["headers"] = {
                "X-Test": "${MINIMALCODE_TEST_MISSING_VALUE}"
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(home, require_file=True)
                with self.assertRaisesRegex(ConfigError, "unset environment variable"):
                    settings.provider_config()

    def test_unreadable_settings_file_is_not_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            ensure_config(home)
            with (
                patch(
                    "minimalcode.config.Path.read_text",
                    side_effect=PermissionError("denied"),
                ),
                self.assertRaisesRegex(ConfigError, "cannot read"),
            ):
                load_settings(home, require_file=True)

    def test_redaction(self) -> None:
        data = {
            "api_key": "abc",
            "apiKey": "camel",
            "api_key_env": "SAFE_VARIABLE_NAME",
            "nested": {"authorization": "Bearer x", "name": "ok"},
        }
        redacted = redact_config(data)
        self.assertEqual(redacted["api_key"], "<redacted>")
        self.assertEqual(redacted["apiKey"], "<redacted>")
        self.assertEqual(redacted["api_key_env"], "SAFE_VARIABLE_NAME")
        self.assertEqual(redacted["nested"]["authorization"], "<redacted>")
        self.assertEqual(redacted["nested"]["name"], "ok")

    def test_redaction_keeps_token_counts_and_redacts_tokens(self) -> None:
        data = {
            "max_tokens": 100,
            "prompt_tokens": 5,
            "reasoning_tokens": 2,
            "tokenizer": "bpe",
            "access_token": "secret",
            "X-Api-Key": "secret",
            "client_secret": "secret",
            "api_key_env": "SAFE_VARIABLE_NAME",
        }
        redacted = redact_config(data)
        self.assertEqual(redacted["max_tokens"], 100)
        self.assertEqual(redacted["prompt_tokens"], 5)
        self.assertEqual(redacted["reasoning_tokens"], 2)
        self.assertEqual(redacted["tokenizer"], "bpe")
        self.assertEqual(redacted["access_token"], "<redacted>")
        self.assertEqual(redacted["X-Api-Key"], "<redacted>")
        self.assertEqual(redacted["client_secret"], "<redacted>")
        self.assertEqual(redacted["api_key_env"], "SAFE_VARIABLE_NAME")


if __name__ == "__main__":
    unittest.main()
