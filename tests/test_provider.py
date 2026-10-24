from __future__ import annotations

import json
import unittest

import httpx

from minimalcode.provider import OpenAICompatibleProvider


class ProviderTests(unittest.TestCase):
    def test_stream_tool_calls_and_usage(self) -> None:
        seen_request: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
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
            return httpx.Response(
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
            transport=httpx.MockTransport(handler),
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

        def handler(request: httpx.Request) -> httpx.Response:
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
            return httpx.Response(
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
            transport=httpx.MockTransport(handler),
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


if __name__ == "__main__":
    unittest.main()
