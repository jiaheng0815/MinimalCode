from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx2

from . import __version__
from .config import Settings, provider_api_key
from .errors import ProviderError


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class ProviderReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for call in self.tool_calls
            ]
        return message


def _normalize_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("prompt_tokens", raw.get("input_tokens", 0))
    completion = raw.get("completion_tokens", raw.get("output_tokens", 0))
    total = raw.get("total_tokens", 0)
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
    completion_details = raw.get("completion_tokens_details") or raw.get(
        "output_tokens_details"
    )
    cached = (
        prompt_details.get("cached_tokens", 0)
        if isinstance(prompt_details, dict)
        else 0
    )
    reasoning = (
        completion_details.get("reasoning_tokens", 0)
        if isinstance(completion_details, dict)
        else 0
    )
    try:
        prompt_int = int(prompt or 0)
        completion_int = int(completion or 0)
        total_int = int(total or prompt_int + completion_int)
        cached_int = int(cached or 0)
        reasoning_int = int(reasoning or 0)
    except (TypeError, ValueError):
        return {}
    return {
        "prompt_tokens": prompt_int,
        "completion_tokens": completion_int,
        "total_tokens": total_int,
        "cached_tokens": cached_int,
        "reasoning_tokens": reasoning_int,
    }


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _tool_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value or "{}"
    if value is None:
        return "{}"
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _is_event_stream(response: httpx2.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return "text/event-stream" in content_type


def _decode_json_body(body: bytes) -> tuple[bool, Any]:
    try:
        return True, json.loads(body)
    except ValueError:
        return False, None


def _stream_event_error(event: dict[str, Any]) -> ProviderError:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error
    else:
        message = error or event
    return ProviderError(f"provider stream returned an error: {message}")


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        config: dict[str, Any],
        model: str,
        thinking: str,
        transport: httpx2.BaseTransport | None = None,
    ):
        protocol = str(config.get("protocol", "openai_chat"))
        if protocol not in {"openai_chat", "openai_responses"}:
            raise ProviderError(
                f"provider {name!r} uses unsupported protocol {protocol!r}; "
                "expected 'openai_chat' or 'openai_responses'"
            )
        self.name = name
        self.config = config
        self.model = model
        self.thinking = thinking
        self.protocol = protocol
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        if not self.base_url:
            raise ProviderError(f"provider {name!r} requires base_url")
        try:
            parsed_base = urlsplit(self.base_url)
            hostname = parsed_base.hostname
            _ = parsed_base.port
        except ValueError as exc:
            raise ProviderError(f"provider {name!r} has an invalid base_url") from exc
        if parsed_base.scheme not in {"http", "https"} or not hostname:
            raise ProviderError(f"provider {name!r} has an invalid base_url")
        try:
            self.context_window = int(config.get("context_window", 128_000))
            self.timeout_seconds = float(config.get("timeout_seconds", 120))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderError(
                f"provider {name!r} has invalid numeric settings"
            ) from exc
        if self.context_window < 1:
            raise ProviderError(f"provider {name!r} requires a positive context_window")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ProviderError(
                f"provider {name!r} requires a positive timeout_seconds"
            )
        self.stream_enabled = bool(config.get("stream", True))
        self.include_usage = bool(config.get("include_usage", True))
        self.send_reasoning_effort = bool(config.get("send_reasoning_effort", False))

        api_key = provider_api_key(config)
        key_env = config.get("api_key_env")
        if not api_key and key_env and config.get("require_api_key", True):
            raise ProviderError(
                f"provider {name!r} requires API key in environment variable {key_env}"
            )

        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"MinimalCode/{__version__}",
        }
        custom_headers = config.get("headers")
        if isinstance(custom_headers, dict):
            headers.update(
                {str(key): str(value) for key, value in custom_headers.items()}
            )
        if api_key and not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {api_key}"
        self.headers = headers
        timeout = httpx2.Timeout(
            self.timeout_seconds, connect=min(20.0, self.timeout_seconds)
        )
        self._client = httpx2.Client(
            timeout=timeout,
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _endpoint(self) -> str:
        if self.protocol == "openai_responses":
            if self.base_url.endswith("/responses"):
                return self.base_url
            return f"{self.base_url}/responses"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _chat_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
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
            reserved = {"model", "messages", "stream", "tools", "tool_choice"}
            payload.update(
                {key: value for key, value in extra.items() if key not in reserved}
            )
        return payload

    def _responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                            "arguments": _tool_arguments(function.get("arguments")),
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
            reserved = {"model", "input", "stream", "tools", "tool_choice"}
            payload.update(
                {key: value for key, value in extra.items() if key not in reserved}
            )
        return payload

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ProviderReply:
        if self.protocol == "openai_responses":
            payload = self._responses_payload(messages, tools)
            if self.stream_enabled:
                return self._responses_stream_request(payload, on_text=on_text)
            return self._responses_regular_request(payload, on_text=on_text)
        payload = self._chat_payload(messages, tools)
        if self.stream_enabled:
            return self._chat_stream_request(payload, on_text=on_text)
        return self._chat_regular_request(payload, on_text=on_text)

    def complete_text(self, messages: list[dict[str, Any]]) -> str:
        reply = self.stream_chat(messages, tools=None, on_text=None)
        return reply.content

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        last_error: ProviderError | None = None
        for attempt in range(2):
            try:
                request = self._client.build_request(
                    "POST", self._endpoint(), json=payload
                )
                response = self._client.send(
                    request, stream=bool(payload.get("stream"))
                )
            except (httpx2.HTTPError, httpx2.InvalidURL, ValueError) as exc:
                last_error = ProviderError(f"provider network error: {exc}")
                if attempt:
                    break
                time.sleep(0.5)
                continue
            if response.status_code < 400:
                return response

            status_code = response.status_code
            # Headers must be read before the body is consumed and closed.
            retry_after = response.headers.get("Retry-After", "1")
            try:
                body = response.read().decode("utf-8", errors="replace")[:2000]
            except httpx2.HTTPError as exc:
                body = ""
                last_error = ProviderError(
                    f"provider network error while reading response: {exc}",
                    status_code=status_code,
                )
            else:
                last_error = ProviderError(
                    f"provider returned HTTP {status_code}: {body}",
                    status_code=status_code,
                    body=body,
                )
            finally:
                response.close()
            if status_code not in {429, 500, 502, 503, 504} or attempt:
                break
            try:
                delay = min(max(float(retry_after), 0.2), 5.0)
            except ValueError:
                delay = 1.0
            time.sleep(delay)
        if last_error is None:  # pragma: no cover - defensive
            raise ProviderError("provider request failed")
        raise last_error

    def _chat_stream_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
    ) -> ProviderReply:
        response = self._open(payload)
        try:
            if not _is_event_stream(response):
                try:
                    body = response.read()
                except httpx2.HTTPError as exc:
                    raise ProviderError(f"provider stream interrupted: {exc}") from exc
                parsed, data = _decode_json_body(body)
                if parsed:
                    return self._parse_chat_payload(data, on_text=on_text)
                text = body.decode("utf-8", errors="replace")
                if "data:" not in text:
                    raise ProviderError("provider returned an invalid response")
                lines: Iterable[str] = text.splitlines()
            else:
                lines = response.iter_lines()
            return self._parse_chat_stream(lines, on_text=on_text)
        finally:
            response.close()

    def _parse_chat_stream(
        self,
        lines: Iterable[str],
        *,
        on_text: Callable[[str], None] | None,
    ) -> ProviderReply:
        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}

        try:
            for line in lines:
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
                if event.get("error"):
                    raise _stream_event_error(event)

                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    parsed_usage = _normalize_usage(raw_usage)
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
                text = _content_text(delta.get("content"))
                if text:
                    content_parts.append(text)
                    if on_text is not None:
                        on_text(text)

                raw_calls = delta.get("tool_calls")
                if isinstance(raw_calls, list):
                    for raw_call in raw_calls:
                        if not isinstance(raw_call, dict):
                            continue
                        try:
                            index = int(raw_call.get("index", len(calls)))
                        except (TypeError, ValueError):
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
                                entry["arguments"] = _tool_arguments(arguments)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
        except httpx2.HTTPError as exc:
            raise ProviderError(f"provider stream interrupted: {exc}") from exc

        tool_calls: list[ToolCall] = []
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
        if not content_parts and not tool_calls and finish_reason is None:
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
    ) -> ProviderReply:
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
        content = _content_text(message.get("content"))
        if content and on_text is not None:
            on_text(content)
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
                        arguments=_tool_arguments(function.get("arguments")),
                    )
                )
        finish_reason = choice.get("finish_reason")
        return ProviderReply(
            content=content,
            tool_calls=calls,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            usage=_normalize_usage(data.get("usage")),
        )

    def _chat_regular_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
    ) -> ProviderReply:
        response = self._open(payload)
        try:
            if _is_event_stream(response):
                return self._parse_chat_stream(response.iter_lines(), on_text=on_text)
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError("provider returned invalid JSON") from exc
            except httpx2.HTTPError as exc:
                raise ProviderError(f"provider response read failed: {exc}") from exc
        finally:
            response.close()
        return self._parse_chat_payload(data, on_text=on_text)

    @staticmethod
    def _parse_responses_output(
        output: Any,
    ) -> tuple[str, list[ToolCall]]:
        if not isinstance(output, list):
            return "", []
        content_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                text = _content_text(item.get("content"))
                if text:
                    content_parts.append(text)
            elif item_type in {"output_text", "text"}:
                text = _content_text(item)
                if text:
                    content_parts.append(text)
            elif item_type == "function_call":
                name = str(item.get("name") or "")
                if name:
                    calls.append(
                        ToolCall(
                            id=str(item.get("call_id") or item.get("id") or ""),
                            name=name,
                            arguments=_tool_arguments(item.get("arguments")),
                        )
                    )
        for index, call in enumerate(calls):
            if not call.id:
                call.id = f"call_{index}"
        return "".join(content_parts), calls

    @staticmethod
    def _response_finish(response: dict[str, Any], has_calls: bool) -> str:
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
    ) -> ProviderReply:
        content_parts: list[str] = []
        fallback_text: list[str] = []
        call_states: dict[str, dict[str, str]] = {}
        output_keys: dict[str, str] = {}
        final_response: dict[str, Any] = {}
        usage: dict[str, int] = {}

        def state_for(key: str) -> dict[str, str]:
            return call_states.setdefault(
                key,
                {"id": "", "name": "", "arguments": "", "index": str(len(call_states))},
            )

        def update_call(item: dict[str, Any], key: str) -> None:
            state = state_for(key)
            if item.get("call_id"):
                state["id"] = str(item["call_id"])
            elif item.get("id"):
                state["id"] = str(item["id"])
            if item.get("name"):
                state["name"] = str(item["name"])
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                if arguments or not state["arguments"]:
                    state["arguments"] = arguments
            elif isinstance(arguments, dict):
                state["arguments"] = _tool_arguments(arguments)

        def add_fallback_text(text: str) -> None:
            if text and text not in fallback_text:
                fallback_text.append(text)

        response = self._open(payload)
        try:
            if not _is_event_stream(response):
                try:
                    body = response.read()
                except httpx2.HTTPError as exc:
                    raise ProviderError(f"provider stream interrupted: {exc}") from exc
                parsed, data = _decode_json_body(body)
                if parsed:
                    return self._parse_responses_payload(data, on_text=on_text)
                text = body.decode("utf-8", errors="replace")
                if "data:" not in text:
                    raise ProviderError("provider returned an invalid response")
                lines: Iterable[str] = text.splitlines()
            else:
                lines = response.iter_lines()
            try:
                for line in lines:
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
                        text = _content_text(event.get("delta"))
                        if text:
                            content_parts.append(text)
                            if on_text is not None:
                                on_text(text)
                        continue
                    if event_type == "response.output_text.done":
                        add_fallback_text(_content_text(event.get("text")))
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
                            state["arguments"] = _tool_arguments(delta)
                        continue
                    if event_type == "response.function_call_arguments.done":
                        raw_key = str(
                            event.get("item_id") or event.get("output_index", "0")
                        )
                        key = output_keys.get(raw_key, raw_key)
                        state = state_for(key)
                        arguments = event.get("arguments")
                        if isinstance(arguments, str) and (
                            arguments or not state["arguments"]
                        ):
                            state["arguments"] = arguments
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
                            add_fallback_text(_content_text(item.get("content")))
                        continue
                    if event_type in {"response.completed", "response.incomplete"}:
                        completed = event.get("response")
                        if isinstance(completed, dict):
                            final_response = completed
                            parsed_usage = _normalize_usage(completed.get("usage"))
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

        finish_reason = (
            self._response_finish(final_response, bool(tool_calls))
            if final_response
            else ("tool_calls" if tool_calls else "incomplete_stream")
        )
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
    ) -> ProviderReply:
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
        return ProviderReply(
            content=content,
            tool_calls=calls,
            finish_reason=self._response_finish(data, bool(calls)),
            usage=_normalize_usage(data.get("usage")),
        )

    def _responses_regular_request(
        self,
        payload: dict[str, Any],
        *,
        on_text: Callable[[str], None] | None,
    ) -> ProviderReply:
        response = self._open(payload)
        try:
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError("provider returned invalid JSON") from exc
            except httpx2.HTTPError as exc:
                raise ProviderError(f"provider response read failed: {exc}") from exc
        finally:
            response.close()
        return self._parse_responses_payload(data, on_text=on_text)


def create_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> tuple[OpenAICompatibleProvider, str, str]:
    name, config = settings.provider_config(provider_name)
    provider_default_model = config.get("model")
    effective_model = (
        model
        or (str(provider_default_model) if provider_default_model else None)
        or settings.effective_model()
    )
    effective_thinking = thinking or str(settings.get("thinking", "high"))
    provider = OpenAICompatibleProvider(
        name=name,
        config=config,
        model=effective_model,
        thinking=effective_thinking,
        transport=transport,
    )
    return provider, name, effective_model
