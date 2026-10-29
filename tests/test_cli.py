from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from minimalcode.cli import _interactive, run
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


class StubAgent:
    """Minimal stand-in for the interactive loop's agent contract."""

    def __init__(self, cwd: Path, *, failures: int = 0) -> None:
        self.shell = SimpleNamespace(label="stub-shell")
        self.session = SimpleNamespace(cwd=cwd, path=None)
        self.provider = SimpleNamespace(
            name="fake", model="fake-model", thinking="high"
        )
        self.tools = SimpleNamespace(
            web_client=None, vision_client=None, thinking="high"
        )
        self.prompts: list[str] = []
        self._failures = failures

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


if __name__ == "__main__":
    unittest.main()
