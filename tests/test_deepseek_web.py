"""Offline tests for the deepseek_web protocol: stream parsing and tool blocks.

No network: every case feeds recorded frame shapes through the parsers, which are
the parts that broke in practice (trailing snapshots and nested BATCH patches).
"""

from __future__ import annotations

import json
import ssl
import time
import types
import unittest

from minimalcode.errors import ProviderError
from minimalcode.provider.deepseek_web import (
    CHAT_PATH,
    SESSION_PATH,
    TOOL_CLOSE,
    TOOL_OPEN,
    DeepSeekWebProtocolMixin,
    _parse_dsml,
    _parse_tool_block,
    _ToolFilter,
    _ToolParseError,
    extract_tool_calls,
    iter_reply_chunks,
)
from minimalcode.provider.transport import TransportMixin


def frames(*items: object) -> list[str]:
    return [f"data: {json.dumps(item, ensure_ascii=False)}" for item in items]


def collect(lines: list[str]) -> tuple[str, str]:
    answer, thinking = [], []
    for kind, text in iter_reply_chunks(lines):
        if kind == "answer":
            answer.append(text)
        elif kind == "thinking":
            thinking.append(text)
    return "".join(answer), "".join(thinking)


class StreamParsingTests(unittest.TestCase):
    def test_append_and_bare_append(self) -> None:
        lines = frames(
            {
                "v": {
                    "response": {
                        "message_id": 2,
                        "fragments": [{"type": "RESPONSE", "content": ""}],
                    }
                }
            },
            {"p": "response/fragments/-1/content", "o": "APPEND", "v": "你"},
            {"v": "好"},
            {"p": "response/status", "o": "SET", "v": "FINISHED"},
        )
        self.assertEqual(collect(lines), ("你好", ""))

    def test_trailing_snapshot_delivers_unstreamed_answer(self) -> None:
        # Thinking mode streams THINK only; the RESPONSE text arrives once, in the
        # trailing full snapshot, and must not be duplicated by it.
        lines = frames(
            {
                "v": {
                    "response": {
                        "message_id": 7,
                        "fragments": [{"type": "THINK", "content": "想"}],
                    }
                }
            },
            {"p": "response/fragments/-1/content", "o": "APPEND", "v": "一想"},
            {
                "p": "response/fragments",
                "v": [
                    {"type": "THINK", "content": "想一想要答案"},
                    {"type": "RESPONSE", "content": "答案"},
                ],
            },
        )
        self.assertEqual(collect(lines), ("答案", "想一想要答案"))

    def test_nested_batch_registers_fragments_in_order(self) -> None:
        # Search mode wraps the fragments list in a BATCH patch; -1 must then point
        # at the RESPONSE fragment, not at the SEARCH one.
        lines = frames(
            {
                "p": "response",
                "o": "BATCH",
                "v": [
                    {
                        "p": "fragments",
                        "v": [
                            {"type": "SEARCH", "content": "搜索到 12 个网页"},
                            {"type": "RESPONSE", "content": ""},
                        ],
                    }
                ],
            },
            {"p": "response/fragments/-1/content", "o": "APPEND", "v": "完成"},
            {"v": "。"},
        )
        self.assertEqual(collect(lines), ("完成。", ""))

    def test_tip_fragment_is_neither_answer_nor_thinking(self) -> None:
        lines = frames(
            {
                "p": "response/fragments",
                "v": [
                    {"type": "RESPONSE", "content": "正文"},
                    {"type": "TIP", "content": "AI 生成"},
                ],
            },
        )
        self.assertEqual(collect(lines), ("正文", ""))


