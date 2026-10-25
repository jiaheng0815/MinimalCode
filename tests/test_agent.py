from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.agent import Agent
from minimalcode.config import load_settings
from minimalcode.highlight import strip_ansi
from minimalcode.prompt import build_system_prompt
from minimalcode.provider import ProviderReply, ToolCall
from minimalcode.session import SessionStore
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime


class FakeProvider:
    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="write",
                        arguments=json.dumps({"path": "result.txt", "content": "ok\n"}),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            ProviderReply(
                content="done",
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            ),
        ]

    def stream_chat(self, messages, *, tools=None, on_text=None):
        reply = self.responses.pop(0)
        if reply.content and on_text:
            on_text(reply.content)
        return reply

    def complete_text(self, messages):
        return "summary"


class MarkdownProvider(FakeProvider):
    text = "# Result\n\n**done**\n"

    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000

    def stream_chat(self, messages, *, tools=None, on_text=None):
        if on_text:
            on_text(self.text[:8])
            on_text(self.text[8:])
        return ProviderReply(
            content=self.text,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class ShellProvider(FakeProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_shell",
                        name="shell",
                        arguments=json.dumps({"command": "show output"}),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            ),
            ProviderReply(
                content="done",
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
            ),
        ]


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class BadArgumentsProvider(ShellProvider):
    """Ask for a tool with a malformed optional argument."""

    def __init__(self) -> None:
        super().__init__()
        self.responses[0].tool_calls = [
            ToolCall(
                id="call_bad",
                name="grep",
                arguments=json.dumps({"pattern": "x", "glob": 5}),
            )
        ]


class TruncatedToolProvider(FakeProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_partial",
                        name="write",
                        arguments='{"path":"partial.txt","content":"',
                    )
                ],
                finish_reason="length",
            )
        ]


class EmptyReplyProvider(FakeProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(content="", tool_calls=[], finish_reason="stop", usage={}),
        ]


class FilteredReplyProvider(FakeProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(
                content="partial answer",
                tool_calls=[],
                finish_reason="content_filter",
                usage={},
            ),
        ]


