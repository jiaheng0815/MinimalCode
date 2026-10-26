from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx2

from minimalcode.config import EFFORT_LEVELS
from minimalcode.provider import OpenAICompatibleProvider, ProviderError


class ProviderTests(unittest.TestCase):
    def test_stream_tool_calls_and_usage(self) -> None:
        seen_request: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen_request.update(json.loads(request.content))
            self.assertEqual(request.url.path, "/v1/chat/completions")
            events = [
                {"choices": [{"delta": {"content": "checking"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "read",
                                            "arguments": '{"path":',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"README.md"}'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 4,
                        "total_tokens": 16,
                        "prompt_tokens_details": {"cached_tokens": 3},
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            ]
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            body += "data: [DONE]\n\n"
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body.encode("utf-8"),
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={
                "base_url": "https://provider.test/v1",
                "context_window": 1000,
                "include_usage": True,
            },
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        deltas: list[str] = []
        try:
            reply = provider.stream_chat(
                [{"role": "user", "content": "inspect"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                on_text=deltas.append,
            )
        finally:
            provider.close()

        self.assertEqual(deltas, ["checking"])
        self.assertEqual(reply.content, "checking")
        self.assertEqual(reply.usage["prompt_tokens"], 12)
        self.assertEqual(reply.usage["cached_tokens"], 3)
        self.assertEqual(reply.usage["reasoning_tokens"], 1)
        self.assertEqual(reply.tool_calls[0].id, "call_1")
        self.assertEqual(reply.tool_calls[0].name, "read")
        self.assertEqual(reply.tool_calls[0].arguments, '{"path":"README.md"}')
        self.assertEqual(seen_request["model"], "model-a")
        self.assertTrue(seen_request["stream"])

    def test_effort_levels_are_forwarded(self) -> None:
        chat_requests: list[dict[str, object]] = []
        responses_requests: list[dict[str, object]] = []

        def chat_handler(request: httpx2.Request) -> httpx2.Response:
            chat_requests.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
                },
            )

        def responses_handler(request: httpx2.Request) -> httpx2.Response:
            responses_requests.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                },
            )

        chat = OpenAICompatibleProvider(
            name="chat",
            config={
                "base_url": "https://provider.test/v1",
                "stream": False,
                "send_reasoning_effort": True,
            },
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(chat_handler),
        )
        responses = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": False,
                "send_reasoning_effort": True,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(responses_handler),
        )
        try:
            for effort in EFFORT_LEVELS:
                chat.thinking = effort
                responses.thinking = effort
                chat.stream_chat([{"role": "user", "content": "hello"}])
                responses.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            chat.close()
            responses.close()

        for effort, payload in zip(EFFORT_LEVELS, chat_requests):
            with self.subTest(protocol="chat", effort=effort):
                if effort == "none":
                    self.assertEqual(payload["thinking"], {"type": "disabled"})
                    self.assertNotIn("reasoning_effort", payload)
                else:
                    self.assertEqual(payload["reasoning_effort"], effort)
                    self.assertNotIn("thinking", payload)
        for effort, payload in zip(EFFORT_LEVELS, responses_requests):
            with self.subTest(protocol="responses", effort=effort):
                self.assertEqual(payload["reasoning"], {"effort": effort})

    def test_non_finite_numeric_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderError, "numeric settings"):
            OpenAICompatibleProvider(
                name="bad",
                config={
                    "base_url": "https://provider.test/v1",
                    "context_window": float("inf"),
                },
                model="x",
                thinking="off",
            )
        with self.assertRaisesRegex(ProviderError, "timeout_seconds"):
            OpenAICompatibleProvider(
                name="bad",
                config={
                    "base_url": "https://provider.test/v1",
                    "timeout_seconds": float("inf"),
                },
                model="x",
                thinking="off",
            )

    def test_disconnect_without_a_response_is_retried(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise httpx2.RemoteProtocolError(
                    "Server disconnected without sending a response."
                )
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "recovered"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": False},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        notices: list[str] = []
        provider.notify = notices.append
        try:
            with patch("minimalcode.provider.transport.time.sleep") as sleep:
                reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "recovered")
        self.assertEqual(attempts, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(len(notices), 3)
        self.assertTrue(notices[0].startswith("[retry]"))
        self.assertIn("1/10", notices[0])

    def test_disconnect_is_retried_ten_times_then_reported(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            raise httpx2.RemoteProtocolError(
                "Server disconnected without sending a response."
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": False},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            with (
                patch("minimalcode.provider.transport.time.sleep") as sleep,
                self.assertRaises(ProviderError) as context,
            ):
                provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(attempts, 11)
        self.assertEqual(sleep.call_count, 10)
        self.assertIn("Server disconnected", str(context.exception))
        # The retry backoff is bounded, so a stuck provider cannot hang the turn.
        self.assertLessEqual(max(call.args[0] for call in sleep.call_args_list), 8.0)

    def test_http_error_reports_status_and_body(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(400, json={"error": {"message": "bad request"}})

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": True},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            with self.assertRaises(ProviderError) as context:
                provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("bad request", str(context.exception))
        self.assertIn("bad request", context.exception.body or "")

    def test_responses_input_skips_empty_messages(self) -> None:
        seen_request: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen_request.update(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [{"type": "message", "content": [{"text": "ok"}]}],
                },
            )

        provider = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": False,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            reply = provider.stream_chat(
                [
                    {"role": "system", "content": "system"},
                    {"role": "assistant", "content": None},
                    {"role": "assistant", "content": ""},
                    {"role": "user", "content": "hello"},
                ]
            )
        finally:
            provider.close()

        self.assertEqual(reply.content, "ok")
        request_input = seen_request["input"]
        self.assertEqual(len(request_input), 2)
        for item in request_input:
            self.assertIsNotNone(item.get("content"))

    def test_invalid_base_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProviderError, "base_url"):
            OpenAICompatibleProvider(
                name="bad",
                config={"base_url": "not a url"},
                model="x",
                thinking="off",
            )

    def test_unknown_protocol_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsupported protocol"):
            OpenAICompatibleProvider(
                name="bad",
                config={"base_url": "https://provider.test", "protocol": "native"},
                model="x",
                thinking="off",
            )

    def test_responses_stream_tool_call_and_history_conversion(self) -> None:
        seen_request: dict[str, object] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen_request.update(json.loads(request.content))
            self.assertEqual(request.url.path, "/v1/responses")
            events = [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "call_id": "call_1",
                        "type": "function_call",
                        "name": "read",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "output_index": 0,
                    "delta": '{"path":',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "output_index": 0,
                    "delta": '"README.md"}',
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "call_id": "call_1",
                        "type": "function_call",
                        "name": "read",
                        "arguments": '{"path":"README.md"}',
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "id": "fc_1",
                                "call_id": "call_1",
                                "type": "function_call",
                                "name": "read",
                                "arguments": '{"path":"README.md"}',
                            }
                        ],
                        "usage": {
                            "input_tokens": 21,
                            "output_tokens": 7,
                            "total_tokens": 28,
                        },
                    },
                },
            ]
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            body += "data: [DONE]\n\n"
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body.encode("utf-8"),
            )

        provider = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "context_window": 1000,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "previous_call",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "previous_call",
                "name": "read",
                "content": '{"status":"success"}',
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
                            "description": "Read",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )
        finally:
            provider.close()

        self.assertEqual(reply.tool_calls[0].id, "call_1")
        self.assertEqual(reply.tool_calls[0].arguments, '{"path":"README.md"}')
        self.assertEqual(reply.usage["prompt_tokens"], 21)
        self.assertEqual(seen_request["model"], "model-r")
        request_input = seen_request["input"]
        self.assertIsInstance(request_input, list)
        self.assertTrue(
            any(
                isinstance(item, dict) and item.get("type") == "function_call_output"
                for item in request_input
            )
        )
        request_tools = seen_request["tools"]
        self.assertEqual(request_tools[0]["name"], "read")

    def test_stream_falls_back_to_json_response(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "plain response"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                },
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": True},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        deltas: list[str] = []
        try:
            reply = provider.stream_chat(
                [{"role": "user", "content": "hello"}],
                on_text=deltas.append,
            )
        finally:
            provider.close()

        self.assertEqual(deltas, ["plain response"])
        self.assertEqual(reply.content, "plain response")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage["total_tokens"], 5)

    def test_responses_stream_falls_back_to_json_response(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "plain"}],
                        }
                    ],
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                },
            )

        provider = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": True,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "plain")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage["prompt_tokens"], 2)

    def test_responses_output_text_items_are_parsed(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [{"type": "output_text", "text": "plain"}],
                },
            )

        provider = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": False,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "plain")

    def test_chat_stream_ignores_non_object_events_and_reports_errors(self) -> None:
        def events(body: str):
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=body.encode("utf-8"),
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": True},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(
                lambda request: events(
                    "data: []\n\n"
                    "data: null\n\n"
                    'data: {"choices":[{"delta":{"content":"ok"},'
                    '"finish_reason":"stop"}]}\n\n'
                )
            ),
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()
        self.assertEqual(reply.content, "ok")

        failing = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": True},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(
                lambda request: events('data: {"error":{"message":"bad key"}}\n\n')
            ),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "bad key"):
                failing.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            failing.close()

    def test_responses_stream_does_not_duplicate_fallback_text(self) -> None:
        events = [
            {"type": "response.output_text.done", "text": "once"},
            {
                "type": "response.output_item.done",
                "item": {
                    "id": "msg_1",
                    "type": "message",
                    "content": [{"type": "output_text", "text": "once"}],
                },
            },
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

        provider = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": True,
            },
            model="model-r",
            thinking="high",
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=body.encode("utf-8"),
                )
            ),
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

        self.assertEqual(reply.content, "once")

    def test_stream_without_a_terminal_event_is_incomplete(self) -> None:
        body = b'data: {"choices":[{"delta":{"content":"half an answer"}}]}\n\n'
        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": True},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=body,
                )
            ),
        )
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()
        self.assertEqual(reply.content, "half an answer")
        self.assertEqual(reply.finish_reason, "incomplete")

    def test_complete_text_rejects_an_incomplete_summary(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "partial"},
                            "finish_reason": "length",
                        }
                    ]
                },
            )

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": False},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "finish reason"):
                provider.complete_text([{"role": "user", "content": "summarize"}])
        finally:
            provider.close()

    def test_max_effort_reaches_both_protocols(self) -> None:
        seen: list[dict[str, object]] = []

        def chat_handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
                },
            )

        def responses_handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [{"type": "message", "content": [{"text": "ok"}]}],
                },
            )

        chat = OpenAICompatibleProvider(
            name="chat",
            config={
                "base_url": "https://provider.test/v1",
                "stream": False,
                "send_reasoning_effort": True,
            },
            model="model-a",
            thinking="max",
            transport=httpx2.MockTransport(chat_handler),
        )
        responses = OpenAICompatibleProvider(
            name="responses",
            config={
                "base_url": "https://provider.test/v1",
                "protocol": "openai_responses",
                "stream": False,
                "send_reasoning_effort": True,
            },
            model="model-r",
            thinking="max",
            transport=httpx2.MockTransport(responses_handler),
        )
        try:
            chat.stream_chat([{"role": "user", "content": "hello"}])
            responses.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            chat.close()
            responses.close()

        self.assertEqual(seen[0]["reasoning_effort"], "max")
        self.assertEqual(seen[1]["reasoning"], {"effort": "max"})

    def test_read_errors_are_not_retried(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            raise httpx2.ReadError("connection dropped")

        provider = OpenAICompatibleProvider(
            name="test",
            config={"base_url": "https://provider.test/v1", "stream": False},
            model="model-a",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        try:
            with self.assertRaises(ProviderError):
                provider.stream_chat([{"role": "user", "content": "hello"}])
        finally:
            provider.close()
        self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
