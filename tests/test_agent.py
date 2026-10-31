from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.agent import Agent, estimate_text_tokens, estimate_tokens
from minimalcode.config import load_settings
from minimalcode.highlight import strip_ansi
from minimalcode.prompt import SUMMARY_START, build_system_prompt
from minimalcode.provider import ProviderError, ProviderReply, ToolCall
from minimalcode.session import SessionStore
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolError, ToolRuntime


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

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        reply = self.responses.pop(0)
        if reply.content and on_text:
            on_text(reply.content)
        return reply

    def complete_text(self, messages):
        return "summary"


class BurstToolProvider:
    name = "fake"
    model = "fake-model"
    context_window = 100_000

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        return ProviderReply(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"call_{index}",
                    name="write",
                    arguments=json.dumps(
                        {"path": f"burst-{index}.txt", "content": "nope\n"}
                    ),
                )
                for index in range(40)
            ],
            finish_reason="tool_calls",
            usage={},
        )


class MarkdownProvider(FakeProvider):
    text = "# Result\n\n**done**\n"

    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        if on_text:
            on_text(self.text[:8])
            on_text(self.text[8:])
        return ProviderReply(
            content=self.text,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class ThinkingProvider(MarkdownProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        if on_thinking:
            on_thinking("checking files")
        if on_text:
            on_text("done")
        return ProviderReply(
            content="done",
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


class TwoTurnProvider(FakeProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.responses = [
            ProviderReply(
                content="first",
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            ProviderReply(
                content="second",
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            ),
        ]


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class RecordingProvider(FakeProvider):
    """Return a plain stop reply and keep every request it received."""

    def __init__(self) -> None:
        self.name = "fake"
        self.model = "fake-model"
        self.context_window = 100_000
        self.requests: list[list[dict]] = []

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        self.requests.append([dict(message) for message in messages])
        if on_text:
            on_text("ok")
        return ProviderReply(
            content="ok",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        )


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
    def test_a_burst_of_tool_calls_is_refused_without_executing_any(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(home)
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace, provider="fake", model="fake-model", save=False
            )
            agent = Agent(
                settings=settings,
                provider=BurstToolProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            result = agent.run("write everything")
        self.assertFalse(result.completed)
        self.assertEqual(list(workspace.glob("burst-*.txt")), [])
        tool_messages = [
            message for message in session.messages if message["role"] == "tool"
        ]
        self.assertEqual(len(tool_messages), 40)
        self.assertIn("limit is", tool_messages[0]["content"])

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

    def test_switch_runtime_and_new_session_reset_model_state(self) -> None:
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
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            agent.last_prompt_tokens = 123

            provider = FakeProvider()
            provider.name = "other"
            provider.model = "other-model"
            provider.thinking = "low"
            provider.context_window = 4096
            runtime = ToolRuntime(
                settings,
                workspace,
                shell,
                provider_name="other",
                model="other-model",
                thinking="low",
            )
            agent.switch_runtime(provider, runtime)

            self.assertIs(agent.provider, provider)
            self.assertIs(agent.tools, runtime)
            self.assertEqual(agent.last_prompt_tokens, 0)
            self.assertEqual(agent.status()["model"], "other-model")

            agent.total_usage["total_tokens"] = 9
            agent.last_prompt_tokens = 321
            agent.estimated_turns = 2
            agent._system_prompt = "cached"
            next_session = SessionStore(settings).create(
                workspace,
                provider="other",
                model="other-model",
                save=False,
            )
            agent.new_session(next_session, runtime)

            self.assertIs(agent.session, next_session)
            self.assertEqual(agent.total_usage["total_tokens"], 0)
            self.assertEqual(agent.last_prompt_tokens, 0)
            self.assertEqual(agent.estimated_turns, 0)
            self.assertIsNone(agent._system_prompt)

    def test_run_usage_is_per_turn_while_usage_accumulates(self) -> None:
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
                provider=TwoTurnProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            first = agent.run("one")
            second = agent.run("two")

            self.assertEqual(first.run_usage["total_tokens"], 15)
            self.assertEqual(second.run_usage["total_tokens"], 22)
            self.assertEqual(second.usage["total_tokens"], 37)
            self.assertFalse(first.usage_estimated)
            self.assertFalse(second.usage_estimated)

    def test_missing_usage_is_estimated_and_flagged(self) -> None:
        class NoUsageProvider(FakeProvider):
            def __init__(self) -> None:
                self.name = "fake"
                self.model = "fake-model"
                self.thinking = "high"
                self.context_window = 100_000
                self.responses = [
                    ProviderReply(
                        content="一个中文的回答",
                        tool_calls=[],
                        finish_reason="stop",
                        usage={},
                    )
                ]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace, provider="fake", model="fake-model", save=False
            )
            agent = Agent(
                settings=settings,
                provider=NoUsageProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            result = agent.run("answer")

            self.assertTrue(result.completed)
            self.assertTrue(result.usage_estimated)
            self.assertGreater(result.run_usage["completion_tokens"], 0)
            self.assertEqual(
                result.run_usage["total_tokens"],
                result.run_usage["prompt_tokens"]
                + result.run_usage["completion_tokens"],
            )
            self.assertTrue(agent.status()["usage_estimated"])

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

    def test_repair_drops_orphan_and_duplicate_tool_results(self) -> None:
        from minimalcode.agent import _repair_tool_history

        messages = [
            {"role": "tool", "tool_call_id": "ghost", "name": "read", "content": "x"},
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "a", "function": {"name": "read"}}],
            },
            {"role": "tool", "tool_call_id": "a", "name": "read", "content": "1"},
            {"role": "tool", "tool_call_id": "a", "name": "read", "content": "2"},
            {"role": "tool", "tool_call_id": "unknown", "name": "read", "content": "3"},
        ]
        repaired, changed = _repair_tool_history(messages)
        self.assertTrue(changed)
        self.assertEqual(
            [item["role"] for item in repaired], ["user", "assistant", "tool"]
        )
        self.assertEqual(repaired[-1]["content"], "1")

    def test_unexpected_tool_failure_becomes_a_tool_result(self) -> None:
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
            agent = Agent(
                settings=settings,
                provider=FakeProvider(),
                session=session,
                tool_runtime=runtime,
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            with patch.object(runtime, "dispatch", side_effect=RuntimeError("boom")):
                result = agent.run("write result.txt")

            self.assertTrue(result.completed)
            tool_message = session.messages[2]
            self.assertEqual(tool_message["role"], "tool")
            self.assertIn("boom", tool_message["content"])

    def test_compaction_finds_a_boundary_without_a_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            settings.data["context"]["keep_messages"] = 4
            shell = detect_shell()
            session = SessionStore(settings).create(
                workspace,
                provider="fake",
                model="fake-model",
                save=False,
            )
            for index in range(8):
                session.append_message(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": f"c{index}", "function": {"name": "read"}}
                        ],
                    }
                )
                session.append_message(
                    {
                        "role": "tool",
                        "tool_call_id": f"c{index}",
                        "name": "read",
                        "content": "r",
                    }
                )
            agent = Agent(
                settings=settings,
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            self.assertTrue(agent._compact(force=False))
            self.assertEqual(session.messages[0]["role"], "user")
            self.assertNotEqual(session.messages[2]["role"], "tool")

    def test_should_compact_prefers_reported_prompt_tokens(self) -> None:
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
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            agent.last_prompt_tokens = 90_000
            with patch("minimalcode.agent.estimate_tokens") as estimate:
                self.assertTrue(agent.should_compact())
            estimate.assert_not_called()

            agent.last_prompt_tokens = 0
            with patch("minimalcode.agent.estimate_tokens", return_value=1) as estimate:
                self.assertFalse(agent.should_compact())
            estimate.assert_called_once()

    def test_write_summary_creates_the_workspace_agents_file(self) -> None:
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
            session.append_message({"role": "user", "content": "add a test"})
            agent = Agent(
                settings=settings,
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            target = agent.write_summary()

            self.assertEqual(target, (workspace / "AGENTS.md").resolve())
            text = target.read_text(encoding="utf-8")
            self.assertIn("summary", text)
            self.assertNotIn("add a test", text)

    def test_write_summary_replaces_a_previous_block_and_keeps_prose(self) -> None:
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
            session.append_message({"role": "user", "content": "add a test"})
            agent = Agent(
                settings=settings,
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            existing = "# Notes\n\nkeep me\n"
            (workspace / "AGENTS.md").write_text(existing, encoding="utf-8")

            target = agent.write_summary()
            with patch.object(
                agent.provider, "complete_text", return_value="second summary"
            ):
                agent.write_summary()

            text = target.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(existing))
            self.assertEqual(text.count("second summary"), 1)
            # The block written by the first call must be gone, not duplicated.
            self.assertNotIn(f"{SUMMARY_START}\nsummary\n", text)

    def test_write_summary_refuses_an_empty_provider_reply(self) -> None:
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
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            with (
                patch.object(agent.provider, "complete_text", return_value="  "),
                self.assertRaises(ProviderError),
            ):
                agent.write_summary()

            self.assertFalse((workspace / "AGENTS.md").exists())

    def test_write_summary_refuses_an_unreadable_agents_file(self) -> None:
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
                provider=FakeProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )
            target = workspace / "AGENTS.md"
            target.write_bytes(b"# Notes\n\xff\xfe broken\n")

            with self.assertRaises(ToolError):
                agent.write_summary()

            self.assertEqual(target.read_bytes(), b"# Notes\n\xff\xfe broken\n")

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
            with patch(
                "minimalcode.highlight.color._supports_color", return_value=True
            ):
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

    def test_reasoning_is_rendered_in_the_h6_heading_style(self) -> None:
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
            with patch(
                "minimalcode.highlight.color._supports_color", return_value=True
            ):
                agent = Agent(
                    settings=settings,
                    provider=ThinkingProvider(),
                    session=session,
                    tool_runtime=ToolRuntime(settings, workspace, shell),
                    shell=shell,
                    output=output,
                    error_output=io.StringIO(),
                    quiet=True,
                )
                agent.run("look around")

            rendered = output.getvalue()
            self.assertIn("\x1b[2;38;5;245mchecking files\x1b[0m", rendered)
            self.assertEqual(strip_ansi(rendered), "checking files\ndone")

    def test_reasoning_is_not_written_without_ansi_support(self) -> None:
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
            output = io.StringIO()
            agent = Agent(
                settings=settings,
                provider=ThinkingProvider(),
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                output=output,
                error_output=io.StringIO(),
                quiet=True,
            )
            agent.run("look around")

            self.assertEqual(output.getvalue(), "done")

    def test_instructions_are_re_read_at_the_start_of_each_run(self) -> None:
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
                    provider=TwoTurnProvider(),
                    session=session,
                    tool_runtime=ToolRuntime(settings, workspace, shell),
                    shell=shell,
                    quiet=True,
                    stream_output=False,
                )
                agent.run("first")
                agent.run("second")

            self.assertEqual(build.call_count, 2)

    def test_each_run_starts_with_the_current_agents_and_claude_files(self) -> None:
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
            (workspace / "AGENTS.md").write_text("ALPHA-RULE\n", encoding="utf-8")
            provider = RecordingProvider()
            agent = Agent(
                settings=settings,
                provider=provider,
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            agent.run("first")
            (workspace / "AGENTS.md").write_text("GAMMA-RULE\n", encoding="utf-8")
            (workspace / "CLAUDE.md").write_text("BETA-RULE\n", encoding="utf-8")
            agent.run("second")

            first_system = provider.requests[0][0]
            second_system = provider.requests[1][0]
            self.assertEqual(first_system["role"], "system")
            self.assertIn("ALPHA-RULE", first_system["content"])
            self.assertNotIn("BETA-RULE", first_system["content"])
            self.assertEqual(second_system["role"], "system")
            self.assertIn("GAMMA-RULE", second_system["content"])
            self.assertIn("BETA-RULE", second_system["content"])
            self.assertNotIn("ALPHA-RULE", second_system["content"])

    def test_runs_without_instruction_files_still_start(self) -> None:
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
            provider = RecordingProvider()
            agent = Agent(
                settings=settings,
                provider=provider,
                session=session,
                tool_runtime=ToolRuntime(settings, workspace, shell),
                shell=shell,
                quiet=True,
                stream_output=False,
            )

            result = agent.run("hello")

            self.assertTrue(result.completed)
            system = provider.requests[0][0]["content"]
            self.assertIn("MinimalCode", system)
            self.assertNotIn("Repository instructions", system)

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
                patch("minimalcode.highlight.color._supports_color", return_value=True),
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


class TokenEstimateTests(unittest.TestCase):
    def test_cjk_text_counts_more_tokens_than_ascii_of_equal_length(self) -> None:
        cjk = "中" * 400
        ascii_text = "a" * 400
        self.assertGreater(
            estimate_text_tokens(cjk), estimate_text_tokens(ascii_text) * 3
        )
        self.assertGreater(
            estimate_tokens([{"role": "user", "content": cjk}]),
            estimate_tokens([{"role": "user", "content": ascii_text}]) * 3,
        )

    def test_estimate_is_never_zero(self) -> None:
        self.assertEqual(estimate_tokens([]), 1)
        self.assertEqual(estimate_text_tokens(""), 0)


if __name__ == "__main__":
    unittest.main()