class ToolFilterTests(unittest.TestCase):
    def test_block_is_hidden_from_display_text(self) -> None:
        tool_filter = _ToolFilter(True)
        visible = tool_filter.feed("我来读取。")
        visible += tool_filter.feed(TOOL_OPEN)
        visible += tool_filter.feed(
            '[{"name": "read_file", "arguments": {"path": "a"}}]'
        )
        visible += tool_filter.feed(TOOL_CLOSE)
        visible += tool_filter.finish()
        self.assertEqual(visible.strip(), "我来读取。")
        self.assertEqual(tool_filter.calls[0]["name"], "read_file")

    def test_marker_split_across_chunks_is_not_leaked(self) -> None:
        tool_filter = _ToolFilter(True)
        emitted = ""
        for chunk in ("说明", TOOL_OPEN[:5], TOOL_OPEN[5:], "[]", TOOL_CLOSE):
            emitted += tool_filter.feed(chunk)
        emitted += tool_filter.finish()
        self.assertEqual(emitted, "说明")
        self.assertEqual(tool_filter.calls, [])

    def test_disabled_filter_passes_text_through(self) -> None:
        tool_filter = _ToolFilter(False)
        self.assertEqual(tool_filter.feed(TOOL_OPEN), TOOL_OPEN)
        self.assertEqual(tool_filter.finish(), "")

    def test_fenced_block_is_parsed(self) -> None:
        self.assertEqual(
            _parse_tool_block('```json\n[{"name": "shell", "arguments": "{}"}]\n```')[
                0
            ]["name"],
            "shell",
        )


class PromptTests(unittest.TestCase):
    def test_tools_render_into_the_prompt(self) -> None:
        mixin = DeepSeekWebProtocolMixin()
        mixin.config = {"tool_mode": "text"}
        prompt = mixin._ds_web_prompt(
            [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "read a.txt"},
            ],
            [{"type": "function", "function": {"name": "read_file"}}],
        )
        self.assertIn(TOOL_OPEN, prompt)
        self.assertIn("read_file", prompt)
        self.assertIn("## user", prompt)
        self.assertLess(prompt.index("## system"), prompt.index("## user"))

    def test_tool_mode_none_omits_instructions(self) -> None:
        mixin = DeepSeekWebProtocolMixin()
        mixin.config = {"tool_mode": "none"}
        prompt = mixin._ds_web_prompt(
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "read_file"}}],
        )
        self.assertNotIn(TOOL_OPEN, prompt)


DSML_SAMPLE = (
    "<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
    '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read">\n'
    '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path" string="true">'
    "D:\\demo\\note.txt</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n"
    "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
    "</\uff5c\uff5cDSML\uff5c\uff5c calls>"
)


class DsmlTests(unittest.TestCase):
    """The web model prefers its own DSML markup over the requested JSON block."""

    def test_native_markup_is_parsed(self) -> None:
        calls = _parse_dsml(DSML_SAMPLE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "read")
        self.assertEqual(calls[0]["arguments"], {"path": "D:\\demo\\note.txt"})

    def test_markup_is_hidden_from_display_text(self) -> None:
        tool_filter = _ToolFilter(True)
        shown = tool_filter.feed("让我看看。")
        shown += tool_filter.feed(DSML_SAMPLE[:20])
        shown += tool_filter.feed(DSML_SAMPLE[20:])
        shown += tool_filter.finish()
        self.assertEqual(shown.strip(), "让我看看。")
        self.assertEqual(tool_filter.calls[0]["name"], "read")

    def test_json_literal_parameter_is_decoded(self) -> None:
        markup = (
            '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="shell">'
            '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="timeout" '
            'string="false">30</\uff5c\uff5cDSML\uff5c\uff5c parameter>'
            "</\uff5c\uff5cDSML\uff5c\uff5c invoke>"
        )
        calls = _parse_dsml(markup)
        self.assertEqual(calls[0]["arguments"], {"timeout": 30})

    def test_markup_without_named_invoke_raises(self) -> None:
        # Real markup that lost its tool name is a broken call, not prose.
        with self.assertRaises(ProviderError):
            _parse_dsml(
                "<\uff5c\uff5cDSML\uff5c\uff5c invoke>"
                '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path">a'
                "</\uff5c\uff5cDSML\uff5c\uff5c invoke>"
            )

    def test_plain_prose_mentioning_invoke_is_not_markup(self) -> None:
        self.assertEqual(_parse_dsml("invoke name=broken"), [])


