"""Shared parsing helpers for OpenAI-compatible payloads and streams."""

from __future__ import annotations

import json
from typing import Any

import httpx2

from ..errors import ProviderError


def normalize_usage(raw: Any) -> dict[str, int]:
    """Normalize Chat and Responses usage objects to one stable key set."""
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
        # Usage is diagnostic only; a malformed object must not fail a turn.
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


def tool_arguments(value: Any) -> str:
    """Return tool arguments as the JSON string the message format requires."""
    if isinstance(value, str):
        return value or "{}"
    if value is None:
        return "{}"
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


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
