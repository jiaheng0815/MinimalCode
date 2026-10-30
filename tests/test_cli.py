from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from minimalcode.cli import _interactive, _read_message, run
from minimalcode.config import EFFORT_LEVELS, ensure_config, load_settings
from minimalcode.provider import ProviderError, ProviderReply
from minimalcode.session import SessionStore


class FakeCliProvider:
    name = "fake"
    model = "fake-model"
    thinking = "high"
    context_window = 1000

    def __init__(self) -> None:
        self.closed = False
        self.config: dict = {}
        self.messages: list[dict] = []

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        self.messages = messages
        if on_text:
            on_text("completed")
        return ProviderReply(
            content="completed",
            finish_reason="stop",
            usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        )

    def complete_text(self, messages):
        return "summary"

    def close(self) -> None:
        self.closed = True


class FakeClosingClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class StubAgent:
    """Minimal stand-in for the interactive loop's agent contract."""

    def __init__(self, cwd: Path, *, failures: int = 0) -> None:
        self.shell = SimpleNamespace(label="stub-shell")
        self.session = SimpleNamespace(cwd=cwd, path=None)
        self.provider = FakeCliProvider()
        self.tools = SimpleNamespace(
            web_client=None,
            vision_client=None,
            thinking="high",
            subagent_enabled=True,
        )
        self.prompts: list[str] = []
        self._failures = failures

    def switch_runtime(self, provider, tools) -> None:
        self.provider = provider
        self.tools = tools

    def new_session(self, session, tools) -> None:
        self.session = session
        self.tools = tools

    def run(self, text: str) -> SimpleNamespace:
        self.prompts.append(text)
        if self._failures:
            self._failures -= 1
            raise OSError("simulated failure")
        return SimpleNamespace(
            text="done\n",
            completed=True,
            usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        )

    def status(self) -> dict:
        return {}

    def compact(self) -> bool:
        return False

    def write_summary(self) -> Path:
        target = self.session.cwd / "AGENTS.md"
        target.write_text("summary\n", encoding="utf-8")
        return target