class ReportedRegressionTests(unittest.TestCase):
    """The three failures seen in a real session, kept as executable examples."""

    def _collect(self, *chunks: str) -> tuple[str, list[dict]]:
        tool_filter = _ToolFilter(True)
        shown = ""
        for chunk in chunks:
            shown += tool_filter.feed(chunk)
        shown += tool_filter.finish()
        return shown, tool_filter.calls

    def test_unescaped_quotes_inside_json_string_are_repaired(self) -> None:
        # "command": "python -c "print(1)"" used to raise from json.loads.
        body = (
            TOOL_OPEN
            + '\n[{"name": "shell", "arguments": {"command": '
            + '"python -c "print(1)""}}]\n'
            + TOOL_CLOSE
        )
        calls = extract_tool_calls(body)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "shell")
        self.assertEqual(calls[0]["arguments"]["command"], 'python -c "print(1)"')

    def test_alternate_closing_tag_spelling_is_recognized(self) -> None:
        # The model switched to <</TOOL_CALLS>>>; that must not reach the display.
        shown, calls = self._collect(
            "让我先看看。<<</TOOL_CALLS>>>",
            '[{"name": "read", "arguments": {"path": "a.txt"}}]',
            "<<<END_TOOL_CALLS>>>",
        )
        self.assertEqual(shown, "让我先看看。")
        self.assertEqual(calls[0]["arguments"]["path"], "a.txt")

    def test_missing_parameter_close_tag_does_not_leak_markup(self) -> None:
        # Regression: the path arrived as "D:\\MinimalCode\\" plus tags.
        markup = (
            '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read">'
            '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path" string="true">'
            "D:\\MinimalCode\\a.txt"
            "</\uff5c\uff5cDSML\uff5c\uff5c invoke>"
        )
        calls = _parse_dsml(markup)
        self.assertEqual(calls[0]["arguments"]["path"], "D:\\MinimalCode\\a.txt")
        self.assertNotIn("DSML", calls[0]["arguments"]["path"])

    def test_residual_markup_is_stripped_from_values(self) -> None:
        markup = (
            '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read">'
            '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path" string="true">'
            "a.txt<\uff5c\uff5cDSML\uff5c\uff5c parameter>"
            "</\uff5c\uff5cDSML\uff5c\uff5c invoke>"
        )
        self.assertEqual(_parse_dsml(markup)[0]["arguments"]["path"], "a.txt")

    def test_undecodable_block_raises_instead_of_returning_garbage(self) -> None:
        with self.assertRaises(_ToolParseError):
            extract_tool_calls('{"name": "shell", "arguments": {broken}}')

    def test_parse_error_is_a_provider_error(self) -> None:
        self.assertTrue(issubclass(_ToolParseError, ProviderError))

    def test_two_blocks_in_one_reply_are_both_parsed(self) -> None:
        _shown, calls = self._collect(
            "先读后写。",
            TOOL_OPEN
            + '[{"name": "read", "arguments": {"path": "a.txt"}}]'
            + TOOL_CLOSE,
            TOOL_OPEN
            + '[{"name": "write", "arguments": {"path": "b.txt", "content": "x"}}]'
            + TOOL_CLOSE,
        )
        self.assertEqual([call["name"] for call in calls], ["read", "write"])

    def test_stray_text_before_marker_is_preserved(self) -> None:
        shown, _calls = self._collect("答案在这里。", TOOL_OPEN, "[]", TOOL_CLOSE)
        self.assertEqual(shown, "答案在这里。")


