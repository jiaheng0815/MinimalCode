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

    def test_environment_expansion(self) -> None:
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "secret-value"}):
            self.assertEqual(expand_env("${TEST_PROVIDER_KEY}"), "secret-value")
            self.assertEqual(expand_env("${MISSING_KEY}"), "${MISSING_KEY}")

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

    def test_redaction(self) -> None:
        data = {
            "api_key": "abc",
            "api_key_env": "SAFE_VARIABLE_NAME",
            "nested": {"authorization": "Bearer x", "name": "ok"},
        }
        redacted = redact_config(data)
        self.assertEqual(redacted["api_key"], "<redacted>")
        self.assertEqual(redacted["api_key_env"], "SAFE_VARIABLE_NAME")
        self.assertEqual(redacted["nested"]["authorization"], "<redacted>")
        self.assertEqual(redacted["nested"]["name"], "ok")


if __name__ == "__main__":
    unittest.main()