class CliTests(unittest.TestCase):
    def test_json_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            provider = FakeCliProvider()
            stdout = io.StringIO()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                redirect_stdout(stdout),
            ):
                code = run(
                    [
                        "--json",
                        "--no-session",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "say completed",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["text"], "completed")
            self.assertTrue(payload["completed"])
            self.assertIsNone(payload["session"])
            self.assertTrue(provider.closed)
            self.assertEqual(payload["usage"]["prompt_tokens"], 5)
            self.assertEqual(payload["usage"]["completion_tokens"], 1)
            self.assertEqual(payload["usage"]["total_tokens"], 6)
            self.assertEqual(payload["session_usage"], payload["usage"])
            self.assertFalse(payload["usage_estimated"])

    def test_resumed_session_uses_requested_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            original = root / "original"
            requested = root / "requested"
            original.mkdir()
            requested.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            session = SessionStore(settings).create(
                original,
                provider="fake",
                model="fake-model",
                save=True,
            )
            provider = FakeCliProvider()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                redirect_stdout(io.StringIO()),
            ):
                code = run(
                    [
                        "--json",
                        "--home",
                        str(home),
                        "--session",
                        session.session_id,
                        "--cwd",
                        str(requested),
                        "say completed",
                    ]
                )
            self.assertEqual(code, 0)
            system_message = next(
                message["content"]
                for message in provider.messages
                if message["role"] == "system"
            )
            self.assertIn(str(requested.resolve()), system_message)

    def test_session_path_must_be_a_jsonl_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            notes = root / "notes.txt"
            notes.write_text("important\n", encoding="utf-8")
            provider = FakeCliProvider()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertRaises(ValueError),
            ):
                run(
                    [
                        "--json",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "--session",
                        str(notes),
                        "say completed",
                    ]
                )
            self.assertEqual(notes.read_text(encoding="utf-8"), "important\n")

    def test_max_effort_is_accepted_and_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = ensure_config(home)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["thinking"] = "max"
            path.write_text(json.dumps(data), encoding="utf-8")
            settings = load_settings(home, require_file=True)
            self.assertEqual(settings.get("thinking"), "max")

    def test_double_dash_keeps_subcommand_words_as_a_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            provider = FakeCliProvider()
            stdout = io.StringIO()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                patch("minimalcode.cli._management_command") as managed,
                redirect_stdout(stdout),
            ):
                code = run(
                    [
                        "--json",
                        "--no-session",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "--",
                        "init",
                        "config",
                        "show",
                    ]
                )
            self.assertEqual(code, 0)
            managed.assert_not_called()
            prompt = next(
                message["content"]
                for message in provider.messages
                if message["role"] == "user"
            )
            self.assertEqual(prompt, "init config show")

    def test_config_set_model_and_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".MinimalCode"
            ensure_config(home)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run(["config", "set-model", "new-model", "--home", str(home)]),
                    0,
                )
                self.assertEqual(
                    run(["config", "set-provider", "deepseek", "--home", str(home)]),
                    0,
                )
            settings = load_settings(home, require_file=True)
            self.assertEqual(settings.get("model"), "new-model")
            self.assertEqual(settings.get("provider"), "deepseek")

    def test_interactive_effort_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            stub.provider = SimpleNamespace(thinking="high")
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(
                [
                    "/effort",
                    "/effort LOW",
                    "/effort none",
                    "/effort minimal",
                    "/effort xhigh",
                    "/effort ULTRA",
                    "/effort max",
                    "/effort turbo",
                    "/exit",
                ]
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.provider.thinking, "max")
            self.assertEqual(stub.tools.thinking, "max")
            self.assertEqual(stub.prompts, [])
            self.assertIn("| stub-shell | effort high", stdout.getvalue())
            for level in EFFORT_LEVELS:
                self.assertIn(level, stdout.getvalue())
            self.assertIn("effort: high", stdout.getvalue())
            self.assertIn("effort: low", stdout.getvalue())
            self.assertIn("effort: none", stdout.getvalue())
            self.assertIn("effort: ultra", stdout.getvalue())
            self.assertIn("effort: max", stdout.getvalue())
            self.assertIn("unknown effort: turbo", stderr.getvalue())

    def test_interactive_model_command_switches_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            old_provider = stub.provider
            old_web = FakeClosingClient()
            old_vision = FakeClosingClient()
            stub.tools.web_client = old_web
            stub.tools.vision_client = old_vision
            args = argparse.Namespace(
                no_session=False,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(
                ["/model", "/model openai/gpt-test", "/new", "/model", "/exit"]
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "fake-model")

            self.assertEqual(code, 0)
            self.assertTrue(old_provider.closed)
            self.assertTrue(old_web.closed)
            self.assertTrue(old_vision.closed)
            self.assertEqual(stub.provider.name, "openai")
            self.assertEqual(stub.provider.model, "gpt-test")
            self.assertEqual(stub.provider.thinking, "high")
            self.assertEqual(stub.tools.provider_name, "openai")
            self.assertEqual(stub.tools.model, "gpt-test")
            self.assertEqual(stub.tools.thinking, "high")
            output = stdout.getvalue()
            self.assertIn("model: fake/fake-model", output)
            self.assertIn("providers with detected credentials", output)
            self.assertIn("OPENAI_API_KEY (set)", output)
            self.assertIn("no key required", output)
            self.assertNotIn("(missing)", output)
            self.assertIn("model: openai/gpt-test", output)
            self.assertEqual(stderr.getvalue(), "")

            rows = store.list()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["provider"], "openai")
            self.assertEqual(rows[0]["model"], "gpt-test")
            persisted = load_settings(home, require_file=True)
            self.assertEqual(persisted.get("provider"), "deepseek")
            self.assertEqual(persisted.get("model"), "deepseek-flash")

            stub.provider.close()
            for client in (stub.tools.web_client, stub.tools.vision_client):
                if client is not None:
                    client.close()

    def test_interactive_model_listing_shows_only_detected_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["/model", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "fake-model")

            self.assertEqual(code, 0)
            output = stdout.getvalue()
            # One hosted provider with a key plus the three keyless local presets.
            self.assertIn("providers with detected credentials (4):", output)
            self.assertIn("openai  -  OPENAI_API_KEY (set)", output)
            self.assertEqual(output.count("no key required"), 3)
            self.assertNotIn("deepseek", output)
            self.assertNotIn("ANTHROPIC_API_KEY", output)
            self.assertEqual(stderr.getvalue(), "")

            stub.provider.close()
            for client in (stub.tools.web_client, stub.tools.vision_client):
                if client is not None:
                    client.close()

    def test_interactive_model_command_uses_provider_default_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["/model deepseek-anthropic", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}),
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "fake-model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.provider.name, "deepseek-anthropic")
            self.assertEqual(stub.provider.model, "deepseek-flash")
            self.assertIn("model: deepseek-anthropic/deepseek-flash", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

            stub.provider.close()
            for client in (stub.tools.web_client, stub.tools.vision_client):
                if client is not None:
                    client.close()

    def test_interactive_model_command_keeps_provider_when_key_is_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            old_provider = stub.provider
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["/model", "/model openai/gpt-test", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "fake-model")

            self.assertEqual(code, 0)
            self.assertIs(stub.provider, old_provider)
            self.assertFalse(old_provider.closed)
            self.assertNotIn("(missing)", stdout.getvalue())
            self.assertIn("model switch failed", stderr.getvalue())
            self.assertIn("OPENAI_API_KEY", stderr.getvalue())

    def test_interactive_model_command_rejects_bad_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(
                [
                    "/model deepseek",
                    "/model missing/model",
                    "/model deepseek/",
                    "/exit",
                ]
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "fake-model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.provider.name, "fake")
            self.assertNotIn("model: ", stdout.getvalue())
            errors = stderr.getvalue()
            self.assertIn("has no default model", errors)
            self.assertIn("unknown provider: missing", errors)
            self.assertIn("usage: /model [provider/model]", errors)

    def test_interactive_loop_survives_a_failed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace, failures=1)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["first", "second", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.prompts, ["first", "second"])
            self.assertIn("simulated failure", stderr.getvalue())

    def test_one_shot_prints_token_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            provider = FakeCliProvider()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = run(
                    [
                        "--no-session",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "say completed",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("completed", stdout.getvalue())
            self.assertIn("[usage]", stderr.getvalue())
            self.assertIn("5 prompt", stderr.getvalue())
            self.assertIn("1 completion", stderr.getvalue())
            self.assertIn("6 total", stderr.getvalue())

    def test_quiet_one_shot_hides_token_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            provider = FakeCliProvider()
            stderr = io.StringIO()
            with (
                patch(
                    "minimalcode.provider.create_provider",
                    return_value=(provider, "fake", "fake-model"),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(stderr),
            ):
                code = run(
                    [
                        "--quiet",
                        "--no-session",
                        "--home",
                        str(home),
                        "--cwd",
                        str(workspace),
                        "say completed",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertNotIn("[usage]", stderr.getvalue())

    def test_interactive_summary_command_writes_agents_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["/help", "/summary", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.prompts, [])
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("/summary", stdout.getvalue())
            self.assertIn("/model", stdout.getvalue())
            self.assertIn("summary written to", stdout.getvalue())
            self.assertEqual(
                (workspace / "AGENTS.md").read_text(encoding="utf-8"), "summary\n"
            )

    def test_interactive_summary_failure_keeps_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["/summary", "after failure", "/exit"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(
                    stub,
                    "write_summary",
                    side_effect=ProviderError("provider is down"),
                ),
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.prompts, ["after failure"])
            self.assertIn("summary failed: provider is down", stderr.getvalue())

    def test_interactive_prints_token_usage_after_each_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=False,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["first", "second", "/exit"])
            stderr = io.StringIO()
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                redirect_stdout(io.StringIO()),
                redirect_stderr(stderr),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue().count("[usage]"), 2)
            self.assertIn("9 total", stderr.getvalue())

    def test_read_message_joins_backslash_continuations(self) -> None:
        answers = iter(["first line \\", "second line"])
        prompts: list[str] = []

        def ask(prompt: str = "") -> str:
            prompts.append(prompt)
            return next(answers)

        with patch("builtins.input", ask):
            text = _read_message()

        self.assertEqual(text, "first line \nsecond line")
        self.assertEqual(prompts, ["> ", "... "])

    def test_read_message_keeps_an_escaped_trailing_backslash(self) -> None:
        with patch("builtins.input", lambda prompt="": "path C:\\\\"):
            self.assertEqual(_read_message(), "path C:\\")

    def test_read_message_unwraps_a_bracketed_paste(self) -> None:
        answers = iter(["\x1b[200~def f():"])
        rest = io.StringIO("\n    return 1\x1b[201~")
        with (
            patch("builtins.input", lambda prompt="": next(answers)),
            patch("sys.stdin", rest),
        ):
            text = _read_message()

        self.assertEqual(text, "def f():\n    return 1")

    def test_read_message_keeps_text_typed_before_a_paste(self) -> None:
        pasted = "run \x1b[200~pytest -q\x1b[201~"
        with patch("builtins.input", lambda prompt="": pasted):
            self.assertEqual(_read_message(), "run pytest -q")

    def test_read_message_drains_a_paste_without_bracket_markers(self) -> None:
        answers = iter(["line one", "line two", "line three"])
        with (
            patch("builtins.input", lambda prompt="": next(answers)),
            patch("minimalcode.cli._input_pending", side_effect=[True, True, False]),
        ):
            text = _read_message()

        self.assertEqual(text, "line one\nline two\nline three")

    def test_interactive_forwards_a_multiline_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)
            stub = StubAgent(workspace)
            args = argparse.Namespace(
                no_session=True,
                max_turns=None,
                quiet=True,
                json_output=False,
                no_highlight=False,
            )
            answers = iter(["first \\", "second", "/exit"])
            with (
                patch("builtins.input", lambda prompt="": next(answers)),
                patch("minimalcode.cli._input_pending", return_value=False),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                code = _interactive(stub, args, settings, store, "fake", "model")

            self.assertEqual(code, 0)
            self.assertEqual(stub.prompts, ["first \nsecond"])


if __name__ == "__main__":
    unittest.main()
