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
from minimalcode.provider import ProviderReply
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

    def stream_chat(self, messages, *, tools=None, on_text=None):
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
        self.provider = SimpleNamespace(name="fake", model="fake-model", thinking="high")
        self.tools = SimpleNamespace(web_client=None, vision_client=None)
        self.prompts: list[str] = []
        self._failures = failures

    def run(self, text: str) -> SimpleNamespace:
        self.prompts.append(text)
        if self._failures:
            self._failures -= 1
            raise OSError("simulated failure")
        return SimpleNamespace(text="done\n", completed=True)

    def status(self) -> dict:
        return {}

    def compact(self) -> bool:
        return False


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
            self.assertIn(f"Workspace: {requested.resolve()}", system_message)

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
            self.assertEqual(stub.provider.thinking, "ultra")
            self.assertEqual(stub.prompts, [])
            self.assertIn("| stub-shell | effort high", stdout.getvalue())
            for level in EFFORT_LEVELS:
                self.assertIn(level, stdout.getvalue())
            self.assertIn("effort: high", stdout.getvalue())
            self.assertIn("effort: low", stdout.getvalue())
            self.assertIn("effort: none", stdout.getvalue())
            self.assertIn("effort: ultra", stdout.getvalue())
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


if __name__ == "__main__":
    unittest.main()
