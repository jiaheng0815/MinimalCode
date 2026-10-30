from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from minimalcode.cli import run
from minimalcode.config import ConfigError, ensure_config, load_settings
from minimalcode.provider.anthropic_env import from_anthropic_env


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "env-token",
        "ANTHROPIC_MODEL": "deepseek-flash[1m]",
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    }
    values.update(overrides)
    return values


class AnthropicEnvTests(unittest.TestCase):
    def test_claude_code_variables_build_a_provider(self) -> None:
        result = from_anthropic_env(_env())

        self.assertEqual(result.provider, "anthropic-env")
        self.assertEqual(result.model, "deepseek-flash")
        config = result.config
        self.assertEqual(config["protocol"], "anthropic_messages")
        self.assertEqual(config["base_url"], "https://api.deepseek.com/anthropic")
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertEqual(config["context_window"], 1_000_000)
        self.assertEqual(config["max_tokens"], 8192)
        self.assertTrue(config["stream"])
        self.assertTrue(config["send_reasoning_effort"])
        # ANTHROPIC_AUTH_TOKEN is a bearer credential, not an x-api-key one.
        self.assertEqual(config["headers"], {"Authorization": "Bearer env-token"})
        self.assertNotIn("api_key", config)
        self.assertEqual(result.thinking, "max")
        self.assertEqual(result.compact_at, 0.7864)

    def test_api_key_uses_the_generated_x_api_key_header(self) -> None:
        env = _env()
        del env["ANTHROPIC_AUTH_TOKEN"]
        env["ANTHROPIC_API_KEY"] = "key-value"

        config = from_anthropic_env(env).config

        self.assertEqual(config["api_key"], "key-value")
        self.assertEqual(config["headers"], {})

    def test_auth_token_wins_when_both_credentials_are_set(self) -> None:
        env = _env()
        env["ANTHROPIC_API_KEY"] = "key-value"

        config = from_anthropic_env(env).config

        self.assertEqual(config["headers"], {"Authorization": "Bearer env-token"})
        self.assertNotIn("api_key", config)

    def test_missing_credentials_are_reported(self) -> None:
        env = _env()
        del env["ANTHROPIC_AUTH_TOKEN"]
        with self.assertRaisesRegex(ConfigError, "ANTHROPIC_AUTH_TOKEN"):
            from_anthropic_env(env)
        with self.assertRaisesRegex(ConfigError, "ANTHROPIC_AUTH_TOKEN"):
            from_anthropic_env({})

    def test_missing_model_is_reported(self) -> None:
        env = _env()
        del env["ANTHROPIC_MODEL"]
        with self.assertRaisesRegex(ConfigError, "ANTHROPIC_MODEL"):
            from_anthropic_env(env)
        # The Claude Code fallback model name is accepted as a model source.
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = "claude-sonnet-4-5"
        self.assertEqual(from_anthropic_env(env).model, "claude-sonnet-4-5")

    def test_cli_overrides_win_over_the_environment(self) -> None:
        result = from_anthropic_env(_env(), model="custom-model", thinking="low")

        self.assertEqual(result.model, "custom-model")
        self.assertEqual(result.config["model"], "custom-model")
        self.assertEqual(result.thinking, "low")

    def test_defaults_and_context_suffixes(self) -> None:
        env = _env()
        del env["ANTHROPIC_BASE_URL"]
        del env["ANTHROPIC_MODEL"]
        with self.assertRaises(ConfigError):
            from_anthropic_env(env)

        env["ANTHROPIC_MODEL"] = "plain-model"
        result = from_anthropic_env(env)
        self.assertEqual(
            result.config["base_url"], "https://api.deepseek.com/anthropic"
        )
        self.assertEqual(result.model, "plain-model")
        self.assertEqual(result.config["context_window"], 200_000)

        for model, window in {
            "model[200k]": 200_000,
            "model[2m]": 2_000_000,
            "model[1M]": 1_000_000,
        }.items():
            with self.subTest(model=model):
                env["ANTHROPIC_MODEL"] = model
                result = from_anthropic_env(env)
                self.assertEqual(result.model, "model")
                self.assertEqual(result.config["context_window"], window)

        # A bracket that is not a context marker stays part of the model name.
        env["ANTHROPIC_MODEL"] = "model[preview]"
        self.assertEqual(from_anthropic_env(env).model, "model[preview]")

    def test_explicit_window_and_output_cap_win(self) -> None:
        env = _env()
        env["ANTHROPIC_CONTEXT_WINDOW"] = "32000"
        env["ANTHROPIC_MAX_TOKENS"] = "1024"

        result = from_anthropic_env(env)

        self.assertEqual(result.config["context_window"], 32_000)
        self.assertEqual(result.config["max_tokens"], 1024)
        # 786432 tokens no longer fit in a 32k window: compaction stays late,
        # but the ratio must remain inside (0, 1).
        self.assertEqual(result.compact_at, 0.95)

    def test_invalid_numeric_variables_are_reported(self) -> None:
        for name, value in {
            "ANTHROPIC_CONTEXT_WINDOW": "wide",
            "ANTHROPIC_MAX_TOKENS": "0",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "-5",
        }.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ConfigError, name),
            ):
                from_anthropic_env(_env(**{name: value}))

    def test_invalid_effort_from_the_environment_is_ignored(self) -> None:
        result = from_anthropic_env(_env(CLAUDE_CODE_EFFORT_LEVEL="turbo"))
        self.assertIsNone(result.thinking)
        # An explicit flag is still checked instead of silently dropped.
        with self.assertRaisesRegex(ConfigError, "unknown effort"):
            from_anthropic_env(_env(), thinking="turbo")