class _FakeResponse:
    """Minimal stand-in for an httpx2 streaming response."""

    status_code = 200

    def __init__(self, text: str):
        self.headers = {"content-type": "text/event-stream; charset=utf-8"}
        # Split into small chunks so the streaming filter is exercised too.
        payload = json.dumps(
            {"v": {"response": {"fragments": [{"type": "RESPONSE", "content": text}]}}},
            ensure_ascii=False,
        )
        self._lines = [f"data: {payload}", "data: [DONE]"]
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class _FakeProvider(DeepSeekWebProtocolMixin):
    """Drives the real mixin with the transport and handshake replaced."""

    def __init__(self, replies: list[str], config: dict | None = None):
        self.protocol = "deepseek_web"
        self.config = {
            "base_url": "https://chat.deepseek.com",
            "tool_mode": "text",
            **(config or {}),
        }
        self.model = "default"
        self.thinking = "high"
        self.notify = None
        self._replies = list(replies)
        self.prompts: list[str] = []
        self.payloads: list[dict] = []
        self._client = types.SimpleNamespace(headers={})

    def _endpoint(self) -> str:
        return "https://chat.deepseek.com" + CHAT_PATH

    def _ds_web_session(self) -> str:
        return "session-1"

    def _ds_web_pow(self) -> str:
        return "pow-ticket"

    def _ds_web_apply_cookie(self) -> None:
        return None

    def _open(self, payload: dict):
        self.prompts.append(str(payload.get("prompt")))
        self.payloads.append(dict(payload))
        return _FakeResponse(self._replies.pop(0))

    def _sse_lines(self, response):
        return response.iter_lines()
        return response.iter_lines()

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        """Same entry point the agent calls; the dispatch lives on the client."""
        return self._ds_web_turn(
            messages, tools, on_text=on_text, on_thinking=on_thinking
        )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    }
]


class TurnTests(unittest.TestCase):
    """The turn must parse a reply, and must re-ask instead of shipping garbage."""

    def test_dsml_reply_becomes_a_clean_tool_call(self) -> None:
        markup = (
            "让我看看。\n\n<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
            '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read">\n'
            '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path" string="true">'
            "D:\\work\\a.txt</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n"
            "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
            "</\uff5c\uff5cDSML\uff5c\uff5c calls>"
        )
        provider = _FakeProvider([markup])
        reply = provider.stream_chat(
            [{"role": "user", "content": "读取 a.txt"}], tools=TOOLS
        )
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual(reply.content, "让我看看。")
        self.assertEqual(reply.tool_calls[0].name, "read")
        self.assertEqual(
            json.loads(reply.tool_calls[0].arguments),
            {"path": "D:\\work\\a.txt"},
        )
        self.assertEqual(len(provider.prompts), 1)

    def test_broken_json_triggers_one_correction_turn(self) -> None:
        broken = (
            TOOL_OPEN
            + '\n[{"name": "read", "arguments": {"path": "a.txt"}]\n'
            + TOOL_CLOSE
        )
        good = (
            TOOL_OPEN
            + '[{"name": "read", "arguments": {"path": "a.txt"}}]'
            + TOOL_CLOSE
        )
        provider = _FakeProvider([broken, good])
        reply = provider.stream_chat([{"role": "user", "content": "读"}], tools=TOOLS)
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual(json.loads(reply.tool_calls[0].arguments), {"path": "a.txt"})
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("# Correction", provider.prompts[1])

    def test_correction_is_bounded_by_the_retry_budget(self) -> None:
        broken = '{"name": "read", "arguments": {broken}}'
        provider = _FakeProvider([broken, broken, broken], {"tool_parse_retries": 1})
        with self.assertRaises(_ToolParseError):
            provider.stream_chat([{"role": "user", "content": "读"}], tools=TOOLS)
        self.assertEqual(len(provider.prompts), 2)

    def test_zero_retries_raises_immediately(self) -> None:
        provider = _FakeProvider(
            ['{"name": "read", "arguments": {broken}}'], {"tool_parse_retries": 0}
        )
        with self.assertRaises(_ToolParseError):
            provider.stream_chat([{"role": "user", "content": "读"}], tools=TOOLS)
        self.assertEqual(len(provider.prompts), 1)

    def test_plain_answer_has_no_tool_calls(self) -> None:
        provider = _FakeProvider(["就是 42。"])
        reply = provider.stream_chat(
            [{"role": "user", "content": "答案？"}], tools=TOOLS
        )
        self.assertEqual(reply.content, "就是 42。")
        self.assertEqual(reply.tool_calls, [])
        self.assertEqual(reply.finish_reason, "stop")

    def test_endpoint_guard_rejects_a_foreign_host(self) -> None:
        provider = _FakeProvider(["x"], {"base_url": "https://api.deepseek.com/v1"})
        with self.assertRaises(ProviderError):
            provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)


