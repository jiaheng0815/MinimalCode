"""Shared parsing helpers for OpenAI-compatible payloads and streams."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx2

from ..errors import ProviderError


def _is_json(text: str) -> bool:
    """Return True when ``text`` is a complete JSON value."""
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    """Yield one payload per SSE event, joining multi-line `data:` fields.

    The event stream format allows a single event's payload to be split across
    several `data:` lines. Parsing one line at a time made `json.loads` fail on
    every fragment and dropped the event silently, so a gateway that wraps long
    JSON produced an empty reply with no error at all.

    Two framings are in the wild: a blank line terminates the event, and some
    gateways simply put one complete payload on each `data:` line with no blank
    separator. A buffer that already parses as complete JSON is therefore
    yielded before the next line is appended.
    """
    buffer = ""
    for raw in lines:
        line = strip_sse_bom(raw).rstrip("\r\n")
        if not line.strip():
            if buffer:
                yield buffer
                buffer = ""
            continue
        if not line.startswith("data:"):
            # `event:`, `id:`, `retry:`, and comments carry no JSON payload.
            continue
        payload = line[5:].removeprefix(" ")
        if not payload:
            continue
        if buffer and _is_json(buffer):
            yield buffer
            buffer = ""
        buffer = f"{buffer}\n{payload}" if buffer else payload
    if buffer:
        yield buffer


_USAGE_KEYS = frozenset(
    {
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "total_tokens",
    }
)


def normalize_usage(raw: Any) -> dict[str, int]:
    """Normalize Chat and Responses usage objects to one stable key set."""
    if not isinstance(raw, dict) or not any(key in raw for key in _USAGE_KEYS):
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
    except (TypeError, ValueError, OverflowError):
        # Usage is diagnostic only; a malformed object (including a non-finite
        # JSON number such as Infinity) must not fail a turn.
        return {}
    return {
        "prompt_tokens": prompt_int,
        "completion_tokens": completion_int,
        "total_tokens": total_int,
        "cached_tokens": cached_int,
        "reasoning_tokens": reasoning_int,
    }


def content_text(value: Any) -> str:
    """Flatten string, object, or multimodal-part content into plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        refusal = value.get("refusal")
        return refusal if isinstance(refusal, str) else ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("refusal"), str):
                    parts.append(item["refusal"])
        # No separator is inserted because this same helper flattens streaming
        # deltas and finished messages; adding one would make the reassembled
        # text differ from what the model actually produced.
        return "".join(parts)
    return ""


def tool_arguments(value: Any) -> str:
    """Return tool arguments as the JSON string the message format requires."""
    # The wire format wants a JSON *string*, and "" is what models emit for a
    # no-argument call; leaving it empty would fail json.loads on the next turn.
    if isinstance(value, str):
        return value or "{}"
    if value is None:
        return "{}"
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def strip_sse_bom(line: str) -> str:
    """Remove a UTF-8 BOM that a gateway left at the start of an SSE line."""
    return line.lstrip("\ufeff") if line else line


def is_event_stream(response: httpx2.Response) -> bool:
    """Return True when the response advertises server-sent events."""
    content_type = response.headers.get("content-type", "").lower()
    return "text/event-stream" in content_type


def decode_json_body(body: bytes) -> tuple[bool, Any]:
    """Attempt to decode a non-streaming response body."""
    try:
        return True, json.loads(body)
    except ValueError:
        return False, None


def stream_event_error(event: dict[str, Any]) -> ProviderError:
    """Build a ProviderError from an SSE ``error`` event."""
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error
    else:
        message = error or event
    return ProviderError(f"provider stream returned an error: {message}")
