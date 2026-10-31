"""Anthropic Messages payloads, streams, and response parsing."""

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
    stream_event_error,
    tool_arguments,
)
from .types import ProviderReply, ToolCall

# Anthropic requires 1024 <= budget_tokens < max_tokens. The effort names are
# MinimalCode's, so each one maps to a budget that fits the default 8192-token
# output cap; a configured max_tokens still wins when it is smaller.
_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 3072,
    "high": 4096,
    "xhigh": 5120,
    "ultra": 6144,
    "max": 7168,
}

_ANTHROPIC_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "max_tokens",
    # A paused server-tool turn cannot be continued by this agent loop.
    "pause_turn": "incomplete",
}


def _anthropic_usage(raw: Any) -> dict[str, int]:
    """Normalize Anthropic usage, counting cached input as prompt tokens."""
    if not isinstance(raw, dict):
        return {}
    try:
        input_tokens = int(raw.get("input_tokens") or 0)
        cache_creation = int(raw.get("cache_creation_input_tokens") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        output_tokens = int(raw.get("output_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        # Usage is diagnostic; a malformed object must not fail a turn.
        return {}
    prompt_tokens = input_tokens + cache_creation + cache_read
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
        "cached_tokens": cache_read,
        "reasoning_tokens": 0,
    }


class AnthropicProtocolMixin:
    """Anthropic Messages wire format for the provider client."""

    # Attributes supplied by OpenAICompatibleProvider.
    model: str
    thinking: str
    stream_enabled: bool
    send_reasoning_effort: bool
    max_tokens: int
    config: dict[str, Any]

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        raise NotImplementedError

    @staticmethod
    def _anthropic_finish(stop_reason: Any, has_calls: bool) -> str:
        """Map an Anthropic stop reason to the agent loop's vocabulary."""
        if stop_reason:
            value = str(stop_reason)
            return _ANTHROPIC_STOP_REASONS.get(value, value)
        return "tool_calls" if has_calls else "stop"

    @staticmethod
    def _anthropic_image_block(value: Any) -> dict[str, Any] | None:
        """Convert an OpenAI-style image URL into an Anthropic image source."""
        url = value
        if isinstance(value, dict):
            url = value.get("url")
        if not isinstance(url, str) or not url:
            return None
        if url.startswith("data:"):
            header, _, data = url.partition(",")
            media_type = header[5:].split(";", 1)[0]
            if not media_type or not data:
                return None
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }
        return {"type": "image", "source": {"type": "url", "url": url}}

    @classmethod
    def _anthropic_content_blocks(cls, content: Any) -> list[dict[str, Any]]:
        """Flatten stored string or OpenAI-style content into content blocks."""
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for part in content:
                if isinstance(part, str):
                    if part:
                        blocks.append({"type": "text", "text": part})
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text" and isinstance(part.get("text"), str):
                    if part["text"]:
                        blocks.append({"type": "text", "text": part["text"]})
                elif part_type == "image_url":
                    block = cls._anthropic_image_block(part.get("image_url"))
                    if block is not None:
                        blocks.append(block)
                elif part_type == "image" and isinstance(part.get("source"), dict):
                    blocks.append({"type": "image", "source": part["source"]})
                else:
                    text = content_text(part)
                    if text:
                        blocks.append({"type": "text", "text": text})
            return blocks
        if content is None:
            return []
        text = content_text(content)
        return [{"type": "text", "text": text}] if text else []

    @classmethod
    def _anthropic_tool_input(cls, arguments: Any) -> dict[str, Any]:
        """Parse stored tool arguments into the object Anthropic expects."""
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _anthropic_assistant_blocks(
        cls,
        message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build an assistant content list, preferring preserved raw blocks."""
        raw_blocks = message.get("provider_content")
        raw_calls = message.get("tool_calls")
        if isinstance(raw_blocks, list):
            preserved = [
                dict(block)
                for block in raw_blocks
                if isinstance(block, dict)
                and block.get("type")
                in {"text", "thinking", "redacted_thinking", "tool_use"}
            ]
            # Reasoning is only replayed verbatim when every call it belongs to
            # is still there; a partial block list would orphan a tool_result.
            expected = {
                str(call.get("id") or "")
                for call in raw_calls or []
                if isinstance(call, dict)
            }
            replayed = {
                str(block.get("id") or "")
                for block in preserved
                if block.get("type") == "tool_use"
            }
            if preserved and expected <= replayed:
                return preserved

        blocks = cls._anthropic_content_blocks(message.get("content"))
        if not isinstance(raw_calls, list):
            return blocks
        for index, call in enumerate(raw_calls):
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if not name:
                continue
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id") or f"toolu_{index}"),
                    "name": name,
                    "input": cls._anthropic_tool_input(function.get("arguments")),
                }
            )
        return blocks

    @staticmethod
    def _merge_user_blocks(
        existing: list[dict[str, Any]],
        added: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge user blocks with every tool_result ahead of text."""
        merged = [*existing, *added]
        results = [block for block in merged if block.get("type") == "tool_result"]
        others = [block for block in merged if block.get("type") != "tool_result"]
        return [*results, *others]

    def _anthropic_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Split system text and convert stored history into Messages turns."""
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role in {"system", "developer"}:
                text = content_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue

            if role == "tool":
                content = message.get("content")
                blocks = self._anthropic_content_blocks(content)
                tool_result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": blocks or "",
                }
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict) and parsed.get("status") == "error":
                        tool_result["is_error"] = True
                if converted and converted[-1].get("role") == "user":
                    converted[-1]["content"] = self._merge_user_blocks(
                        converted[-1]["content"], [tool_result]
                    )
                else:
                    converted.append({"role": "user", "content": [tool_result]})
                continue

            if role == "assistant":
                blocks = self._anthropic_assistant_blocks(message)
            elif role == "user":
                blocks = self._anthropic_content_blocks(message.get("content"))
            else:
                continue
            if not blocks:
                continue
            if converted and converted[-1].get("role") == role:
                if role == "user":
                    converted[-1]["content"] = self._merge_user_blocks(
                        converted[-1]["content"], blocks
                    )
                else:
                    converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": blocks})

        return system_parts, converted

    @staticmethod
    def _anthropic_tools(
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """Convert Chat-style function tools into Anthropic tool definitions."""
        if not tools:
            return None
        result: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if not name:
                continue
            definition: dict[str, Any] = {
                "name": name,
                "input_schema": function.get("parameters")
                if isinstance(function.get("parameters"), dict)
                else {"type": "object"},
            }
            description = function.get("description")
            if isinstance(description, str) and description:
                definition["description"] = description
            result.append(definition)
        return result or None

    def _anthropic_thinking(self) -> dict[str, Any] | None:
        """Translate the configured effort into an Anthropic thinking object."""
        effort = self.thinking.strip().lower()
        if effort == "none":
            return {"type": "disabled"}
        if not effort:
            return None
        if self.max_tokens <= 1024:
            raise ProviderError(
                "Anthropic thinking requires max_tokens greater than 1024"
            )
        budget = _THINKING_BUDGETS.get(effort, 4096)
        budget = max(1024, min(budget, self.max_tokens - 1))
        return {"type": "enabled", "budget_tokens": budget}

    def _anthropic_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the Anthropic Messages request body."""
        system_parts, converted = self._anthropic_messages(messages)
        if not converted:
            raise ProviderError("provider request requires at least one message")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
            "stream": self.stream_enabled,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        converted_tools = self._anthropic_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
        if self.send_reasoning_effort:
            thinking = self._anthropic_thinking()
            if thinking is not None:
                payload["thinking"] = thinking
        extra = self.config.get("extra_body")
        if isinstance(extra, dict):
            # Never let extra_body override protocol-critical fields.
            reserved = {
                "model",
                "max_tokens",
                "messages",
                "system",
                "stream",
                "tools",
                "tool_choice",
                "thinking",
            }
            payload.update(
                {key: value for key, value in extra.items() if key not in reserved}
            )
        return payload

    def _anthropic_stream_request(
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
                    return self._parse_anthropic_payload(
                        data, on_text=on_text, on_thinking=on_thinking
                    )
                text = body.decode("utf-8", errors="replace")
                if "data:" not in text:
                    raise ProviderError("provider returned an invalid response")
                lines: Iterable[str] = text.splitlines()
            else:
                lines = response.iter_lines()
            return self._parse_anthropic_stream(
                lines, on_text=on_text, on_thinking=on_thinking
            )
        finally:
            response.close()

    @staticmethod
    def _event_index(event: dict[str, Any]) -> int:
        try:
            return int(event.get("index", -1))
        except (TypeError, ValueError):
            return -1

    def _parse_anthropic_stream(
        self,
        lines: Iterable[str],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Accumulate Anthropic SSE events into one normalized reply."""
        content_parts: list[str] = []
        # Content arrives under an index: text and thinking deltas append to the
        # open block, while tool input arrives as a partial JSON string.
        blocks: dict[int, dict[str, Any]] = {}
        stop_reason: Any = None
        saw_message_stop = False
        usage_raw: dict[str, Any] = {}

        def state_for(index: int, block_type: str) -> dict[str, Any]:
            state = blocks.get(index)
            if state is None:
                state = {
                    "type": block_type,
                    "text": "",
                    "thinking": "",
                    "signature": "",
                    "id": "",
                    "name": "",
                    "input": {},
                    "partial_json": "",
                    "data": "",
                }
                blocks[index] = state
            return state

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
                if event_type == "error" or (not event_type and event.get("error")):
                    raise stream_event_error(event)

                if event_type == "message_start":
                    message = event.get("message")
                    if isinstance(message, dict) and isinstance(
                        message.get("usage"), dict
                    ):
                        usage_raw.update(message["usage"])
                    continue
                if event_type == "content_block_start":
                    index = self._event_index(event)
                    block = event.get("content_block")
                    if index < 0 or not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type not in {
                        "text",
                        "thinking",
                        "redacted_thinking",
                        "tool_use",
                    }:
                        # Server tools are executed provider-side and cannot be
                        # replayed as client content in the next request.
                        continue
                    state = state_for(index, block_type)
                    state["type"] = block_type
                    if block_type == "redacted_thinking":
                        # Anthropic requires this opaque block to be replayed
                        # verbatim while a tool call is open.
                        data = block.get("data")
                        if isinstance(data, str):
                            state["data"] += data
                    elif block_type == "text":
                        text = content_text(block.get("text"))
                        if text:
                            state["text"] += text
                            content_parts.append(text)
                            if on_text is not None:
                                on_text(text)
                    elif block_type == "thinking":
                        thinking = content_text(block.get("thinking"))
                        if thinking:
                            state["thinking"] += thinking
                            if on_thinking is not None:
                                on_thinking(thinking)
                        if isinstance(block.get("signature"), str):
                            state["signature"] += block["signature"]
                    else:
                        state["id"] = str(block.get("id") or "")
                        state["name"] = str(block.get("name") or "")
                        if isinstance(block.get("input"), dict):
                            state["input"] = block["input"]
                    continue
                if event_type == "content_block_delta":
                    index = self._event_index(event)
                    delta = event.get("delta")
                    if index < 0 or not isinstance(delta, dict):
                        continue
                    delta_type = str(delta.get("type") or "")
                    if delta_type == "text_delta":
                        text = content_text(delta.get("text"))
                        if text:
                            state = state_for(index, "text")
                            state["text"] += text
                            content_parts.append(text)
                            if on_text is not None:
                                on_text(text)
                    elif delta_type == "thinking_delta":
                        thinking = content_text(delta.get("thinking"))
                        if thinking:
                            state = state_for(index, "thinking")
                            state["thinking"] += thinking
                            if on_thinking is not None:
                                on_thinking(thinking)
                    elif delta_type == "signature_delta":
                        signature = delta.get("signature")
                        if isinstance(signature, str):
                            state_for(index, "thinking")["signature"] += signature
                    elif delta_type == "input_json_delta":
                        partial = delta.get("partial_json")
                        if isinstance(partial, str):
                            state_for(index, "tool_use")["partial_json"] += partial
                    continue
                if event_type == "content_block_stop":
                    continue
                if event_type == "message_delta":
                    delta = event.get("delta")
                    if isinstance(delta, dict) and delta.get("stop_reason"):
                        stop_reason = delta["stop_reason"]
                    # The final message_delta usage is cumulative; message_start
                    # only carries the initial input counts.
                    if isinstance(event.get("usage"), dict):
                        usage_raw.update(event["usage"])
                    continue
                if event_type == "message_stop":
                    saw_message_stop = True
        except httpx2.HTTPError as exc:
            raise ProviderError(f"provider stream interrupted: {exc}") from exc

        tool_calls: list[ToolCall] = []
        provider_content: list[dict[str, Any]] = []
        for index in sorted(blocks):
            state = blocks[index]
            block_type = state["type"]
            if block_type == "text":
                if state["text"]:
                    provider_content.append({"type": "text", "text": state["text"]})
                continue
            if block_type == "redacted_thinking":
                if state["data"]:
                    provider_content.append(
                        {"type": "redacted_thinking", "data": state["data"]}
                    )
                continue
            if block_type == "thinking":
                if not state["thinking"] and not state["signature"]:
                    continue
                block: dict[str, Any] = {
                    "type": "thinking",
                    "thinking": state["thinking"],
                }
                if state["signature"]:
                    block["signature"] = state["signature"]
                provider_content.append(block)
                continue
            if block_type != "tool_use" or not state["name"]:
                continue
            tool_input = state["input"]
            arguments_text = tool_arguments(tool_input)
            if state["partial_json"]:
                try:
                    parsed = json.loads(state["partial_json"])
                except json.JSONDecodeError:
                    # Keep the malformed fragment so the tool layer reports an
                    # invalid-JSON result instead of executing empty arguments.
                    arguments_text = state["partial_json"]
                else:
                    if isinstance(parsed, dict):
                        tool_input = parsed
                        arguments_text = tool_arguments(tool_input)
                    else:
                        arguments_text = state["partial_json"]
            if not isinstance(tool_input, dict):
                tool_input = {}
            call_id = state["id"] or f"toolu_{index}"
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=state["name"],
                    arguments=arguments_text,
                )
            )
            provider_content.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": state["name"],
                    "input": tool_input,
                }
            )

        if not saw_message_stop:
            # Anthropic closes an interrupted response without message_stop;
            # reporting a clean stop could execute a half-received tool call.
            finish_reason = "incomplete"
        else:
            finish_reason = self._anthropic_finish(stop_reason, bool(tool_calls))
        if not content_parts and not tool_calls and not saw_message_stop:
            raise ProviderError("provider returned an empty stream")
        return ProviderReply(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=_anthropic_usage(usage_raw),
            # Thinking signatures are only replayed while a tool call is open;
            # a finished answer does not need them in the session history.
            provider_content=provider_content if tool_calls else None,
        )

    def _parse_anthropic_payload(
        self,
        data: Any,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Parse one non-streaming Anthropic Messages response body."""
        if not isinstance(data, dict):
            raise ProviderError("provider response must be an object")
        if data.get("type") == "error" or data.get("error"):
            detail = data.get("error") or "unknown error"
            raise ProviderError(f"provider response failed: {detail}")
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderError("provider response has no content")

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        provider_content: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = content_text(block.get("text"))
                if text:
                    content_parts.append(text)
                    provider_content.append({"type": "text", "text": text})
                    if on_text is not None:
                        on_text(text)
            elif block_type == "redacted_thinking":
                data = block.get("data")
                if isinstance(data, str) and data:
                    provider_content.append({"type": "redacted_thinking", "data": data})
            elif block_type == "thinking":
                thinking = content_text(block.get("thinking"))
                if thinking and on_thinking is not None:
                    on_thinking(thinking)
                preserved: dict[str, Any] = {
                    "type": "thinking",
                    "thinking": thinking,
                }
                if isinstance(block.get("signature"), str) and block["signature"]:
                    preserved["signature"] = block["signature"]
                provider_content.append(preserved)
            elif block_type == "tool_use":
                name = str(block.get("name") or "")
                if not name:
                    continue
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = {}
                call_id = str(block.get("id") or f"toolu_{len(tool_calls)}")
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=name,
                        arguments=tool_arguments(tool_input),
                    )
                )
                provider_content.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": tool_input,
                    }
                )
        return ProviderReply(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=self._anthropic_finish(
                data.get("stop_reason"), bool(tool_calls)
            ),
            usage=_anthropic_usage(data.get("usage")),
            provider_content=provider_content if tool_calls else None,
        )

    def _anthropic_regular_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Request a non-streaming reply, accepting SSE if the server sends it."""
        response = self._open(payload)
        try:
            if is_event_stream(response):
                return self._parse_anthropic_stream(
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
        return self._parse_anthropic_payload(
            data, on_text=on_text, on_thinking=on_thinking
        )
