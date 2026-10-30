"""OpenAI Chat Completions payloads, streams, and response parsing."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import httpx2

from ..errors import ProviderError
from .common import (
    content_text,
    decode_json_body,
    is_event_stream,
    normalize_usage,
    stream_event_error,
    tool_arguments,
)
from .types import ProviderReply, ToolCall


class ChatProtocolMixin:
    """Chat Completions wire format for the provider client."""

    # Attributes supplied by OpenAICompatibleProvider.
    config: dict[str, Any]
    model: str
    thinking: str
    stream_enabled: bool
    include_usage: bool
    send_reasoning_effort: bool

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        raise NotImplementedError

    @staticmethod
    def _chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop the Anthropic-only history field from Chat messages."""
        return [
            {key: value for key, value in message.items() if key != "provider_content"}
            for message in messages
            if isinstance(message, dict)
        ]

    def _chat_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the Chat Completions request body."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._chat_messages(messages),
            "stream": self.stream_enabled,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.stream_enabled and self.include_usage:
            payload["stream_options"] = {"include_usage": True}
        if self.send_reasoning_effort:
            effort = self.thinking.strip().lower()
            if effort == "none":
                payload["thinking"] = {"type": "disabled"}
            elif effort:
                payload["reasoning_effort"] = effort
        extra = self.config.get("extra_body")
        if isinstance(extra, dict):
            # Never let extra_body override protocol-critical fields.
            reserved = {"model", "messages", "stream", "tools", "tool_choice"}
            payload.update(
                {key: value for key, value in extra.items() if key not in reserved}
            )
        return payload

    def _chat_stream_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Request a streaming reply, tolerating JSON when SSE is misconfigured."""
        response = self._open(payload)
        try:
            if not is_event_stream(response):
                try:
                    body = response.read()
                except httpx2.HTTPError as exc:
                    raise ProviderError(f"provider stream interrupted: {exc}") from exc
                parsed, data = decode_json_body(body)
                if parsed:
                    return self._parse_chat_payload(
                        data, on_text=on_text, on_thinking=on_thinking
                    )
                text = body.decode("utf-8", errors="replace")
                if "data:" not in text:
                    raise ProviderError("provider returned an invalid response")
                lines: Iterable[str] = text.splitlines()
            else:
                lines = response.iter_lines()
            return self._parse_chat_stream(
                lines, on_text=on_text, on_thinking=on_thinking
            )
        finally:
            response.close()

    def _parse_chat_stream(
        self,
        lines: Iterable[str],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Accumulate content, tool-call fragments, usage, and finish state."""
        content_parts: list[str] = []
        # Calls arrive as fragments keyed by `index`: id and name are sent once,
        # while the arguments JSON string is split across many chunks, so each
        # entry accumulates instead of being overwritten.
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        saw_terminal = False
        usage: dict[str, int] = {}

        try:
            for line in lines:
                if not line or line.startswith((":", "event:")):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    saw_terminal = True
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    # Unknown SSE fields are ignored; malformed JSON is not a
                    # protocol event and must not abort an otherwise valid stream.
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise stream_event_error(event)

                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    parsed_usage = normalize_usage(raw_usage)
                    if parsed_usage:
                        usage = parsed_usage

                choices = event.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    delta = {}
                text = content_text(delta.get("content"))
                if text:
                    content_parts.append(text)
                    if on_text is not None:
                        on_text(text)

                if on_thinking is not None:
                    # DeepSeek/Qwen stream `reasoning_content`; OpenRouter uses
                    # `reasoning`. Neither belongs in `content`: reasoning is
                    # display-only and must never enter the assistant message.
                    reasoning = content_text(
                        delta.get("reasoning_content")
                    ) or content_text(delta.get("reasoning"))
                    if reasoning:
                        on_thinking(reasoning)

                raw_calls = delta.get("tool_calls")
                if isinstance(raw_calls, list):
                    for raw_call in raw_calls:
                        if not isinstance(raw_call, dict):
                            continue
                        try:
                            index = int(raw_call.get("index", len(calls)))
                        except (TypeError, ValueError):
                            # Some OpenAI-compatible servers omit `index`; the
                            # number of calls seen so far is then the only
                            # ordering signal available.
                            index = len(calls)
                        entry = calls.setdefault(
                            index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        if raw_call.get("id"):
                            entry["id"] = str(raw_call["id"])
                        function = raw_call.get("function")
                        if isinstance(function, dict):
                            if function.get("name"):
                                entry["name"] = str(function["name"])
                            arguments = function.get("arguments")
                            if isinstance(arguments, str):
                                entry["arguments"] += arguments
                            elif arguments is not None:
                                entry["arguments"] = tool_arguments(arguments)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                    saw_terminal = True
        except httpx2.HTTPError as exc:
            raise ProviderError(f"provider stream interrupted: {exc}") from exc

        tool_calls: list[ToolCall] = []
        # Sorting by stream index replays the calls in the order the model emitted
        # them; dict insertion order can differ when a later chunk fills in an
        # earlier index.
        for index in sorted(calls):
            entry = calls[index]
            if not entry["name"]:
                continue
            tool_calls.append(
                ToolCall(
                    id=entry["id"] or f"call_{index}",
                    name=entry["name"],
                    arguments=entry["arguments"] or "{}",
                )
            )
        if not saw_terminal:
            # The connection closed before the provider sent finish_reason or
            # [DONE]. Treating that as a normal stop silently truncates answers
            # and can execute a half-received tool call.
            finish_reason = "incomplete"
        elif finish_reason is None:
            finish_reason = "tool_calls" if tool_calls else "stop"
        if not content_parts and not tool_calls and not saw_terminal:
            # `saw_terminal` is re-read here on purpose: a stream that terminated
            # cleanly with no content is a legal empty answer, while one that died
            # without a terminal event is a broken response.
            raise ProviderError("provider returned an empty stream")
        return ProviderReply(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _parse_chat_payload(
        self,
        data: Any,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Parse one non-streaming Chat Completions response body."""
        if not isinstance(data, dict):
            raise ProviderError("provider response must be an object")
        if data.get("error"):
            raise ProviderError(f"provider response failed: {data['error']}")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("provider response has no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("provider response choice must be an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("provider response has no message")
        content = content_text(message.get("content"))
        if content and on_text is not None:
            on_text(content)
        if on_thinking is not None:
            reasoning = content_text(message.get("reasoning_content")) or content_text(
                message.get("reasoning")
            )
            if reasoning:
                on_thinking(reasoning)
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            for index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "")
                if not name:
                    continue
                calls.append(
                    ToolCall(
                        id=str(raw_call.get("id") or f"call_{index}"),
                        name=name,
                        arguments=tool_arguments(function.get("arguments")),
                    )
                )
        finish_reason = choice.get("finish_reason")
        return ProviderReply(
            content=content,
            tool_calls=calls,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            usage=normalize_usage(data.get("usage")),
        )

    def _chat_regular_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Request a non-streaming reply, accepting SSE if the server sends it."""
        response = self._open(payload)
        try:
            # Some gateways ignore `stream: false` and answer with SSE anyway, so
            # the content type, not the request flag, selects the parser.
            if is_event_stream(response):
                return self._parse_chat_stream(
                    response.iter_lines(), on_text=on_text, on_thinking=on_thinking
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError("provider returned invalid JSON") from exc
            except httpx2.HTTPError as exc:
                raise ProviderError(f"provider response read failed: {exc}") from exc
        finally:
            response.close()
        return self._parse_chat_payload(data, on_text=on_text, on_thinking=on_thinking)
