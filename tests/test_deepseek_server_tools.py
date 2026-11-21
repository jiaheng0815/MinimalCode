"""DeepSeek-API-only features: gating, wire declarations, blocks, and routes.

The interesting failures here are silent: a server tool declared at an endpoint
that does not implement it, a search card that never reaches the page, or a read
link whose `signature` is dropped so the next turn forgets the page.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx2

from minimalcode.config import load_settings
from minimalcode.deepseek import features, is_deepseek_api, server_tools
from minimalcode.errors import ProviderError
from minimalcode.provider import OpenAICompatibleProvider
from minimalcode.webui import create_server
from minimalcode.webui.runtime import WebRuntime

_API = "https://api.deepseek.com/v1"
_SERVER_TOOLS = {"enabled": True, "web_search": True, "read_url": True}
_MEDIA = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "ocr_path": "v1/tools/ocr",
    "asr_path": "v1/tools/asr",
    "tts_path": "v1/tools/tts",
    "files_path": "files",
}
_WEB_TOOLS = {
    "enabled": True,
    "base_url": "https://api.deepseek.com/v1/tools",
    "search_path": "search",
    "fetch_path": "fetch",
    "timeout_seconds": 30,
    "default_count": 5,
    "max_count": 50,
    "max_content_chars": 1000,
    "default_need_content": False,
    "min_rerank_score": 0,
    "headers": {},
}


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "protocol": "openai_chat",
        "base_url": _API,
        "api_key": "secret",
        "server_tools": dict(_SERVER_TOOLS),
        # These tests exercise the explicit conversation-only server path.
        # The agent default uses the standalone block and is covered separately.
        "web_tools": {**_WEB_TOOLS, "enabled": False},
        "media": dict(_MEDIA),
        "safety_check": {"level": "high"},
        "session_title": {"enabled": True},
    }
    config.update(overrides)
    return config


class CapabilityTests(unittest.TestCase):
    def test_the_deepseek_web_app_gets_none_of_it(self) -> None:
        config = _config(protocol="deepseek_web", base_url="https://chat.deepseek.com")
        result = features(config)
        self.assertFalse(result["available"])
        self.assertIn("web-app", result["reason"])
        self.assertEqual(server_tools(config), ())
        self.assertFalse(is_deepseek_api(config))

    def test_another_endpoint_gets_none_of_it(self) -> None:
        config = _config(base_url="https://api.openai.com/v1")
        result = features(config)
        self.assertFalse(result["available"])
        self.assertIn("api.deepseek.com", result["reason"])
        self.assertEqual(server_tools(config), ())

    def test_the_documented_api_gets_the_whole_set(self) -> None:
        result = features(_config())
        self.assertTrue(result["available"])
        self.assertTrue(result["web_search"])
        self.assertTrue(result["read_url"])
        self.assertTrue(result["documents"])
        self.assertTrue(result["ocr"])
        self.assertTrue(result["asr"])
        self.assertTrue(result["tts"])
        self.assertFalse(result["search_api"])
        self.assertFalse(result["fetch_api"])
        self.assertTrue(result["safety"])

    def test_the_two_server_tools_switch_independently(self) -> None:
        config = _config(server_tools={"enabled": True, "web_search": False})
        self.assertEqual(server_tools(config), ("deepseek_read_url",))
        self.assertFalse(features(config)["web_search"])

    def test_an_untouched_media_block_reports_no_capability(self) -> None:
        result = features(_config(media={"enabled": False}))
        self.assertFalse(result["ocr"])
        self.assertFalse(result["documents"])


def _chat_reply() -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "冰岛人口约 39.4 万。",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://example.com/a",
                                "title": "Statistics Iceland",
                                "start_index": 0,
                                "end_index": 4,
                            },
                        }
                    ],
                    "server_tool_calls": [
                        {
                            "index": 0,
                            "reasoning_content": "",
                            "calls": [
                                {
                                    "index": 0,
                                    "id": "srvtoolu_s_1",
                                    "type": "deepseek_web_search",
                                    "kind": "search",
                                    "action": {
                                        "type": "search",
                                        "queries": ["Iceland population"],
                                    },
                                    "status": "completed",
                                    "sources": [
                                        {
                                            "type": "url",
                                            "url": "https://example.com/r",
                                            "title": "Result",
                                            "snippet": "snip",
                                        }
                                    ],
                                },
                                {
                                    "index": 1,
                                    "id": "srvtoolu_rl_1",
                                    "type": "deepseek_read_url",
                                    "kind": "read_link",
                                    "action": {
                                        "type": "read_link",
                                        "url": "https://example.com/a",
                                    },
                                    "status": "completed",
                                    "page": {
                                        "url": "https://example.com/a",
                                        "title": "Statistics Iceland",
                                        "published_at": None,
                                    },
                                    "signature": "sig-1",
                                },
                            ],
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


class ChatServerToolTests(unittest.TestCase):
    def _provider(self, config: dict[str, Any], handler) -> OpenAICompatibleProvider:
        provider = OpenAICompatibleProvider(
            name="deepseek",
            config=config,
            model="deepseek-flash",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        self.addCleanup(provider.close)
        return provider

    def test_declarations_ride_after_the_function_tools(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(200, json=_chat_reply())

        provider = self._provider(_config(stream=False), handler)
        reply = provider.stream_chat(
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "read", "parameters": {"type": "object"}},
                }
            ],
        )
        declarations = [tool for tool in seen[0]["tools"] if "function" not in tool]
        self.assertEqual(
            declarations,
            [
                {"type": "deepseek_web_search"},
                {"type": "deepseek_read_url"},
            ],
        )
        self.assertEqual(seen[0]["tool_choice"], "auto")
        kinds = [block["kind"] for block in reply.server_tools]
        self.assertEqual(kinds, ["search", "read_link"])
        self.assertEqual(reply.server_tools[0]["sources"][0]["title"], "Result")
        self.assertEqual(reply.citations[0]["url"], "https://example.com/a")

    def test_the_transcript_keeps_the_blocks_and_the_citation(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json=_chat_reply())

        provider = self._provider(_config(stream=False), handler)
        reply = provider.stream_chat([{"role": "user", "content": "hi"}], tools=[])
        message = reply.as_message()
        self.assertIn("server_tools", message)
        self.assertIn("citations", message)

    def test_only_read_links_are_replayed(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("no request expected")

        provider = self._provider(_config(stream=False), handler)
        blocks = [
            {"id": "s1", "kind": "search", "action": {"type": "search"}},
            {
                "id": "r1",
                "kind": "read_link",
                "action": {"type": "read_link", "url": "https://example.com/a"},
                "page": {"url": "https://example.com/a", "title": "A"},
                "signature": "sig-1",
            },
        ]
        messages = provider._chat_messages(
            [
                {
                    "role": "assistant",
                    "content": "answer",
                    "server_tools": blocks,
                    "citations": [{"url": "https://example.com/a"}],
                }
            ]
        )
        replayed = messages[0]["server_tool_calls"]
        calls = replayed[0]["calls"]
        self.assertEqual([call["id"] for call in calls], ["r1"])
        self.assertEqual(calls[0]["signature"], "sig-1")
        self.assertNotIn("server_tools", messages[0])
        self.assertNotIn("citations", messages[0])

    def test_a_plain_completion_never_declares_them(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "summary"},
                        }
                    ]
                },
            )

        provider = self._provider(_config(stream=False), handler)
        provider.complete([{"role": "user", "content": "summarize"}])
        self.assertNotIn("tools", seen[0])

    def test_a_non_deepseek_endpoint_refuses_the_switch(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("no request expected")

        provider = self._provider(
            _config(base_url="https://api.openai.com/v1", stream=False), handler
        )
        self.assertEqual(provider.server_tools, ())
        with self.assertRaisesRegex(ProviderError, "api.deepseek.com"):
            provider.set_server_tools(["deepseek_web_search"])

    def test_the_web_protocol_never_declares_them(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("no request expected")

        provider = self._provider(
            _config(
                protocol="deepseek_web",
                base_url="https://chat.deepseek.com",
                stream=False,
            ),
            handler,
        )
        self.assertEqual(provider.server_tools, ())
        self.assertEqual(provider._server_tool_declarations(), [])


class AnthropicServerToolTests(unittest.TestCase):
    def _provider(self, handler) -> OpenAICompatibleProvider:
        provider = OpenAICompatibleProvider(
            name="deepseek-anthropic",
            config=_config(protocol="anthropic_messages", stream=False),
            model="deepseek-flash",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        self.addCleanup(provider.close)
        return provider

    def test_blocks_and_citations_are_merged(self) -> None:
        seen: list[dict[str, Any]] = []
        body = {
            "content": [
                {
                    "type": "server_tool_use",
                    "id": "c1",
                    "name": "deepseek_web_search",
                    "input": {"action": "search", "queries": ["x"]},
                    "caller": {"type": "direct"},
                    "kind": "deep_search",
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "c1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "title": "T",
                            "url": "https://e.com/1",
                            "snippet": "s",
                        }
                    ],
                    "kind": "deep_search",
                },
                {
                    "type": "text",
                    "text": "答案",
                    "citations": [
                        {
                            "type": "web_search_result_location",
                            "url": "https://e.com/1",
                            "title": "T",
                        }
                    ],
                },
            ],
            "stop_reason": "end_turn",
        }

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(200, json=body)

        provider = self._provider(handler)
        reply = provider.stream_chat([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(len(reply.server_tools), 1)
        block = reply.server_tools[0]
        self.assertEqual(block["status"], "completed")
        self.assertEqual(block["sources"][0]["url"], "https://e.com/1")
        self.assertEqual(reply.citations[0]["url"], "https://e.com/1")
        self.assertEqual(
            seen[0]["tools"],
            [
                {"type": "deepseek_web_search", "name": "deepseek_web_search"},
                {"type": "deepseek_read_url", "name": "deepseek_read_url"},
            ],
        )
        # A `file_id` document needs this beta on the same endpoint.
        self.assertEqual(provider.headers["anthropic-beta"], "files-api-2025-04-14")

    def test_a_read_link_is_replayed_with_its_signature(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("no request expected")

        provider = self._provider(handler)
        _, converted = provider._anthropic_messages(
            [
                {
                    "role": "assistant",
                    "content": "answer",
                    "server_tools": [
                        {
                            "id": "rl1",
                            "kind": "read_link",
                            "action": {
                                "type": "read_link",
                                "url": "https://example.com/a",
                            },
                            "page": {
                                "url": "https://example.com/a",
                                "title": "A",
                                "published_at": "2026-01-02",
                            },
                            "signature": "sig-1",
                        }
                    ],
                }
            ]
        )
        types = [block["type"] for block in converted[0]["content"]]
        self.assertEqual(types, ["server_tool_use", "web_fetch_tool_result", "text"])
        self.assertEqual(converted[0]["content"][1]["signature"], "sig-1")
        self.assertEqual(
            converted[0]["content"][0]["input"],
            {"action": "read_link", "url": "https://example.com/a"},
        )


class ResponsesServerToolTests(unittest.TestCase):
    def test_items_are_parsed_and_declared(self) -> None:
        seen: list[dict[str, Any]] = []
        body = {
            "status": "completed",
            "output": [
                {
                    "type": "web_search_call",
                    "id": "ws1",
                    "status": "completed",
                    "kind": "read_link",
                    "action": {"type": "open_page", "url": "https://example.com/a"},
                    "page": {"url": "https://example.com/a", "title": "A"},
                    "signature": "sig-1",
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "答案",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/a",
                                    "title": "A",
                                    "start_index": 0,
                                    "end_index": 2,
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(200, json=body)

        provider = OpenAICompatibleProvider(
            name="deepseek",
            config=_config(protocol="openai_responses", stream=False),
            model="deepseek-flash",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        self.addCleanup(provider.close)
        reply = provider.stream_chat([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(reply.server_tools[0]["kind"], "read_link")
        self.assertEqual(reply.citations[0]["url"], "https://example.com/a")
        self.assertEqual(
            [tool for tool in seen[0]["tools"] if "function" not in tool],
            [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}],
        )
        # Replaying puts the call item back before the answer.
        items = provider._responses_input(
            [
                {
                    "role": "assistant",
                    "content": "答案",
                    "server_tools": reply.server_tools,
                }
            ]
        )
        self.assertEqual(items[0]["type"], "web_search_call")
        self.assertEqual(items[0]["signature"], "sig-1")
        self.assertEqual(items[-1]["role"], "assistant")


class _FakeProvider:
    """Stands in for the wire protocol so the runtime builds without a key."""

    name = "deepseek"
    model = "deepseek-flash"
    thinking = "high"
    context_window = 100_000
    server_tool_events = False

    def __init__(self) -> None:
        self.config = _config()
        self.deepseek = features(self.config)
        self.safety_level = "low"
        self.server_tools: tuple[str, ...] = ()
        self.closed = False

    def set_server_tools(self, names) -> None:
        self.server_tools = tuple(names)

    def set_safety_level(self, level: str) -> None:
        self.safety_level = level

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        raise AssertionError("no turn expected")

    def close(self) -> None:
        self.closed = True


class _FakeMedia:
    enabled = True
    documents_enabled = True
    ocr_enabled = True
    asr_enabled = True
    tts_enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def upload(self, filename, data, purpose=None):
        self.calls.append(("upload", (filename, data, purpose)))
        return {
            "file_id": "file-api-1",
            "filename": filename,
            "purpose": purpose or "text_recognition",
        }

    def ocr(self, filename, data, purpose=None):
        self.calls.append(("ocr", (filename, data, purpose)))
        return {"page_count": 2, "text": "page one", "truncated": False}

    def transcribe(self, filename, data):
        self.calls.append(("asr", (filename, data)))
        return {"text": "hello", "duration_ms": 1200}

    def speech(self, text, *, voice_id=None, audio_format=None):
        self.calls.append(("tts", text))
        return {"audio": b"RIFFwave", "format": "wav", "voice_id": "stella"}

    def close(self) -> None:
        pass


class RuntimeFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.workspace = Path(self.tmp.name) / "project"
        self.workspace.mkdir()
        self.settings = load_settings(self.home)
        self.provider = _FakeProvider()
        self.media = _FakeMedia()
        patcher = patch(
            "minimalcode.webui.runtime.create_provider",
            lambda *args, **kwargs: (self.provider, "deepseek", "deepseek-flash"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        media_patcher = patch(
            "minimalcode.webui.runtime.ProviderMediaClient",
            lambda *args, **kwargs: self.media,
        )
        media_patcher.start()
        self.addCleanup(media_patcher.stop)
        self.runtime = WebRuntime(self.settings, self.workspace)
        self.addCleanup(self.runtime.close)

    def test_switches_reach_the_provider(self) -> None:
        # The provider exists once the agent is built; before that the switch is
        # remembered and applied at construction.
        self.runtime._ensure_agent()
        state = self.runtime.set_web_search(True)
        self.assertTrue(state["web_search"])
        self.assertEqual(state["web_search_mode"], "server")
        self.assertEqual(
            self.provider.server_tools,
            ("deepseek_web_search", "deepseek_read_url"),
        )
        # The tool runtime follows the provider, so a replayed local call cannot
        # slip through while the server-side tool already covers it.
        agent_tools = self.runtime._agent.tools
        self.assertEqual(
            agent_tools.server_tools, ("deepseek_web_search", "deepseek_read_url")
        )
        from minimalcode.errors import ToolError

        with self.assertRaisesRegex(ToolError, "server-side tool"):
            agent_tools.dispatch("web_search", {"query": "q"})
        state = self.runtime.set_web_search(False)
        self.assertEqual(self.provider.server_tools, ())
        self.assertEqual(state["web_search_mode"], "off")
        self.runtime.set_safety(True)
        self.assertEqual(self.provider.safety_level, "high")

    def test_the_switch_also_turns_on_the_standalone_api(self) -> None:
        # An older settings file kept `web_tools` on and `server_tools` off. The
        # Search API is a real web path, so the composer switch must be offered
        # and must turn on instead of refusing with "not available here".
        self.runtime._ensure_agent()
        self.provider.config["web_tools"] = dict(_WEB_TOOLS)
        self.provider.config["server_tools"] = {"enabled": False}
        self.provider.deepseek = features(self.provider.config)
        state = self.runtime.describe()
        self.assertTrue(state["web_search_available"])
        self.assertFalse(state["deepseek"]["web_search"])
        state = self.runtime.set_web_search(True)
        self.assertEqual(state["web_search_mode"], "local")

    def test_the_switch_stays_shut_when_no_web_path_exists(self) -> None:
        from minimalcode.config import ConfigError

        self.runtime._ensure_agent()
        self.provider.config["server_tools"] = {"enabled": False}
        self.provider.config["web_tools"] = {"enabled": False}
        self.provider.deepseek = features(self.provider.config)
        self.assertFalse(self.runtime.describe()["web_search_available"])
        with self.assertRaisesRegex(ConfigError, "not available"):
            self.runtime.set_web_search(True)

    def test_uploads_queue_and_cancel_documents(self) -> None:
        state = self.runtime.attach_document("report.pdf", b"%PDF-1.4", None)
        self.assertEqual(state["attachments"][0]["file_id"], "file-api-1")
        self.assertEqual(state["state"]["attachments"][0]["filename"], "report.pdf")
        state = self.runtime.detach_document("file-api-1")
        self.assertEqual(state["attachments"], [])

    def test_ocr_speech_and_transcription_go_through_the_media_client(self) -> None:
        self.assertEqual(
            self.runtime.recognize_text("scan.pdf", b"x")["text"], "page one"
        )
        self.assertEqual(self.runtime.transcribe("voice.wav", b"x")["text"], "hello")
        audio = self.runtime.speak("你好")
        self.assertEqual(audio["audio"], b"RIFFwave")

    def test_an_upload_route_reads_raw_bytes(self) -> None:
        server = create_server(self.runtime, "127.0.0.1", 0)
        port = server.server_address[1]
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 10)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            connection.request(
                "POST",
                "/api/attach?name=report.pdf",
                body=b"%PDF-1.4",
                headers={"Content-Type": "application/octet-stream"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["attachments"][0]["filename"], "report.pdf")