class _FakeJsonResponse:
    """A non-streaming response carrying one JSON payload."""

    status_code = 200

    def __init__(self, payload: dict):
        self.headers = {"content-type": "application/json"}
        self.payload = json.dumps(payload).encode("utf-8")
        self.closed = False

    def iter_bytes(self):
        yield self.payload

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class _FakeHttpClient:
    """Replays a list of outcomes, raising the ones that are exceptions."""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.headers: dict[str, str] = {}
        self.sent = 0

    def build_request(self, method, url, json=None, timeout=None):
        return {"method": method, "url": url}

    def send(self, request, stream=True):
        self.sent += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _HandshakeProvider(TransportMixin, DeepSeekWebProtocolMixin):
    """Uses the real transport retry loop with a scripted HTTP client."""

    def __init__(self, outcomes: list):
        self.protocol = "deepseek_web"
        self.config = {"base_url": "https://chat.deepseek.com"}
        self.base_url = "https://chat.deepseek.com"
        self.model = "default"
        self.thinking = "none"
        self.timeout_seconds = 30
        self.notify = None
        self._client = _FakeHttpClient(outcomes)

    def _endpoint(self) -> str:
        return self.base_url + CHAT_PATH


SESSION_OK = {"code": 0, "data": {"biz_data": {"chat_session": {"id": "session-2"}}}}


class HandshakeRetryTests(unittest.TestCase):
    """A dropped TLS session on a handshake must be retried, not fatal."""

    def setUp(self) -> None:
        # Retries are real here; only the backoff sleep is collapsed.
        self._sleep = time.sleep
        time.sleep = lambda _delay: None

    def tearDown(self) -> None:
        time.sleep = self._sleep

    def test_handshake_survives_a_tls_reset(self) -> None:
        provider = _HandshakeProvider(
            [
                ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"),
                _FakeJsonResponse(SESSION_OK),
            ]
        )
        biz = provider._ds_web_post(SESSION_PATH, {})
        self.assertEqual(biz["chat_session"]["id"], "session-2")
        self.assertEqual(provider._client.sent, 2)

    def test_repeated_tls_resets_give_up_with_a_proxy_hint(self) -> None:
        outcomes = [ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")] * 20
        provider = _HandshakeProvider(outcomes)
        with self.assertRaises(ProviderError) as caught:
            provider._ds_web_post(SESSION_PATH, {})
        message = str(caught.exception)
        self.assertIn("HTTPS_PROXY", message)
        self.assertIn("TLS connection", message)

    def test_proxy_hint_is_not_attached_to_other_errors(self) -> None:
        provider = _HandshakeProvider(
            [_FakeJsonResponse({"code": 40002, "msg": "Missing Token"})]
        )
        with self.assertRaises(ProviderError) as caught:
            provider._ds_web_post(SESSION_PATH, {})
        self.assertNotIn("HTTPS_PROXY", str(caught.exception))
        self.assertEqual(provider._client.sent, 1)


class _CookieOrderProvider(_FakeProvider):
    """Records the order of the handshake and checks the cookie is in place."""

    def __init__(self, replies: list[str]):
        super().__init__(replies)
        self.order: list[str] = []

    def _ds_web_apply_cookie(self) -> None:
        self.order.append("cookie")
        self._client.headers["Cookie"] = "session=1"

    def _ds_web_session(self) -> str:
        self.order.append("session")
        if self._client.headers.get("Cookie") != "session=1":
            raise AssertionError("session create ran without the cookie")
        return "session-1"

    def _ds_web_pow(self) -> str:
        self.order.append("pow")
        return "pow-ticket"


class HandshakeOrderTests(unittest.TestCase):
    """The cookie and the ticket must be scoped to where they belong."""

    def test_cookie_is_attached_before_the_handshake(self) -> None:
        provider = _CookieOrderProvider(["答案"])
        reply = provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)
        self.assertEqual(reply.content, "答案")
        self.assertEqual(provider.order, ["cookie", "session", "pow"])

    def test_pow_ticket_is_not_left_on_the_client(self) -> None:
        provider = _CookieOrderProvider(["一", "二"])
        provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)
        self.assertNotIn("x-ds-pow-response", provider._client.headers)
        provider.stream_chat([{"role": "user", "content": "again"}], tools=None)
        self.assertEqual(provider.order.count("pow"), 2)

    def test_missing_cookie_is_not_an_error(self) -> None:
        import os
        from unittest import mock

        class _NoCookie(_FakeProvider):
            def _ds_web_apply_cookie(self) -> None:
                DeepSeekWebProtocolMixin._ds_web_apply_cookie(self)

        provider = _NoCookie(["x"])
        with mock.patch.dict(os.environ, {"DEEPSEEK_WEB_COOKIE": ""}):
            reply = provider.stream_chat(
                [{"role": "user", "content": "hi"}], tools=None
            )
        self.assertEqual(reply.content, "x")
        self.assertNotIn("Cookie", provider._client.headers)


