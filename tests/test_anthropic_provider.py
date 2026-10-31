from __future__ import annotations

import json
import unittest

import httpx2

from minimalcode.provider import OpenAICompatibleProvider, ProviderError
from minimalcode.provider.anthropic import AnthropicProtocolMixin


def _sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode("utf-8")


def _anthropic_provider(
    handler,
    *,
    base_url: str = "https://api.deepseek.com/anthropic",
    **config,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test-anthropic",
        config={
            "protocol": "anthropic_messages",
            "base_url": base_url,
            "api_key": "test-key",
            "stream": False,
            **config,
        },
        model="deepseek-flash",
        thinking="high",
        transport=httpx2.MockTransport(handler),
    )


class AnthropicProviderTests(unittest.TestCase):
    def test_payload_conversion_headers_and_non_stream_reply(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen["path"] = request.url.path
            seen["authorization"] = request.headers.get("authorization")
            seen["x_api_key"] = request.headers.get("x-api-key")
            seen["version"] = request.headers.get("anthropic-version")
            seen["body"] = json.loads(request.content)
            return httpx2.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "deliberating",
                            "signature": "sig-1",
                        },
                        {"type": "text", "text": "done"},
                        {
                            "type": "tool_use",
                            "id": "toolu_9",
                            "name": "read",
                            "input": {"path": "b.py"},
                        },
                    ],
                    "stop_reason": "tool_use",
                    "usage": {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 3,
                        "output_tokens": 4,
                    },
                },
            )

        provider = _anthropic_provider(
            handler,
            send_reasoning_effort=True,
            max_tokens=8192,
        )
        text: list[str] = []
        thinking: list[str] = []
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": "checking",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path":"a.py"}',
                        },
                    },
                    {
                        "id": "toolu_2",
                        "type": "function",
                        "function": {
                            "name": "grep",
                            "arguments": '{"pattern":"x"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_1",
                "name": "read",
                "content": '{"status":"success"}',
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_2",
                "name": "grep",
                "content": '{"status":"error","message":"missing"}',
            },
        ]
        try:
            reply = provider.stream_chat(
                messages,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read",
                            "description": "Read a file",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                            },
                        },
                    }
                ],
                on_text=text.append,
                on_thinking=thinking.append,
            )
        finally:
            provider.close()

        self.assertEqual(seen["path"], "/anthropic/v1/messages")
        self.assertIsNone(seen["authorization"])
        self.assertEqual(seen["x_api_key"], "test-key")
        self.assertEqual(seen["version"], "2023-06-01")
        body = seen["body"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 8192)
        self.assertFalse(body["stream"])
        self.assertEqual(body["system"], "system prompt")
        self.assertEqual(body["thinking"], {"type": "enabled", "budget_tokens": 4096})
        self.assertEqual(body["tools"][0]["name"], "read")
        self.assertEqual(body["tools"][0]["description"], "Read a file")
        self.assertNotIn("type", body["tools"][0])
        self.assertIn("input_schema", body["tools"][0])

        request_messages = body["messages"]
        self.assertEqual(
            [item["role"] for item in request_messages],
            [
                "user",
                "assistant",
                "user",
            ],
        )
        self.assertEqual(
            request_messages[1]["content"][0],
            {
                "type": "text",
                "text": "checking",
            },
        )
        self.assertEqual(request_messages[1]["content"][1]["type"], "tool_use")
        self.assertEqual(request_messages[1]["content"][1]["input"], {"path": "a.py"})
        tool_results = request_messages[2]["content"]
        self.assertEqual(
            [block["type"] for block in tool_results],
            [
                "tool_result",
                "tool_result",
            ],
        )
        self.assertEqual(tool_results[0]["tool_use_id"], "toolu_1")
        self.assertNotIn("is_error", tool_results[0])
        self.assertTrue(tool_results[1]["is_error"])

        self.assertEqual(thinking, ["deliberating"])
        self.assertEqual(text, ["done"])
        self.assertEqual(reply.content, "done")
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual(reply.tool_calls[0].id, "toolu_9")
        self.assertEqual(reply.tool_calls[0].name, "read")
        self.assertEqual(reply.tool_calls[0].arguments, '{"path":"b.py"}')
        self.assertEqual(reply.usage["prompt_tokens"], 15)
        self.assertEqual(reply.usage["completion_tokens"], 4)
        self.assertEqual(reply.usage["total_tokens"], 19)
        self.assertEqual(reply.usage["cached_tokens"], 3)
        self.assertEqual(
            reply.provider_content,
            [
                {
                    "type": "thinking",
                    "thinking": "deliberating",
                    "signature": "sig-1",
                },
                {"type": "text", "text": "done"},
                {
                    "type": "tool_use",
                    "id": "toolu_9",
                    "name": "read",
                    "input": {"path": "b.py"},
                },
            ],
        )

    def test_redacted_thinking_is_replayed_verbatim(self) -> None:
        message = {
            "role": "assistant",
            "content": "",
            "provider_content": [
                {"type": "redacted_thinking", "data": "opaque-data"},
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read",
                    "input": {"path": "a.py"},
                },
            ],
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                }
            ],
        }
        blocks = AnthropicProtocolMixin._anthropic_assistant_blocks(message)
        self.assertEqual(
            [block["type"] for block in blocks],
            ["redacted_thinking", "thinking", "tool_use"],
        )
        self.assertEqual(blocks[0]["data"], "opaque-data")

    def test_redacted_thinking_in_a_non_stream_reply_is_preserved(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "redacted_thinking", "data": "opaque-data"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read",
                            "input": {"path": "a.py"},
                        },
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            )

        provider = _anthropic_provider(handler)
        try:
            reply = provider.stream_chat([{"role": "user", "content": "go"}])
        finally:
            provider.close()

        # Reading the block used to rebind the response body, so the stop-reason
        # and usage lookups below raised AttributeError.
        self.assertEqual(reply.content, "")
        self.assertEqual(
            [block["type"] for block in reply.provider_content],
            ["redacted_thinking", "tool_use"],
        )
        self.assertEqual(reply.provider_content[0]["data"], "opaque-data")
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual([call.name for call in reply.tool_calls], ["read"])
        self.assertEqual(reply.usage["prompt_tokens"], 3)

    def test_non_string_history_role_and_block_type_are_skipped(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                },
            )

        provider = _anthropic_provider(handler)
        try:
            reply = provider.stream_chat(
                [
                    {"role": {"nested": "role"}, "content": "dropped"},
                    {
                        "role": "assistant",
                        "content": "",
                        "provider_content": [
                            {"type": ["text"], "text": "dropped"},
                            {"type": {"nested": "type"}, "text": "dropped"},
                            {"type": "text", "text": "kept"},
                        ],
                    },
                    {"role": "user", "content": "hi"},
                ]
            )
        finally:
            provider.close()

        # A hand-edited session file can carry a non-string role or block type;
        # membership tests on those raised TypeError instead of skipping them.
        self.assertEqual(reply.content, "ok")
        messages = seen["messages"]
        self.assertEqual(
            [message["role"] for message in messages], ["assistant", "user"]
        )
        self.assertEqual(messages[0]["content"], [{"type": "text", "text": "kept"}])

    def test_streaming_tool_call_and_thinking_replay(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            requests.append(body)
            has_tool_result = any(
                block.get("type") == "tool_result"
                for message in body["messages"]
                for block in message["content"]
            )
            if has_tool_result:
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_3",
                            "role": "assistant",
                            "usage": {"input_tokens": 20, "output_tokens": 1},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "finished"},
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 2},
                    },
                    {"type": "message_stop"},
                ]
            else:
                events = [
                    {"type": "ping"},
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_2",
                            "role": "assistant",
                            "usage": {
                                "input_tokens": 5,
                                "cache_read_input_tokens": 2,
                                "output_tokens": 1,
                            },
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "thinking", "thinking": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "thinking_delta", "thinking": "plan"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "signature_delta",
                            "signature": "sig-1",
                        },
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": "hel"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": "lo"},
                    },
                    {"type": "content_block_stop", "index": 1},
                    {
                        "type": "content_block_start",
                        "index": 2,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read",
                            "input": {},
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 2,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"path":',
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 2,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '"a.py"}',
                        },
                    },
                    {"type": "content_block_stop", "index": 2},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 7},
                    },
                    {"type": "message_stop"},
                ]
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(events),
            )

        provider = _anthropic_provider(handler, stream=True)
        text: list[str] = []
        thinking: list[str] = []
        try:
            reply = provider.stream_chat(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "inspect"},
                ],
                on_text=text.append,
                on_thinking=thinking.append,
            )
            self.assertEqual(text, ["hel", "lo"])
            self.assertEqual(thinking, ["plan"])
            self.assertEqual(reply.content, "hello")
            self.assertEqual(reply.finish_reason, "tool_calls")
            self.assertEqual(reply.tool_calls[0].id, "toolu_1")
            self.assertEqual(reply.tool_calls[0].arguments, '{"path":"a.py"}')
            self.assertEqual(reply.usage["prompt_tokens"], 7)
            self.assertEqual(reply.usage["completion_tokens"], 7)
            self.assertEqual(reply.usage["total_tokens"], 14)

            assistant = reply.as_message()
            self.assertIn("provider_content", assistant)
            follow_up = provider.stream_chat(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "inspect"},
                    assistant,
                    {
                        "role": "tool",
                        "tool_call_id": "toolu_1",
                        "name": "read",
                        "content": '{"status":"success"}',
                    },
                ]
            )
        finally:
            provider.close()

        self.assertEqual(follow_up.content, "finished")
        self.assertEqual(follow_up.finish_reason, "stop")
        self.assertEqual(len(requests), 2)
        replayed = requests[1]
        self.assertEqual(replayed["system"], "sys")
        replay_messages = replayed["messages"]
        self.assertEqual(
            [item["role"] for item in replay_messages],
            [
                "user",
                "assistant",
                "user",
            ],
        )
        assistant_blocks = replay_messages[1]["content"]
        self.assertEqual(
            [block["type"] for block in assistant_blocks],
            ["thinking", "text", "tool_use"],
        )
        self.assertEqual(assistant_blocks[0]["signature"], "sig-1")
        self.assertEqual(assistant_blocks[2]["input"], {"path": "a.py"})
        self.assertEqual(replay_messages[2]["content"][0]["tool_use_id"], "toolu_1")

    def test_stream_without_message_stop_is_incomplete(self) -> None:
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 2, "output_tokens": 1}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "half"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
        ]

        provider = _anthropic_provider(
            lambda request: httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(events),
            ),
            stream=True,
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "half")
        self.assertEqual(reply.finish_reason, "incomplete")

    def test_stream_falls_back_to_a_json_response(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "plain"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            )

        provider = _anthropic_provider(handler, stream=True)
        text: list[str] = []
        try:
            reply = provider.stream_chat(
                [{"role": "user", "content": "hello"}],
                on_text=text.append,
            )
        finally:
            provider.close()

        self.assertEqual(text, ["plain"])
        self.assertEqual(reply.content, "plain")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage["total_tokens"], 3)
        self.assertIsNone(reply.provider_content)

    def test_non_stream_accepts_an_sse_response(self) -> None:
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 2, "output_tokens": 1}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "streamed"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 2},
            },
            {"type": "message_stop"},
        ]

        provider = _anthropic_provider(
            lambda request: httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse(events),
            ),
            stream=False,
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "streamed")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage["completion_tokens"], 2)

    def test_partial_provider_content_falls_back_to_tool_calls(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                },
            )

        provider = _anthropic_provider(handler, max_tokens=2048)
        try:
            provider.stream_chat(
                [
                    {"role": "user", "content": "inspect"},
                    {
                        "role": "assistant",
                        "content": "checking",
                        # The preserved list lost one of the calls, so replaying
                        # it verbatim would orphan the second tool_result.
                        "provider_content": [{"type": "text", "text": "checking"}],
                        "tool_calls": [
                            {
                                "id": "toolu_1",
                                "type": "function",
                                "function": {"name": "read", "arguments": "{}"},
                            },
                            {
                                "id": "toolu_2",
                                "type": "function",
                                "function": {"name": "read", "arguments": "{}"},
                            },
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "toolu_1",
                        "name": "read",
                        "content": '{"status":"success"}',
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "toolu_2",
                        "name": "read",
                        "content": '{"status":"success"}',
                    },
                ]
            )
        finally:
            provider.close()

        assistant_blocks = seen["messages"][1]["content"]
        self.assertEqual(
            [block["type"] for block in assistant_blocks],
            ["text", "tool_use", "tool_use"],
        )
        self.assertEqual(
            [block["id"] for block in assistant_blocks[1:]],
            ["toolu_1", "toolu_2"],
        )
        self.assertEqual(
            [block["tool_use_id"] for block in seen["messages"][2]["content"]],
            ["toolu_1", "toolu_2"],
        )

    def test_image_parts_become_anthropic_image_sources(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                },
            )

        provider = _anthropic_provider(handler)
        try:
            provider.stream_chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,cG5n"},
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.test/a.png"},
                            },
                            {"type": "unknown", "text": "fallback"},
                        ],
                    }
                ]
            )
        finally:
            provider.close()

        blocks = seen["messages"][0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "look"})
        self.assertEqual(
            blocks[1],
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "cG5n",
                },
            },
        )
        self.assertEqual(
            blocks[2],
            {
                "type": "image",
                "source": {"type": "url", "url": "https://example.test/a.png"},
            },
        )
        self.assertEqual(blocks[3], {"type": "text", "text": "fallback"})

    def test_thinking_none_disables_thinking(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                },
            )

        provider = _anthropic_provider(handler, send_reasoning_effort=True)
        provider.thinking = "none"
        try:
            provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(seen["thinking"], {"type": "disabled"})

    def test_stream_error_event_is_reported(self) -> None:
        body = _sse(
            [
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "busy"},
                }
            ]
        )
        provider = _anthropic_provider(
            lambda request: httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body,
            ),
            stream=True,
        )
        try:
            with self.assertRaisesRegex(ProviderError, "busy"):
                provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

    def test_endpoint_accepts_versioned_and_full_messages_urls(self) -> None:
        cases = {
            "https://api.deepseek.com/anthropic": (
                "https://api.deepseek.com/anthropic/v1/messages"
            ),
            "https://api.anthropic.com/v1": "https://api.anthropic.com/v1/messages",
            "https://example.test/v1/messages": ("https://example.test/v1/messages"),
        }
        for base_url, expected in cases.items():
            with self.subTest(base_url=base_url):
                provider = _anthropic_provider(
                    lambda request: httpx2.Response(200, json={}),
                    base_url=base_url,
                )
                try:
                    self.assertEqual(provider._endpoint(), expected)
                finally:
                    provider.close()

    def test_thinking_requires_room_for_a_budget(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("request must not be sent")

        provider = _anthropic_provider(
            handler,
            send_reasoning_effort=True,
            max_tokens=1024,
        )
        try:
            with self.assertRaisesRegex(ProviderError, "max_tokens"):
                provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

    def test_chat_payload_strips_provider_specific_history(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
                },
            )

        provider = OpenAICompatibleProvider(
            name="chat",
            config={
                "base_url": "https://provider.test/v1",
                "stream": False,
            },
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            reply = provider.stream_chat(
                [
                    {"role": "user", "content": "inspect"},
                    {
                        "role": "assistant",
                        "content": "previous",
                        "provider_content": [
                            {"type": "thinking", "thinking": "from Anthropic"}
                        ],
                    },
                    {"role": "user", "content": "continue"},
                ]
            )
        finally:
            provider.close()

        self.assertEqual(reply.content, "ok")
        for message in seen["messages"]:
            self.assertNotIn("provider_content", message)


if __name__ == "__main__":
    unittest.main()
