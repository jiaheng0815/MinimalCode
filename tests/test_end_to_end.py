from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from minimalcode.cli import run
from minimalcode.config import ensure_config, load_settings, write_settings
from minimalcode.errors import ShellError
from minimalcode.shell import detect_shell


def _sse(events: list[dict]) -> bytes:
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    payload += "data: [DONE]\n\n"
    return payload.encode("utf-8")


class _ProviderHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(payload)
        has_tool_result = any(
            message.get("role") == "tool" for message in payload.get("messages", [])
        )
        if has_tool_result:
            events = [
                {
                    "choices": [
                        {"delta": {"content": "finished"}, "finish_reason": "stop"}
                    ]
                }
            ]
        else:
            events = [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_write",
                                        "type": "function",
                                        "function": {
                                            "name": "write",
                                            "arguments": json.dumps(
                                                {
                                                    "path": "created.txt",
                                                    "content": "end-to-end\n",
                                                }
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ]
        body = _sse(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        return


class EndToEndTests(unittest.TestCase):
    def test_local_provider_tool_loop(self) -> None:
        _ProviderHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / ".MinimalCode"
                workspace = root / "workspace"
                workspace.mkdir()
                ensure_config(home)
                settings = load_settings(home, require_file=True)
                data = settings.data
                port = server.server_address[1]
                data["provider"] = "local"
                data["model"] = "test-model"
                data["providers"] = {
                    "local": {
                        "protocol": "openai_chat",
                        "base_url": f"http://127.0.0.1:{port}/v1",
                        "api_key_env": None,
                        "context_window": 4096,
                    }
                }
                write_settings(data, home)

                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = run(
                        [
                            "--json",
                            "--no-session",
                            "--home",
                            str(home),
                            "--cwd",
                            str(workspace),
                            "create a file",
                        ]
                    )
                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["text"], "finished")
                self.assertEqual(
                    (workspace / "created.txt").read_text(encoding="utf-8"),
                    "end-to-end\n",
                )
                self.assertEqual(len(_ProviderHandler.requests), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class _SubagentHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(payload)
        messages = payload.get("messages", [])
        tool_results = [
            message for message in messages if message.get("role") == "tool"
        ]
        if tool_results:
            text = "parent-done"
        else:
            users = [m for m in messages if m.get("role") == "user"]
            last_user = str(users[-1].get("content", "")) if users else ""
            if "SUBAGENT-REVIEW" in last_user:
                text = "child-report"
            else:
                events = [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_subagent",
                                            "type": "function",
                                            "function": {
                                                "name": "subagent",
                                                "arguments": json.dumps(
                                                    {
                                                        "task": "SUBAGENT-REVIEW app.py",
                                                        "timeout": 120,
                                                    }
                                                ),
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                ]
                self._send(events)
                return
        self._send(
            [{"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}]
        )

    def _send(self, events: list[dict]) -> None:
        body = _sse(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        return


class SubagentEndToEndTests(unittest.TestCase):
    def test_parent_dispatches_a_real_child_process(self) -> None:
        try:
            detect_shell()
        except ShellError as exc:
            self.skipTest(f"no usable shell in this environment: {exc}")
        _SubagentHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SubagentHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / ".MinimalCode"
                workspace = root / "workspace"
                workspace.mkdir()
                ensure_config(home)
                settings = load_settings(home, require_file=True)
                data = settings.data
                port = server.server_address[1]
                data["provider"] = "local"
                data["model"] = "test-model"
                data["providers"] = {
                    "local": {
                        "protocol": "openai_chat",
                        "base_url": f"http://127.0.0.1:{port}/v1",
                        "api_key_env": None,
                        "context_window": 4096,
                    }
                }
                write_settings(data, home)

                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = run(
                        [
                            "--json",
                            "--no-session",
                            "--home",
                            str(home),
                            "--cwd",
                            str(workspace),
                            "review the project",
                        ]
                    )
                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["text"], "parent-done")
                requests = _SubagentHandler.requests
                # Parent turn, one child turn, then the parent's follow-up.
                self.assertEqual(
                    len(requests),
                    3,
                    "the child process never reached the provider; the parent's "
                    "last message was: "
                    + json.dumps(
                        requests[-1].get("messages", []) if requests else [],
                        ensure_ascii=False,
                    )[-2000:],
                )
                tool_messages = [
                    message
                    for message in requests[-1]["messages"]
                    if message.get("role") == "tool"
                ]
                self.assertTrue(tool_messages)
                self.assertIn("child-report", tool_messages[-1]["content"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class _AnthropicHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(payload)
        has_tool_result = any(
            block.get("type") == "tool_result"
            for message in payload.get("messages", [])
            for block in message.get("content", [])
            if isinstance(block, dict)
        )
        if has_tool_result:
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_2",
                        "role": "assistant",
                        "usage": {"input_tokens": 30, "output_tokens": 1},
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
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "role": "assistant",
                        "usage": {"input_tokens": 20, "output_tokens": 1},
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
                    "delta": {"type": "signature_delta", "signature": "sig-e2e"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_write",
                        "name": "write",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(
                            {"path": "created.txt", "content": "anthropic\n"}
                        ),
                    },
                },
                {"type": "content_block_stop", "index": 1},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 5},
                },
                {"type": "message_stop"},
            ]
        body = _sse(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        return


class AnthropicEndToEndTests(unittest.TestCase):
    def test_tool_loop_replays_thinking_and_tool_results(self) -> None:
        _AnthropicHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _AnthropicHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / ".MinimalCode"
                workspace = root / "workspace"
                workspace.mkdir()
                ensure_config(home)
                settings = load_settings(home, require_file=True)
                data = settings.data
                port = server.server_address[1]
                data["provider"] = "anthropic-local"
                data["model"] = "deepseek-flash"
                data["providers"] = {
                    "anthropic-local": {
                        "protocol": "anthropic_messages",
                        "base_url": f"http://127.0.0.1:{port}/anthropic",
                        "api_key": "local-key",
                        "context_window": 4096,
                        "send_reasoning_effort": False,
                    }
                }
                write_settings(data, home)

                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = run(
                        [
                            "--json",
                            "--no-session",
                            "--home",
                            str(home),
                            "--cwd",
                            str(workspace),
                            "create a file",
                        ]
                    )

                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["text"], "finished")
                self.assertTrue(payload["completed"])
                self.assertEqual(payload["turns"], 2)
                self.assertEqual(
                    (workspace / "created.txt").read_text(encoding="utf-8"),
                    "anthropic\n",
                )

                requests = _AnthropicHandler.requests
                self.assertEqual(len(requests), 2)
                first = requests[0]
                self.assertEqual(first["stream"], True)
                self.assertIn("max_tokens", first)
                self.assertEqual(first["messages"][0]["role"], "user")
                self.assertEqual(first["tools"][0]["input_schema"]["type"], "object")
                self.assertIn("system", first)

                second = requests[1]
                roles = [message["role"] for message in second["messages"]]
                self.assertEqual(roles, ["user", "assistant", "user"])
                assistant_blocks = second["messages"][1]["content"]
                self.assertEqual(
                    [block["type"] for block in assistant_blocks],
                    ["thinking", "tool_use"],
                )
                self.assertEqual(assistant_blocks[0]["signature"], "sig-e2e")
                self.assertEqual(assistant_blocks[1]["id"], "toolu_write")
                self.assertEqual(
                    assistant_blocks[1]["input"],
                    {"path": "created.txt", "content": "anthropic\n"},
                )
                result_blocks = second["messages"][2]["content"]
                self.assertEqual(result_blocks[0]["type"], "tool_result")
                self.assertEqual(result_blocks[0]["tool_use_id"], "toolu_write")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
