"""Provider client shared by the Chat and Responses protocol mixins."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx2

from .. import __version__
from ..config import provider_api_key
from ..errors import ProviderError
from .chat import ChatProtocolMixin
from .responses import ResponsesProtocolMixin
from .transport import TransportMixin
from .types import ProviderReply


class OpenAICompatibleProvider(
    TransportMixin,
    ChatProtocolMixin,
    ResponsesProtocolMixin,
):
    """HTTP client for OpenAI-compatible Chat Completions and Responses APIs."""

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
        # Optional sink for retry notices; the agent points this at its activity
        # stream so ten silent retries do not look like a hang.
        self.notify: Callable[[str], None] | None = None

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
        """Close the underlying HTTP client and its connection pool."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _endpoint(self) -> str:
        """Return the full protocol endpoint, accepting a full URL as-is."""
        if self.protocol == "openai_responses":
            if self.base_url.endswith("/responses"):
                return self.base_url
            return f"{self.base_url}/responses"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ProviderReply:
        """Send one conversation turn using the configured wire protocol."""
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
        """Request a plain text completion, rejecting abnormal finish reasons."""
        reply = self.stream_chat(messages, tools=None, on_text=None)
        if reply.finish_reason not in {None, "stop", "completed"}:
            raise ProviderError(
                "provider stopped the summary with finish reason "
                f"{reply.finish_reason!r}"
            )
        return reply.content
