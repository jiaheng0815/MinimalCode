"""Reasoning that has to survive into the next request of a tool loop.

DeepSeek's Chat API expects ``reasoning_content`` back on the assistant message
that asked for tools; without it a thinking model repeats or contradicts its own
plan on the follow-up request. A finished answer is the opposite case: the field
is ignored by the next request and rejected by strict servers, so it must stay
out of the log.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

import httpx2

from minimalcode.provider import OpenAICompatibleProvider, ProviderReply, ToolCall


def _chat_provider(handler: Any, *, stream: bool = False) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        config={"base_url": "https://provider.test/v1", "stream": stream},
        model="model-a",
        thinking="high",
        transport=httpx2.MockTransport(handler),
    )


def _tool_call_reply() -> ProviderReply:
    return ProviderReply(
        content="",
        tool_calls=[ToolCall(id="call_1", name="read", arguments='{"path":"a.py"}')],
        finish_reason="tool_calls",
        reasoning="I should read the file first.",
    )


class AsMessageTests(unittest.TestCase):
    def test_a_tool_step_keeps_its_reasoning(self) -> None:
        message = _tool_call_reply().as_message()
        self.assertEqual(message["reasoning_content"], "I should read the file first.")

    def test_a_finished_answer_does_not_keep_it(self) -> None:
        reply = ProviderReply(content="done", reasoning="deliberation")
        self.assertNotIn("reasoning_content", reply.as_message())

    def test_an_empty_reasoning_is_not_written(self) -> None:
        message = ProviderReply(
            tool_calls=[ToolCall(id="call_1", name="read", arguments="{}")]
        ).as_message()
        self.assertNotIn("reasoning_content", message)


class ChatReplayTests(unittest.TestCase):
    def _seen(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
                },
            )

        provider = _chat_provider(handler)
        try:
            provider.stream_chat(messages)
        finally:
            provider.close()
        return seen

    def test_a_tool_step_sends_reasoning_back(self) -> None:
        payload = self._seen(
            [
                {"role": "user", "content": "read a.py"},
                _tool_call_reply().as_message(),
                {"role": "tool", "tool_call_id": "call_1", "content": "print(1)"},
            ]
        )
        assistant = next(
            item for item in payload["messages"] if item["role"] == "assistant"
        )
        self.assertEqual(
            assistant["reasoning_content"], "I should read the file first."
        )

    def test_a_finished_answer_drops_stale_reasoning(self) -> None:
        """A resumed session may carry the field; the request must not."""
        payload = self._seen(
            [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "previous answer",
                    "reasoning_content": "old reasoning",
                },
                {"role": "user", "content": "next"},
            ]
        )
        assistant = next(
            item for item in payload["messages"] if item["role"] == "assistant"
        )
        self.assertNotIn("reasoning_content", assistant)
        self.assertEqual(assistant["content"], "previous answer")


class ChatReasoningCaptureTests(unittest.TestCase):
    def test_a_streamed_tool_step_keeps_the_reasoning_it_streamed(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            events = [
                {"choices": [{"delta": {"reasoning_content": "weigh "}}]},
                {"choices": [{"delta": {"reasoning": "options"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "read", "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            body += "data: [DONE]\n\n"
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body.encode("utf-8"),
            )

        provider = _chat_provider(handler, stream=True)
        thinking: list[str] = []
        try:
            reply = provider.stream_chat(
                [{"role": "user", "content": "hello"}],
                on_thinking=thinking.append,
            )
        finally:
            provider.close()

        self.assertEqual(thinking, ["weigh ", "options"])
        self.assertEqual(reply.reasoning, "weigh options")
        self.assertEqual(reply.as_message()["reasoning_content"], "weigh options")

    def test_a_non_streamed_answer_reports_its_reasoning(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "answer",
                                "reasoning_content": "deliberation",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        provider = _chat_provider(handler)
        try:
            reply = provider.stream_chat(
                [{"role": "user", "content": "hello"}], on_thinking=None
            )
        finally:
            provider.close()

        self.assertEqual(reply.reasoning, "deliberation")
        self.assertNotIn("reasoning_content", reply.as_message())


if __name__ == "__main__":
    unittest.main()
