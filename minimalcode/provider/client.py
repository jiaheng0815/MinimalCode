"""Provider client shared by the Chat, Responses, and Anthropic mixins."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx2

from ..config import provider_api_key
from ..errors import ProviderError
from ..httpclient import build_client, build_headers
from .anthropic import AnthropicProtocolMixin
from .chat import ChatProtocolMixin
from .responses import ResponsesProtocolMixin
from .transport import TransportMixin
from .types import ProviderReply


class OpenAICompatibleProvider(
    TransportMixin,
    ChatProtocolMixin,
    ResponsesProtocolMixin,
    AnthropicProtocolMixin,
):
    """HTTP client for OpenAI Chat/Responses and Anthropic Messages APIs."""

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
        # Unknown names are rejected rather than defaulted to Chat: the dialects
        # need different payload keys and parsers, so a fallback would send a
        # request the server rejects or we misparse.
        if protocol not in {
            "openai_chat",
            "openai_responses",
            "anthropic_messages",
        }:
            raise ProviderError(
                f"provider {name!r} uses unsupported protocol {protocol!r}; "
                "expected 'openai_chat', 'openai_responses', or "
                "'anthropic_messages'"
            )
        self.name = name
        self.config = config
        self.model = model
        self.thinking = thinking
        self.protocol = protocol
        # `_endpoint` appends the protocol path, so a trailing slash here would
        # produce `//chat/completions` on servers that keep the path verbatim.
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        if not self.base_url:
            raise ProviderError(f"provider {name!r} requires base_url")
        try:
            parsed_base = urlsplit(self.base_url)
            hostname = parsed_base.hostname
            # Reading `.port` is deliberate: it raises on a malformed port here,
            # at construction, instead of on every request that follows.
            _ = parsed_base.port
        except ValueError as exc:
            raise ProviderError(f"provider {name!r} has an invalid base_url") from exc
        if parsed_base.scheme not in {"http", "https"} or not hostname:
            raise ProviderError(f"provider {name!r} has an invalid base_url")
        try:
            self.context_window = int(config.get("context_window", 128_000))
            self.timeout_seconds = float(config.get("timeout_seconds", 120))
            self.max_tokens = int(config.get("max_tokens", 8192))
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
        if self.max_tokens < 1:
            raise ProviderError(f"provider {name!r} requires a positive max_tokens")
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

        custom_headers = config.get("headers")
        custom_names = (
            {str(key).lower() for key in custom_headers}
            if isinstance(custom_headers, dict)
            else set()
        )
        headers = build_headers(api_key, custom_headers)
        if protocol == "anthropic_messages":
            # Anthropic authenticates with x-api-key; Authorization is accepted
            # only by some gateways (DeepSeek accepts both). A caller-supplied
            # auth header always wins over the one derived from api_key.
            if (
                api_key
                and "x-api-key" not in custom_names
                and "authorization" not in custom_names
            ):
                headers["x-api-key"] = api_key
            if "authorization" not in custom_names:
                headers.pop("Authorization", None)
            if "anthropic-version" not in custom_names:
                headers["anthropic-version"] = "2023-06-01"
        self.headers = headers
        self._client = build_client(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_seconds,
            transport=transport,
            trust_env=config.get("trust_env"),
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
        if self.protocol == "anthropic_messages":
            if self.base_url.endswith("/messages"):
                return self.base_url
            if self.base_url.endswith("/v1"):
                return f"{self.base_url}/messages"
            # DeepSeek's Anthropic base is https://api.deepseek.com/anthropic,
            # so the version segment is appended unless the caller supplied it.
            return f"{self.base_url}/v1/messages"
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
        on_thinking: Callable[[str], None] | None = None,
    ) -> ProviderReply:
        """Send one conversation turn using the configured wire protocol."""
        if self.protocol == "anthropic_messages":
            payload = self._anthropic_payload(messages, tools)
            if self.stream_enabled:
                return self._anthropic_stream_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
            return self._anthropic_regular_request(
                payload, on_text=on_text, on_thinking=on_thinking
            )
        if self.protocol == "openai_responses":
            payload = self._responses_payload(messages, tools)
            if self.stream_enabled:
                return self._responses_stream_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
            return self._responses_regular_request(
                payload, on_text=on_text, on_thinking=on_thinking
            )
        payload = self._chat_payload(messages, tools)
        if self.stream_enabled:
            return self._chat_stream_request(
                payload, on_text=on_text, on_thinking=on_thinking
            )
        return self._chat_regular_request(
            payload, on_text=on_text, on_thinking=on_thinking
        )

    def complete_text(self, messages: list[dict[str, Any]]) -> str:
        """Request a plain text completion, rejecting abnormal finish reasons."""
        reply = self.stream_chat(messages, tools=None, on_text=None)
        if reply.finish_reason not in {None, "stop", "completed"}:
            raise ProviderError(
                "provider stopped the summary with finish reason "
                f"{reply.finish_reason!r}"
            )
        return reply.content