class AgentTests(unittest.TestCase):
    def test_tool_loop_writes_file_and_records_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(home)
            shell = detect_shell()
            store = SessionStore(settings)
            session = store.create(
                workspace,
                provider="fake",
                model="fake-model",
                save=True,
            )
            provider = FakeProvider()
            agent = Agent(
                settings=settings,
                provider=provider,
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            result = agent.run("write result.txt")
            self.assertEqual(agent.max_turns, 1000)
            self.assertTrue(result.completed)
            self.assertEqual(result.turns, 2)
            self.assertEqual(
                (workspace / "result.txt").read_text(encoding="utf-8"), "ok\n"
            )
            self.assertEqual(session.messages[0]["role"], "user")
            self.assertEqual(session.messages[1]["role"], "assistant")
            self.assertEqual(session.messages[2]["role"], "tool")
            self.assertEqual(session.messages[3]["role"], "assistant")

    def test_malformed_tool_argument_does_not_escape_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            agent = Agent(
                settings=settings,
                provider=BadArgumentsProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            result = agent.run("search")

            self.assertTrue(result.completed)
            tool_message = session.messages[2]
            self.assertEqual(tool_message["role"], "tool")
            self.assertIn("invalid arguments", tool_message["content"])

    def test_truncated_tool_call_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            agent = Agent(
                settings=settings,
                provider=TruncatedToolProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            result = agent.run("write it")

            self.assertFalse(result.completed)
            self.assertFalse((workspace / "partial.txt").exists())
            self.assertEqual(session.messages[-1]["role"], "tool")
            self.assertIn("not executed", session.messages[-1]["content"])

    def test_repair_keeps_tool_results_in_call_order(self) -> None:
        from minimalcode.agent import _repair_tool_history

        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "a", "function": {"name": "read"}},
                    {"id": "b", "function": {"name": "read"}},
                ],
            },
            {"role": "tool", "tool_call_id": "b", "name": "read", "content": "b"},
        ]
        repaired, changed = _repair_tool_history(messages)
        self.assertTrue(changed)
        ids = [
            message["tool_call_id"] for message in repaired if message["role"] == "tool"
        ]
        self.assertEqual(ids, ["a", "b"])

        repaired, changed = _repair_tool_history(
            [
                messages[0],
                {"role": "tool", "tool_call_id": "a", "name": "read", "content": "a"},
            ]
        )
        self.assertTrue(changed)
        ids = [
            message["tool_call_id"] for message in repaired if message["role"] == "tool"
        ]
        self.assertEqual(ids, ["a", "b"])

    def test_repair_recovers_interrupted_tool_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            store = SessionStore(settings)
            session = store.create(
                workspace,
                provider="fake",
                model="fake-model",
                save=True,
            )
            session.append_message({"role": "user", "content": "write it"})
            session.append_message(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_lost",
                            "type": "function",
                            "function": {"name": "write", "arguments": "{}"},
                        }
                    ],
                }
            )

            Agent(
                settings=settings,
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            self.assertEqual(session.messages[-1]["role"], "tool")
            self.assertEqual(session.messages[-1]["tool_call_id"], "call_lost")
            self.assertIn("interrupted", session.messages[-1]["content"])
            reloaded = store.load(session.path)
            self.assertEqual(reloaded.messages[-1]["role"], "tool")
            self.assertIn("interrupted", reloaded.messages[-1]["content"])

    def test_streamed_markdown_is_highlighted_on_a_tty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            output = TtyBuffer()
            with patch("minimalcode.highlight._supports_color", return_value=True):
                agent = Agent(
                    settings=settings,
                    provider=MarkdownProvider(),
                    session=session,
                    tool_runtime=ToolRuntime(settings, workspace, shell),
                    shell=shell,
                    output=output,
                    error_output=io.StringIO(),
                    quiet=True,
                )
                agent.run("show markdown")

            rendered = output.getvalue()
            self.assertIn("\x1b[", rendered)
            self.assertEqual(strip_ansi(rendered), "Result\n\ndone\n")

    def test_system_prompt_is_built_once_per_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            with patch(
                "minimalcode.agent.build_system_prompt",
                wraps=build_system_prompt,
            ) as build:
                agent = Agent(
                    settings=settings,
                    provider=FakeProvider(),
                    session=session,
                    tool_runtime=ToolRuntime(settings, workspace, shell),
                    shell=shell,
                    quiet=True,
                    stream_output=False,
                )
                agent.run("write result.txt")

            self.assertEqual(build.call_count, 1)

    def test_empty_completion_is_reported_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            errors = io.StringIO()
            agent = Agent(
                settings=settings,
                provider=EmptyReplyProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                output=io.StringIO(),
                error_output=errors,
                stream_output=False,
            )

            result = agent.run("say something")

            self.assertFalse(result.completed)
            self.assertEqual(result.text, "")
            self.assertIn("empty response", errors.getvalue())

    def test_finish_reason_is_reported_for_a_stopped_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            errors = io.StringIO()
            agent = Agent(
                settings=settings,
                provider=FilteredReplyProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                output=io.StringIO(),
                error_output=errors,
                stream_output=False,
            )

            result = agent.run("say something")

            self.assertFalse(result.completed)
            self.assertEqual(result.text, "partial answer")
            self.assertIn("partial answer", errors.getvalue())
            self.assertIn("content_filter", errors.getvalue())

    def test_shell_output_is_shown_in_tool_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            runtime = ToolRuntime(settings, workspace, shell)
            activity = TtyBuffer()
            agent = Agent(
                settings=settings,
                provider=ShellProvider(),
                session=session,
                tool_runtime=runtime,
                shell=shell,
                output=io.StringIO(),
                error_output=activity,
                stream_output=False,
            )
            result = {
                "status": "success",
                "shell": shell.label,
                "exit_code": 0,
                "stdout": "first line\nsecond line",
                "stderr": "warning text",
                "truncated": False,
            }

            with (
                patch.object(runtime, "dispatch", return_value=result),
                patch("minimalcode.highlight._supports_color", return_value=True),
            ):
                completed = agent.run("run shell")

            self.assertTrue(completed.completed)
            rendered = activity.getvalue()
            self.assertIn("\x1b[38;5;114m[shell]\x1b[0m show output", rendered)
            plain = strip_ansi(rendered)
            self.assertIn("exit 0", plain)
            self.assertIn("stdout:\nfirst line\nsecond line", plain)
            self.assertIn("stderr:\nwarning text", plain)
            self.assertEqual(session.messages[-2]["role"], "tool")

    def test_long_shell_output_is_clipped_in_tool_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            runtime = ToolRuntime(settings, workspace, shell)
            activity = io.StringIO()
            agent = Agent(
                settings=settings,
                provider=ShellProvider(),
                session=session,
                tool_runtime=runtime,
                shell=shell,
                output=io.StringIO(),
                error_output=activity,
                stream_output=False,
            )
            stdout = "\n".join(f"line-{number}" for number in range(1, 401))
            result = {
                "status": "success",
                "shell": shell.label,
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
                "truncated": False,
            }

            with patch.object(runtime, "dispatch", return_value=result):
                agent.run("run shell")

            rendered = activity.getvalue()
            self.assertIn("line-1\n", rendered)
            self.assertIn("line-400", rendered)
            self.assertNotIn("line-200\n", rendered)
            self.assertIn("lines omitted", rendered)
            self.assertLess(len(rendered), len(stdout))


if __name__ == "__main__":
    unittest.main()
