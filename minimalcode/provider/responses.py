"""OpenAI Responses API payloads, streams, and response parsing."""

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
    tool_arguments,
)
from .types import ProviderReply, ToolCall


class ResponsesProtocolMixin:
    """Responses API wire format for the provider client."""

    # Attributes supplied by OpenAICompatibleProvider.
    model: str
    thinking: str
    stream_enabled: bool
    send_reasoning_effort: bool
    config: dict[str, Any]

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        raise NotImplementedError

    def _responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert stored Chat-style messages into Responses input items."""
        items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(message.get("tool_call_id") or ""),
                        "output": content if isinstance(content, str) else "",
                    }
                )
                continue
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                if content:
                    items.append({"role": "assistant", "content": content})
                for call in message["tool_calls"]:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    if not isinstance(function, dict):
                        continue
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(call.get("id") or ""),
                            "name": str(function.get("name") or ""),
                            "arguments": tool_arguments(function.get("arguments")),
                        }
                    )
                continue
            if content is None or content == "":
                # Transport items with an empty payload are rejected by strict
                # Responses-compatible servers; there is nothing to send.
                continue
            items.append({"role": role, "content": content})
        return items

    @staticmethod
    def _responses_tools(
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """Flatten Chat-style function schemas into Responses tool objects."""
        if not tools:
            return None
        result: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            result.append(
                {
                    "type": "function",
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters", {"type": "object"}),
                }
            )
        return result

    def _responses_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the Responses request body."""
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._responses_input(messages),
            "stream": self.stream_enabled,
        }
        response_tools = self._responses_tools(tools)
        if response_tools:
            payload["tools"] = response_tools
            payload["tool_choice"] = "auto"
        if self.send_reasoning_effort:
            effort = self.thinking.strip().lower()
            if effort:
                payload["reasoning"] = {"effort": effort}
        extra = self.config.get("extra_body")
        if isinstance(extra, dict):
            # Never let extra_body override protocol-critical fields.
            reserved = {"model", "input", "stream", "tools", "tool_choice"}
            payload.update(
                {key: value for key, value in extra.items() if key not in reserved}
            )
        return payload

    @staticmethod
    def _parse_responses_output(
        output: Any,
    ) -> tuple[str, list[ToolCall]]:
        """Extract text and function calls from a Responses ``output`` list."""
        if not isinstance(output, list):
            return "", []
        content_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                text = content_text(item.get("content"))
                if text:
                    content_parts.append(text)
            elif item_type in {"output_text", "text"}:
                text = content_text(item)
                if text:
                    content_parts.append(text)
            elif item_type == "function_call":
                name = str(item.get("name") or "")
                if name:
                    calls.append(
                        ToolCall(
                            id=str(item.get("call_id") or item.get("id") or ""),
                            name=name,
                            arguments=tool_arguments(item.get("arguments")),
                        )
                    )
        for index, call in enumerate(calls):
            if not call.id:
                call.id = f"call_{index}"
        return "".join(content_parts), calls

    @staticmethod
    def _response_finish(response: dict[str, Any], has_calls: bool) -> str:
        """Map a Responses status object to a finish reason."""
        status = str(response.get("status") or "")
        if status == "incomplete":
            details = response.get("incomplete_details")
            if isinstance(details, dict) and details.get("reason"):
                return str(details["reason"])
            return "incomplete"
        if status == "failed":
            return "failed"
        if status and status != "completed":
            return status
        return "tool_calls" if has_calls else "stop"

    @staticmethod
    def _response_error(event: dict[str, Any]) -> ProviderError:
        """Build a ProviderError from a Responses failure event."""
        response = event.get("response")
        if isinstance(response, dict) and response.get("error"):
            error = response["error"]
        else:
            error = event.get("error") or event.get("message") or event
        return ProviderError(f"provider response failed: {error}")

    def _responses_stream_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Consume Responses SSE events and assemble the final reply."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        fallback_text: list[str] = []
        call_states: dict[str, dict[str, str]] = {}
        # Events address an item either by `item_id` or by `output_index`,
        # depending on the implementation; this alias map lets both spellings
        # resolve to the same accumulated state.
        output_keys: dict[str, str] = {}
        final_response: dict[str, Any] = {}
        usage: dict[str, int] = {}

        def state_for(key: str) -> dict[str, str]:
            """Return mutable call state for an item id or output index."""
            return call_states.setdefault(
                key,
                {"id": "", "name": "", "arguments": "", "index": str(len(call_states))},
            )

        def merge_arguments(state: dict[str, str], arguments: Any) -> None:
            """Apply a whole arguments value on top of accumulated deltas."""
            if isinstance(arguments, str):
                # Whole-string events (`output_item.*`, `arguments.done`) carry the
                # finished JSON string, so they replace streamed deltas; an empty
                # string only stands in when nothing was streamed at all.
                if arguments or not state["arguments"]:
                    state["arguments"] = arguments
            elif isinstance(arguments, dict):
                state["arguments"] = tool_arguments(arguments)

        def update_call(item: dict[str, Any], key: str) -> None:
            """Merge a function-call item into its accumulated state."""
            state = state_for(key)
            if item.get("call_id"):
                state["id"] = str(item["call_id"])
            elif item.get("id"):
                state["id"] = str(item["id"])
            if item.get("name"):
                state["name"] = str(item["name"])
            merge_arguments(state, item.get("arguments"))

        def add_fallback_text(text: str) -> None:
            """Remember completed text for servers that omit delta events."""
            # Several implementations stream only `output_text.done` and then
            # repeat the same text in the completed message item; dedupe so the
            # fallback path cannot print the answer twice.
            if text and (not fallback_text or fallback_text[-1] != text):
                fallback_text.append(text)

        response = self._open(payload)
        try:
            if not is_event_stream(response):
                try:
                    body = response.read()
                except httpx2.HTTPError as exc:
                    raise ProviderError(f"provider stream interrupted: {exc}") from exc
                parsed, data = decode_json_body(body)
                if parsed:
                    return self._parse_responses_payload(
                        data, on_text=on_text, on_thinking=on_thinking
                    )
                text = body.decode("utf-8", errors="replace")
                if "data:" not in text:
                    raise ProviderError("provider returned an invalid response")
                lines: Iterable[str] = text.splitlines()
            else:
                lines = response.iter_lines()
            try:
                for line in self._checked_lines(lines):
                    if not line or line.startswith((":", "event:")):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue

                    event_type = str(event.get("type") or "")
                    if not event_type and event.get("error"):
                        raise self._response_error(event)
                    if event_type == "response.output_text.delta":
                        text = content_text(event.get("delta"))
                        if text:
                            content_parts.append(text)
                            if on_text is not None:
                                on_text(text)
                        continue
                    if event_type == "response.output_text.done":
                        add_fallback_text(content_text(event.get("text")))
                        continue
                    if event_type in {
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    }:
                        # Reasoning summaries arrive as their own items; they are
                        # display-only and never part of the assistant content.
                        text = content_text(event.get("delta"))
                        if text:
                            reasoning_parts.append(text)
                            if on_thinking is not None:
                                on_thinking(text)
                        continue
                    if event_type == "response.output_item.added":
                        item = event.get("item")
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "function_call"
                        ):
                            key = str(item.get("id") or event.get("output_index", "0"))
                            if "output_index" in event:
                                output_keys[str(event["output_index"])] = key
                            update_call(item, key)
                        continue
                    if event_type == "response.function_call_arguments.delta":
                        raw_key = str(
                            event.get("item_id") or event.get("output_index", "0")
                        )
                        key = output_keys.get(raw_key, raw_key)
                        state = state_for(key)
                        delta = event.get("delta")
                        if isinstance(delta, str):
                            state["arguments"] += delta
                        elif delta is not None:
                            state["arguments"] = tool_arguments(delta)
                        continue
                    if event_type == "response.function_call_arguments.done":
                        raw_key = str(
                            event.get("item_id") or event.get("output_index", "0")
                        )
                        key = output_keys.get(raw_key, raw_key)
                        state = state_for(key)
                        # Only the string form is accepted here, matching the
                        # delta event above; object arguments arrive via items.
                        arguments = event.get("arguments")
                        if isinstance(arguments, str):
                            merge_arguments(state, arguments)
                        continue
                    if event_type == "response.output_item.done":
                        item = event.get("item")
                        if not isinstance(item, dict):
                            continue
                        key = str(item.get("id") or event.get("output_index", "0"))
                        if "output_index" in event:
                            output_keys[str(event["output_index"])] = key
                        if item.get("type") == "function_call":
                            update_call(item, key)
                        elif item.get("type") == "message":
                            add_fallback_text(content_text(item.get("content")))
                        elif item.get("type") == "reasoning" and not reasoning_parts:
                            # Servers that stream no reasoning delta still repeat
                            # the finished summary here; emit it exactly once.
                            text = content_text(item.get("summary")) or content_text(
                                item.get("content")
                            )
                            if text and on_thinking is not None:
                                reasoning_parts.append(text)
                                on_thinking(text)
                        continue
                    if event_type in {"response.completed", "response.incomplete"}:
                        completed = event.get("response")
                        if isinstance(completed, dict):
                            final_response = completed
                            parsed_usage = normalize_usage(completed.get("usage"))
                            if parsed_usage:
                                usage = parsed_usage
                        continue
                    if event_type in {"response.failed", "error"}:
                        raise self._response_error(event)
            except httpx2.HTTPError as exc:
                raise ProviderError(f"provider stream interrupted: {exc}") from exc
        finally:
            response.close()

        output_text, output_calls = self._parse_responses_output(
            final_response.get("output")
        )
        if output_text and not content_parts:
            content_parts.append(output_text)
            if on_text is not None:
                on_text(output_text)
        elif not content_parts and fallback_text:
            text = "".join(fallback_text)
            content_parts.append(text)
            if on_text is not None:
                on_text(text)

        tool_calls = output_calls
        if not tool_calls:
            for index, state in enumerate(call_states.values()):
                if not state["name"]:
                    continue
                tool_calls.append(
                    ToolCall(
                        id=state["id"] or f"call_{index}",
                        name=state["name"],
                        arguments=state["arguments"] or "{}",
                    )
                )

        if final_response:
            finish_reason = self._response_finish(final_response, bool(tool_calls))
        else:
            # Neither `response.completed` nor `response.incomplete` arrived, so
            # the stream was cut short; reporting a clean stop here would let a
            # truncated answer or half-received tool call through, as with Chat.
            finish_reason = "incomplete"
        return ProviderReply(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _parse_responses_payload(
        self,
        data: Any,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Parse one non-streaming Responses payload."""
        if not isinstance(data, dict):
            raise ProviderError("provider response must be an object")
        if data.get("status") == "failed" or data.get("error"):
            detail = (
                data.get("error") or data.get("incomplete_details") or "unknown error"
            )
            raise ProviderError(f"provider response failed: {detail}")
        output = data.get("output")
        if not isinstance(output, list) and not isinstance(
            data.get("output_text"), str
        ):
            raise ProviderError("provider response has no output")
        content, calls = self._parse_responses_output(output)
        if not content and isinstance(data.get("output_text"), str):
            content = data["output_text"]
        if content and on_text is not None:
            on_text(content)
        if on_thinking is not None and isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "reasoning":
                    continue
                reasoning = content_text(item.get("summary")) or content_text(
                    item.get("content")
                )
                if reasoning:
                    on_thinking(reasoning)
        return ProviderReply(
            content=content,
            tool_calls=calls,
            finish_reason=self._response_finish(data, bool(calls)),
            usage=normalize_usage(data.get("usage")),
        )

    def _parse_responses_sse_body(
        self,
        response: httpx2.Response,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Parse an SSE body received for a non-streaming request."""
        try:
            body = response.read()
        except httpx2.HTTPError as exc:
            raise ProviderError(f"provider stream interrupted: {exc}") from exc
        text = body.decode("utf-8", errors="replace")
        if "data:" not in text:
            raise ProviderError("provider returned an invalid response")
        content_parts: list[str] = []
        final_response: dict[str, Any] = {}
        for line in self._checked_lines(text.splitlines()):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type == "response.output_text.delta":
                delta = content_text(event.get("delta"))
                if delta:
                    content_parts.append(delta)
                    if on_text is not None:
                        on_text(delta)
            elif event_type in {"response.completed", "response.incomplete"}:
                completed = event.get("response")
                if isinstance(completed, dict):
                    final_response = completed
            elif event_type in {"response.failed", "error"}:
                raise self._response_error(event)
        if final_response:
            reply = self._parse_responses_payload(
                final_response, on_text=None, on_thinking=on_thinking
            )
            if not content_parts and reply.content and on_text is not None:
                on_text(reply.content)
            return reply
        if not content_parts:
            raise ProviderError("provider returned an empty stream")
        return ProviderReply(
            content="".join(content_parts),
            finish_reason="incomplete",
        )

    def _responses_regular_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Request a non-streaming Responses reply."""
        response = self._open(payload)
        try:
            # Some gateways ignore `stream: false` and answer with SSE anyway,
            # so the content type, not the request flag, selects the parser.
            if is_event_stream(response):
                return self._parse_responses_sse_body(
                    response, on_text=on_text, on_thinking=on_thinking
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError("provider returned invalid JSON") from exc
            except httpx2.HTTPError as exc:
                raise ProviderError(f"provider response read failed: {exc}") from exc
        finally:
            response.close()
        return self._parse_responses_payload(
            data, on_text=on_text, on_thinking=on_thinking
        )
