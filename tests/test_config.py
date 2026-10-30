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
            self.assertFalse(created.get("sandbox.enabled"))
            self.assertEqual(created.get("sandbox.network_policy"), "deny")
            self.assertFalse(created.get("sandbox.allow_sensitive_files"))
            _, provider = created.provider_config()
            self.assertEqual(provider["protocol"], "openai_responses")
            anthropic_name, anthropic = created.provider_config("deepseek-anthropic")
            self.assertEqual(anthropic_name, "deepseek-anthropic")
            self.assertEqual(anthropic["protocol"], "anthropic_messages")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["max_turns"],
                1000,
            )

    def test_builtin_providers_use_vendor_key_variables(self) -> None:
        expected = {
            "deepseek": "DEEPSEEK_API_KEY",
            "deepseek-anthropic": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GEMINI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "xai": "XAI_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "zhipu": "ZHIPUAI_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "together": "TOGETHER_API_KEY",
            "fireworks": "FIREWORKS_API_KEY",
            "perplexity": "PERPLEXITY_API_KEY",
            "cerebras": "CEREBRAS_API_KEY",
            "sambanova": "SAMBANOVA_API_KEY",
            "nebius": "NEBIUS_API_KEY",
            "deepinfra": "DEEPINFRA_TOKEN",
            "novita": "NOVITA_API_KEY",
            "cohere": "COHERE_API_KEY",
            "github": "GITHUB_TOKEN",
            "volcengine": "ARK_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "hunyuan": "HUNYUAN_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
            "modelscope": "MODELSCOPE_API_KEY",
            "baichuan": "BAICHUAN_API_KEY",
            "yi": "YI_API_KEY",
            "stepfun": "STEP_API_KEY",
            "spark": "SPARK_API_PASSWORD",
            "ppio": "PPIO_API_KEY",
        }
        with tempfile.TemporaryDirectory() as temp:
            settings = load_settings(Path(temp) / ".MinimalCode")
            for name, env_name in expected.items():
                with self.subTest(name=name):
                    resolved_name, provider = settings.provider_config(name)
                    self.assertEqual(resolved_name, name)
                    self.assertEqual(provider["api_key_env"], env_name)
            for name in ("ollama", "lmstudio", "vllm"):
                with self.subTest(name=name):
                    _, provider = settings.provider_config(name)
                    self.assertFalse(provider["require_api_key"])
                    self.assertNotIn("api_key_env", provider)
                    self.assertTrue(provider["base_url"].startswith("http://localhost"))

    def test_builtin_presets_build_a_complete_endpoint(self) -> None:
        from minimalcode.provider import OpenAICompatibleProvider

        with tempfile.TemporaryDirectory() as temp:
            settings = load_settings(Path(temp) / ".MinimalCode")
            for name in settings.data["providers"]:
                with self.subTest(name=name):
                    _, provider_config = settings.provider_config(name)
                    env_name = provider_config.get("api_key_env")
                    patch_env = {env_name: "test-key"} if env_name else {}
                    with patch.dict(os.environ, patch_env, clear=False):
                        provider = OpenAICompatibleProvider(
                            name=name,
                            config=provider_config,
                            model="test-model",
                            thinking="high",
                        )
                        try:
                            endpoint = provider._endpoint()
                        finally:
                            provider.close()
                    self.assertTrue(endpoint.startswith(("https://", "http://")))
                    self.assertTrue(
                        endpoint.endswith(
                            ("/chat/completions", "/responses", "/messages")
                        ),
                        endpoint,
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
            ("max_tokens", 0),
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

    def test_invalid_sandbox_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            original = json.loads(path.read_text(encoding="utf-8"))
            cases = (
                (("enabled",), "yes"),
                (("backend",), "chroot"),
                (("network_policy",), "maybe"),
                (("network_policy",), "allowlist"),
                (("allowed_roots",), "/tmp"),
                (("allowed_hosts",), [1]),
                (("timeout_seconds",), 0),
                (("max_output_chars",), 0),
                (("allow_sensitive_files",), "no"),
                (("max_memory_mb",), -1),
                (("max_processes",), -1),
                (("docker_image",), ""),
            )
            for keys, value in cases:
                with self.subTest(keys=keys, value=value):
                    data = json.loads(json.dumps(original))
                    target = data["sandbox"]
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
