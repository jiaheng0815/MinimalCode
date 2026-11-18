"""DeepSeek tools-API upgrade: OCR, speech, documents, titles, and reminders."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx2

from minimalcode.config import load_settings
from minimalcode.errors import ToolError
from minimalcode.media import ProviderMediaClient
from minimalcode.provider import OpenAICompatibleProvider
from minimalcode.reminder import build_reminder, supports_reminder, weekday_name
from minimalcode.session import SessionStore
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime, available_tool_schemas
from minimalcode.web import ProviderWebClient

_OCR_RESULT = {
    "id": "ocr-1",
    "object": "ocr.result",
    "purpose": "text_recognition",
    "page_count": 2,
    "pages": [
        {"index": 0, "text": "page one", "finish_reason": "stop"},
        {"index": 1, "text": "page two", "finish_reason": "length"},
    ],
}


def _media_config(**overrides: Any) -> dict[str, Any]:
    media: dict[str, Any] = {
        "enabled": True,
        "base_url": "https://api.test",
        "ocr_path": "v1/tools/ocr",
        "asr_path": "v1/tools/asr",
        "tts_path": "v1/tools/tts",
        "files_path": "files",
        "timeout_seconds": 30,
        "max_upload_bytes": 1024,
        "max_audio_bytes": 256,
        "max_pages": 10,
        "max_chars": 32,
        "voice_id": "stella",
        "format": "wav",
        "purpose": "text_recognition",
        "headers": {},
    }
    media.update(overrides)
    return {"base_url": "https://api.test", "api_key": "secret", "media": media}


class MediaClientTests(unittest.TestCase):
    def test_ocr_posts_multipart_and_returns_pages(self) -> None:
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            body = request.content
            self.assertIn(b'name="purpose"', body)
            self.assertIn(b"text_recognition", body)
            self.assertIn(b'filename="scan.pdf"', body)
            return httpx2.Response(200, json=_OCR_RESULT)

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(handler)
        )
        try:
            result = client.ocr("scan.pdf", b"%PDF-1.4")
            self.assertEqual(result["page_count"], 2)
            self.assertEqual(result["text"], "page one\n\npage two")
            # `length` marks the page as clipped even when nothing was dropped.
            self.assertTrue(result["truncated"])
        finally:
            client.close()
        self.assertEqual(seen[0].url.path, "/v1/tools/ocr")

    def test_ocr_reports_an_error_object_returned_with_200(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.dumps(
                {"error": {"message": "OCR did not finish in time"}}
            ).encode()
            return httpx2.Response(200, content=b"\n\n" + body)

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(handler)
        )
        try:
            with self.assertRaisesRegex(ToolError, "did not finish"):
                client.ocr("scan.pdf", b"%PDF-1.4")
        finally:
            client.close()

    def test_transcribe_and_speech(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path.endswith("/asr"):
                self.assertIn(b'filename="speech.wav"', request.content)
                return httpx2.Response(
                    200,
                    json={
                        "request_id": "r1",
                        "text": "how is the weather",
                        "duration_ms": 3200,
                    },
                )
            payload = json.loads(request.content)
            self.assertEqual(payload["voice_id"], "tide")
            self.assertEqual(payload["format"], "wav")
            return httpx2.Response(200, content=b"RIFFwave")

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(handler)
        )
        try:
            heard = client.transcribe("speech.wav", b"RIFF")
            self.assertEqual(heard["text"], "how is the weather")
            spoken = client.speech("hello", voice_id="tide")
            self.assertEqual(spoken["audio"], b"RIFFwave")
            self.assertEqual(spoken["bytes"], 8)
            with self.assertRaisesRegex(ToolError, "voice_id"):
                client.speech("hello", voice_id="nope")
        finally:
            client.close()

    def test_document_upload_is_cached_and_reports_the_file_id(self) -> None:
        calls: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            calls.append(request.url.path)
            self.assertIn(b'name="purpose"', request.content)
            return httpx2.Response(
                200, json={"id": "file-api-1", "purpose": "text_recognition"}
            )

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(handler)
        )
        try:
            first = client.upload("report.pdf", b"%PDF")
            second = client.upload("report.pdf", b"%PDF")
        finally:
            client.close()
        self.assertEqual(first["file_id"], "file-api-1")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        # The second attach must not pay for a second parse.
        self.assertEqual(calls, ["/files"])

    def test_limits_and_redirects_are_rejected(self) -> None:
        def redirect(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(307, headers={"Location": "https://evil.test"})

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(redirect)
        )
        try:
            with self.assertRaisesRegex(ToolError, "too large"):
                client.ocr("big.pdf", b"x" * 2000)
            with self.assertRaisesRegex(ToolError, "too large"):
                client.transcribe("big.wav", b"x" * 300)
            with self.assertRaisesRegex(ToolError, "redirected"):
                client.ocr("scan.pdf", b"%PDF")
        finally:
            client.close()

    def test_disabled_client_exposes_no_capability(self) -> None:
        client = ProviderMediaClient("x", {"media": {"enabled": False}})
        self.assertFalse(client.enabled)
        self.assertFalse(client.ocr_enabled)
        with self.assertRaisesRegex(ToolError, "not enabled"):
            client.ocr("a.pdf", b"%PDF")


class MediaToolTests(unittest.TestCase):
    def _runtime(self, client: Any, cwd: Path, **kwargs: Any) -> ToolRuntime:
        return ToolRuntime(
            load_settings(), cwd, detect_shell(None), media_client=client, **kwargs
        )

    def test_ocr_reads_a_workspace_file_and_speech_writes_one(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path.endswith("/ocr"):
                return httpx2.Response(200, json=_OCR_RESULT)
            return httpx2.Response(200, content=b"RIFFaudio")

        client = ProviderMediaClient(
            "deepseek", _media_config(), transport=httpx2.MockTransport(handler)
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "doc.pdf").write_bytes(b"%PDF-1.4")
            runtime = self._runtime(client, root)
            try:
                result = runtime.dispatch("ocr", {"path": "doc.pdf"})
                self.assertEqual(result["page_count"], 2)
                self.assertTrue(str(result["path"]).endswith("doc.pdf"))
                spoken = runtime.dispatch(
                    "speech", {"text": "hello", "path": "out.wav"}
                )
                self.assertEqual((root / "out.wav").read_bytes(), b"RIFFaudio")
                self.assertEqual(spoken["bytes"], 9)
                schemas = {
                    schema["function"]["name"]
                    for schema in available_tool_schemas(media_client=client)
                }
                self.assertLessEqual({"ocr", "transcribe", "speech"}, schemas)
            finally:
                client.close()
                runtime.mcp.close()

    def test_read_only_keeps_ocr_and_transcribe_but_drops_speech(self) -> None:
        client = ProviderMediaClient(
            "deepseek",
            _media_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=_OCR_RESULT)
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            runtime = self._runtime(client, Path(temp), read_only=True)
            try:
                names = {
                    schema["function"]["name"]
                    for schema in available_tool_schemas(
                        media_client=client, allowed=runtime.allowed_tools
                    )
                }
                self.assertIn("ocr", names)
                self.assertNotIn("speech", names)
                with self.assertRaisesRegex(ToolError, "read-only"):
                    runtime.dispatch("speech", {"text": "hi", "path": "a.wav"})
            finally:
                client.close()
                runtime.mcp.close()

    def test_a_path_outside_the_workspace_is_refused(self) -> None:
        client = ProviderMediaClient(
            "deepseek",
            _media_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=_OCR_RESULT)
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            runtime = self._runtime(client, Path(temp))
            try:
                with self.assertRaises(ToolError):
                    runtime.dispatch("ocr", {"path": "../escape.pdf"})
            finally:
                client.close()
                runtime.mcp.close()


def _with_reminder(settings: Any, **overrides: Any) -> Any:
    """Return the loaded settings with one reminder field replaced."""
    reminder = dict(settings.get("reminder"))
    reminder.update(overrides)
    settings.data["reminder"] = reminder
    return settings


class ReminderTests(unittest.TestCase):
    def test_the_five_fields_and_localized_weekday(self) -> None:
        settings = load_settings()
        moment = dt.datetime(2026, 11, 9, 12, 0)
        self.assertEqual(
            build_reminder(settings, now=moment),
            "2026-11-09,Monday,,App,English",
        )
        self.assertEqual(weekday_name("中文", moment.weekday()), "星期一")
        self.assertEqual(
            build_reminder(
                _with_reminder(settings, location="Singapore", language="中文"),
                now=moment,
            ),
            "2026-11-09,星期一,Singapore,App,中文",
        )

    def test_disabled_and_protocol_gating(self) -> None:
        settings = load_settings()
        self.assertIsNone(build_reminder(_with_reminder(settings, enabled=False)))
        self.assertTrue(supports_reminder("anthropic_messages"))
        self.assertFalse(supports_reminder("openai_responses"))


class ProviderExtensionTests(unittest.TestCase):
    def _provider(self, handler: Any, **extra: Any) -> OpenAICompatibleProvider:
        config: dict[str, Any] = {
            "base_url": "https://provider.test/v1",
            "api_key": "secret",
            **extra,
        }
        return OpenAICompatibleProvider(
            name="deepseek",
            config=config,
            model="deepseek-flash",
            thinking="none",
            transport=httpx2.MockTransport(handler),
        )

    def test_title_and_safety_check_ride_on_the_payload(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "hi"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        extensions = {
            "protocol": "openai_chat",
            "stream": False,
            "session_title": {"enabled": True},
            "safety_check": {"level": "high"},
        }
        provider = self._provider(handler, **extensions)
        try:
            provider.stream_chat([{"role": "user", "content": "hi"}])
        finally:
            provider.close()
        self.assertEqual(payloads[0]["title"], {"enabled": True})
        self.assertEqual(payloads[0]["safety_check"], {"level": "high"})

        # Asking for a title is per request: the agent turns it off once the
        # session has one, and the field must disappear.
        provider = self._provider(handler, **extensions)
        try:
            provider.set_title_request(False)
            provider.stream_chat([{"role": "user", "content": "hi"}])
        finally:
            provider.close()
        self.assertNotIn("title", payloads[1])
        self.assertIn("safety_check", payloads[1])

    def test_deepseek_responses_aliases_the_reserved_find_tool(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "minimalcode_find",
                            "arguments": '{"pattern":"*.py"}',
                        }
                    ],
                },
            )

        provider = self._provider(
            handler,
            protocol="openai_responses",
            stream=False,
            session_title={"enabled": True},
        )
        try:
            reply = provider.stream_chat(
                [
                    {"role": "user", "content": "find files"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_0",
                                "type": "function",
                                "function": {
                                    "name": "find",
                                    "arguments": '{"pattern":"*.md"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_0",
                        "name": "find",
                        "content": '{"status":"ok"}',
                    },
                    {"role": "user", "content": "continue"},
                ],
                tools=available_tool_schemas(),
            )
        finally:
            provider.close()

        self.assertEqual(payloads[0]["title"], {"enabled": True})
        names = [tool["name"] for tool in payloads[0]["tools"]]
        self.assertNotIn("find", names)
        self.assertEqual(names[7], "minimalcode_find")
        replayed = [
            item["name"]
            for item in payloads[0]["input"]
            if item.get("type") == "function_call"
        ]
        self.assertEqual(replayed, ["minimalcode_find"])
        self.assertEqual(reply.tool_calls[0].name, "find")

    def test_deepseek_chat_aliases_the_reserved_find_tool(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "minimalcode_find",
                                            "arguments": '{"pattern":"*.py"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        provider = self._provider(
            handler,
            protocol="openai_chat",
            stream=False,
            session_title={"enabled": True},
        )
        try:
            reply = provider.stream_chat(
                [
                    {"role": "user", "content": "find files"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_0",
                                "type": "function",
                                "function": {
                                    "name": "find",
                                    "arguments": '{"pattern":"*.md"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_0",
                        "name": "find",
                        "content": '{"status":"ok"}',
                    },
                    {"role": "user", "content": "continue"},
                ],
                tools=available_tool_schemas(),
            )
        finally:
            provider.close()

        self.assertEqual(payloads[0]["title"], {"enabled": True})
        names = [tool["function"]["name"] for tool in payloads[0]["tools"]]
        self.assertNotIn("find", names)
        self.assertEqual(names[7], "minimalcode_find")
        replayed = payloads[0]["messages"][1]["tool_calls"][0]["function"]["name"]
        self.assertEqual(replayed, "minimalcode_find")
        self.assertEqual(reply.tool_calls[0].name, "find")

    def test_deepseek_anthropic_aliases_the_reserved_find_tool(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "minimalcode_find",
                            "input": {"pattern": "*.py"},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        provider = self._provider(
            handler,
            protocol="anthropic_messages",
            stream=False,
            max_tokens=64,
            session_title={"enabled": True},
        )
        try:
            reply = provider.stream_chat(
                [
                    {"role": "user", "content": "find files"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "toolu_0",
                                "type": "function",
                                "function": {
                                    "name": "find",
                                    "arguments": '{"pattern":"*.md"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "toolu_0",
                        "name": "find",
                        "content": '{"status":"ok"}',
                    },
                    {"role": "user", "content": "continue"},
                ],
                tools=available_tool_schemas(),
            )
        finally:
            provider.close()

        self.assertEqual(payloads[0]["title"], {"enabled": True})
        names = [tool["name"] for tool in payloads[0]["tools"]]
        self.assertNotIn("find", names)
        self.assertEqual(names[7], "minimalcode_find")
        replayed = [
            block["name"]
            for block in payloads[0]["messages"][1]["content"]
            if block.get("type") == "tool_use"
        ]
        self.assertEqual(replayed, ["minimalcode_find"])
        self.assertEqual(reply.tool_calls[0].name, "find")

    def test_anthropic_attachments_become_a_document_block(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        provider = self._provider(
            handler, protocol="anthropic_messages", stream=False, max_tokens=64
        )
        try:
            provider.stream_chat(
                [
                    {
                        "role": "user",
                        "content": "summarize",
                        "attachments": [{"file_id": "file-api-1"}],
                    }
                ]
            )
        finally:
            provider.close()
        blocks = seen[0]["messages"][0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "summarize"})
        self.assertEqual(
            blocks[1],
            {"type": "document", "source": {"type": "file", "file_id": "file-api-1"}},
        )

    def test_chat_attachments_and_latest_reminder(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        provider = self._provider(handler, stream=False)
        try:
            provider.stream_chat(
                [
                    {
                        "role": "latest_reminder",
                        "content": "2026-11-17,Tuesday,,App,English",
                    },
                    {
                        "role": "user",
                        "content": "summarize",
                        "attachments": [{"file_id": "file-api-1"}],
                    },
                ]
            )
        finally:
            provider.close()
        messages = payloads[0]["messages"]
        self.assertEqual(messages[0]["role"], "latest_reminder")
        self.assertEqual(
            messages[1]["content"],
            [
                {"type": "text", "text": "summarize"},
                {"type": "file", "file_id": "file-api-1"},
            ],
        )
        self.assertNotIn("attachments", messages[1])

    def test_title_is_captured_from_a_stream(self) -> None:
        chunks = [
            {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
            {
                "choices": [
                    {"delta": {"title": "  Greeting  "}, "finish_reason": "stop"}
                ]
            },
        ]

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = b"".join(
                b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks
            )
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body + b"data: [DONE]\n\n",
            )

        provider = self._provider(handler, stream=True)
        try:
            reply = provider.stream_chat([{"role": "user", "content": "hi"}])
        finally:
            provider.close()
        self.assertEqual(reply.content, "hello")
        self.assertEqual(reply.title, "Greeting")


class SessionTitleTests(unittest.TestCase):
    def test_the_title_survives_a_reload_and_shows_in_the_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            store = SessionStore(load_settings(home))
            session = store.create(home, provider="deepseek", model="m", save=True)
            session.append_message({"role": "user", "content": "hello there"})
            session.set_title("Greeting")
            reloaded = store.resolve(session.session_id, home)
            self.assertEqual(reloaded.title, "Greeting")
            self.assertEqual(store.list()[0]["title"], "Greeting")


class RefusalReportTests(unittest.TestCase):
    def test_a_refusal_names_content_safety_in_the_report(self) -> None:
        import io

        from minimalcode.agent import Agent

        for reason in ("refusal", "content_filter"):
            stream = io.StringIO()
            stub = type(
                "Stub",
                (),
                {"quiet": False, "max_turns": 10, "error_output": stream},
            )()
            Agent._report_incomplete(
                stub, turn=1, final_text="partial text", finish_reason=reason
            )
            reported = stream.getvalue()
            self.assertIn("Content safety refused", reported)
            # The partial answer was already streamed, so it is still named;
            # only the framing changes.
            self.assertIn("partial text", reported)


class WebRankingTests(unittest.TestCase):
    def test_search_dedupes_and_orders_by_score(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://a.test/post",
                            "rerank_score": 0.5,
                            "title": "a",
                        },
                        {"url": "https://b.test/", "rerank_score": 2.4, "title": "b"},
                        {
                            "url": "https://a.test/post/",
                            "rerank_score": 1.9,
                            "title": "a2",
                        },
                    ]
                },
            )

        client = ProviderWebClient(
            "deepseek",
            {
                "base_url": "https://api.test/v1/tools",
                "api_key": "secret",
                "web_tools": {
                    "enabled": True,
                    "base_url": "https://api.test/v1/tools",
                    "search_path": "search",
                    "fetch_path": "fetch",
                    "timeout_seconds": 10,
                    "default_count": 5,
                    "max_count": 5,
                    "max_content_chars": 100,
                    "min_rerank_score": 1.0,
                    "headers": {},
                },
            },
            transport=httpx2.MockTransport(handler),
        )
        try:
            results = client.search("q")["results"]
        finally:
            client.close()
        self.assertEqual([item["title"] for item in results], ["b", "a2"])


if __name__ == "__main__":
    unittest.main()