class JsonAnswerTests(unittest.TestCase):
    """A JSON answer must not be mistaken for a tool call."""

    def _run(self, *chunks: str) -> tuple[str, list[dict]]:
        tool_filter = _ToolFilter(True)
        shown = "".join(tool_filter.feed(chunk) for chunk in chunks)
        shown += tool_filter.finish()
        return shown, tool_filter.calls

    def test_names_without_arguments_stay_prose(self) -> None:
        body = '[{"name": "Alice", "age": 30}, {"name": "Bob"}]'
        shown, calls = self._run(body)
        self.assertEqual(calls, [])
        self.assertEqual(shown, body)

    def test_object_answer_stays_prose(self) -> None:
        body = '{"name": "Alice", "age": 30}'
        shown, calls = self._run(body)
        self.assertEqual(calls, [])
        self.assertEqual(shown, body)

    def test_unparsable_string_arguments_raise(self) -> None:
        body = TOOL_OPEN + '[{"name": "shell", "arguments": "not json {"}]' + TOOL_CLOSE
        with self.assertRaises(_ToolParseError):
            extract_tool_calls(body)

    def test_json_string_arguments_are_decoded_not_wrapped(self) -> None:
        body = TOOL_OPEN + '[{"name": "read", "arguments": "{}"}]' + TOOL_CLOSE
        calls = extract_tool_calls(body)
        self.assertEqual(calls[0]["arguments"], {})

    def test_repair_pass_keeps_commas_inside_strings(self) -> None:
        body = (
            TOOL_OPEN
            + '[{"name": "echo", "arguments": {"text": "a, }"}},]'
            + TOOL_CLOSE
        )
        calls = extract_tool_calls(body)
        self.assertEqual(calls[0]["arguments"], {"text": "a, }"})


class PayloadTests(unittest.TestCase):
    def test_model_override_reaches_the_payload(self) -> None:
        provider = _FakeProvider(["ok"], {"model": "default", "model_type": "default"})
        provider.model = "expert"
        provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)
        self.assertEqual(provider.payloads[0]["model_type"], "expert")

    def test_configured_model_type_is_used_for_the_preset_model(self) -> None:
        provider = _FakeProvider(["ok"], {"model": "default", "model_type": "expert"})
        provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)
        self.assertEqual(provider.payloads[0]["model_type"], "expert")


