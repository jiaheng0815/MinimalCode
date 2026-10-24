from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from minimalcode.agent import Agent
from minimalcode.config import load_settings
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
            self.assertTrue(result.completed)
            self.assertEqual(result.turns, 2)
            self.assertEqual((workspace / "result.txt").read_text(encoding="utf-8"), "ok\n")
            self.assertEqual(session.messages[0]["role"], "user")
            self.assertEqual(session.messages[1]["role"], "assistant")
            self.assertEqual(session.messages[2]["role"], "tool")
            self.assertEqual(session.messages[3]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