class _EnvHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    seen_headers: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.requests.append(json.loads(self.rfile.read(length)))
        self.seen_headers.append(dict(self.headers))
        body = json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "from-env"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        return


class AnthropicEnvCliTests(unittest.TestCase):
    def test_run_uses_the_environment_without_touching_settings(self) -> None:
        _EnvHandler.requests = []
        _EnvHandler.seen_headers = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _EnvHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / ".MinimalCode"
                workspace = root / "workspace"
                workspace.mkdir()
                settings_path = ensure_config(home)
                before = settings_path.read_bytes()
                port = server.server_address[1]
                env = _env(
                    ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}/anthropic",
                )
                # Neutralize host variables that would change the expected
                # request if the surrounding environment happened to define them.
                env["ANTHROPIC_API_KEY"] = ""
                env["ANTHROPIC_CONTEXT_WINDOW"] = ""
                env["ANTHROPIC_MAX_TOKENS"] = ""

                stdout = io.StringIO()
                with (
                    patch.dict(os.environ, env),
                    redirect_stdout(stdout),
                    redirect_stderr(io.StringIO()),
                ):
                    code = run(
                        [
                            "--anthropic-env",
                            "--json",
                            "--no-session",
                            "--home",
                            str(home),
                            "--cwd",
                            str(workspace),
                            "say hi",
                        ]
                    )

                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["text"], "from-env")
                self.assertTrue(payload["completed"])

                request = _EnvHandler.requests[0]
                self.assertEqual(request["model"], "deepseek-flash")
                self.assertEqual(request["max_tokens"], 8192)
                self.assertEqual(request["thinking"]["budget_tokens"], 7168)
                self.assertEqual(
                    _EnvHandler.seen_headers[0].get("Authorization"),
                    "Bearer env-token",
                )
                self.assertNotIn("x-api-key", _EnvHandler.seen_headers[0])

                # The file-configuration path is untouched: no provider entry and
                # no rewrite happen on disk.
                self.assertEqual(settings_path.read_bytes(), before)
                reloaded = load_settings(home, require_file=True)
                self.assertNotIn("anthropic-env", reloaded.data["providers"])
                self.assertNotEqual(reloaded.get("provider"), "anthropic-env")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_anthropic_env_conflicts_with_provider(self) -> None:
        with (
            patch.dict(os.environ, _env()),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            run(["--anthropic-env", "--provider", "deepseek", "hi"])

    def test_missing_credentials_stop_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            env = _env()
            env.pop("ANTHROPIC_AUTH_TOKEN")
            with (
                patch.dict(os.environ, env),
                redirect_stderr(io.StringIO()),
                self.assertRaisesRegex(ConfigError, "ANTHROPIC_AUTH_TOKEN"),
            ):
                run(
                    [
                        "--anthropic-env",
                        "--no-session",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "hi",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