class PowChallengeTests(unittest.TestCase):
    def _solver(self):
        from pathlib import Path

        from minimalcode.provider.deepseek_web import _PowSolver

        return _PowSolver(Path("unused.wasm"))

    def test_partial_challenge_is_a_provider_error(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self._solver().header({"algorithm": "DeepSeekHashV1"})
        self.assertIn("missing", str(caught.exception))

    def test_non_object_challenge_is_a_provider_error(self) -> None:
        with self.assertRaises(ProviderError):
            self._solver().header([])


class HandshakeBodyTests(unittest.TestCase):
    def test_non_object_json_body_is_rejected(self) -> None:
        provider = _HandshakeProvider([])
        provider._post_json = lambda url, payload: b"[]"
        with self.assertRaises(ProviderError) as caught:
            provider._ds_web_post(SESSION_PATH, {})
        self.assertIn("instead of a JSON object", str(caught.exception))


class StreamKindTests(unittest.TestCase):
    def test_untyped_fragment_is_shown_as_answer(self) -> None:
        lines = frames({"p": "response/fragments", "v": [{"content": "无类型"}]})
        self.assertEqual(collect(lines), ("无类型", ""))

    def test_response_content_patch_is_answer(self) -> None:
        lines = frames({"p": "response/content", "v": "正文"})
        self.assertEqual(collect(lines), ("正文", ""))


class SessionIdTests(unittest.TestCase):
    """The live endpoint returns the id at `biz_data.id`."""

    def test_id_from_biz_data(self) -> None:
        provider = _HandshakeProvider(
            [_FakeJsonResponse({"code": 0, "data": {"biz_data": {"id": "s-1"}}})]
        )
        self.assertEqual(provider._ds_web_session(), "s-1")

    def test_nested_chat_session_shape_still_works(self) -> None:
        provider = _HandshakeProvider([_FakeJsonResponse(SESSION_OK)])
        self.assertEqual(provider._ds_web_session(), "session-2")


class PowSolveTests(unittest.TestCase):
    def test_bundled_wasm_solves_a_known_challenge(self) -> None:
        import base64
        import importlib.util

        if importlib.util.find_spec("wasmtime") is None:
            self.skipTest("wasmtime is not installed")
        from minimalcode.provider.deepseek_web import _ensure_wasm, _PowSolver

        solver = _PowSolver(_ensure_wasm())
        header = solver.header(
            {
                "algorithm": "DeepSeekHashV1",
                "challenge": (
                    "17f75b5e0984f15fc0b8def0c77b48ee4b41b1865f5e8217971722f7870ad4cb"
                ),
                "salt": "cba9b8a9bf8368b6341c",
                "expire_at": 1785354733914,
                "difficulty": 144000,
                "signature": "0" * 64,
                "target_path": CHAT_PATH,
            }
        )
        payload = json.loads(base64.b64decode(header))
        self.assertEqual(payload["answer"], 32186)
        self.assertEqual(payload["target_path"], CHAT_PATH)

    def test_bundled_wasm_ships_with_the_package(self) -> None:
        from minimalcode.provider.deepseek_web import _ensure_wasm

        path = _ensure_wasm()
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1024)


class PayloadFieldTests(unittest.TestCase):
    def test_streaming_fields_are_sent(self) -> None:
        provider = _FakeProvider(["ok"])
        provider.stream_chat([{"role": "user", "content": "hi"}], tools=None)
        payload = provider.payloads[0]
        self.assertIs(payload["stream"], True)
        self.assertRegex(str(payload["client_stream_id"]), r"^\d{8}-[0-9a-f]{16}$")


class TokenNormalizationTests(unittest.TestCase):
    def test_bearer_prefix_is_not_doubled(self) -> None:
        from minimalcode.provider.client import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            name="deepseek-web",
            config={
                "protocol": "deepseek_web",
                "base_url": "https://chat.deepseek.com",
                "api_key": "Bearer abc123",
            },
            model="default",
            thinking="high",
        )
        try:
            self.assertEqual(provider.headers["Authorization"], "Bearer abc123")
        finally:
            provider.close()


if __name__ == "__main__":
    unittest.main()
