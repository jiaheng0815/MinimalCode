"""Provider client shared by the Chat, Responses, Anthropic, and web mixins."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx2

from ..config import provider_api_key
from ..errors import ProviderError
from ..httpclient import build_client, build_headers
from .anthropic import AnthropicProtocolMixin
from .chat import ChatProtocolMixin
from .deepseek_web import CHAT_PATH, DeepSeekWebProtocolMixin
from .responses import ResponsesProtocolMixin
from .transport import TransportMixin
from .types import ProviderReply


class OpenAICompatibleProvider(
    TransportMixin,
    ChatProtocolMixin,
    ResponsesProtocolMixin,
    AnthropicProtocolMixin,
    DeepSeekWebProtocolMixin,
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
            "deepseek_web",
        }:
            raise ProviderError(
                f"provider {name!r} uses unsupported protocol {protocol!r}; "
                "expected 'openai_chat', 'openai_responses', "
                "'anthropic_messages', or 'deepseek_web'"
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
        lower_name = name.lower()
        lowered_host = hostname.lower()
        deepseek_api = (
            lower_name == "deepseek"
            or lower_name.startswith("deepseek-")
            or lowered_host == "deepseek.com"
            or lowered_host.endswith(".deepseek.com")
            or any(
                key in config
                for key in ("session_title", "safety_check", "latest_reminder")
            )
        )
        # DeepSeek reserves `find` when any of its server-side tools is active,
        # including the implicit tools used by `title` and `safety_check`.
        # Keep the public tool name stable for tools.allow, dispatch, and
        # sessions, but use a non-reserved name on every wire representation.
        self._tool_aliases = (
            {"find": "minimalcode_find"}
            if deepseek_api and protocol != "deepseek_web"
            else {}
        )
        self._reverse_tool_aliases = {
            wire: canonical for canonical, wire in self._tool_aliases.items()
        }
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
        # DeepSeek request extensions, opt-in per provider preset: strict
        # OpenAI-compatible servers reject unknown request fields, so a preset
        # turns them on only where the endpoint documents them. Built here once
        # so the three payload builders cannot drift from each other.
        title_config = config.get("session_title")
        self.title_enabled = bool(
            isinstance(title_config, dict) and title_config.get("enabled") is True
        )
        safety_config = config.get("safety_check")
        self.safety_level = (
            str(safety_config.get("level") or "low")
            if isinstance(safety_config, dict)
            else "low"
        )
        self.latest_reminder_enabled = bool(config.get("latest_reminder", False))
        extensions: dict[str, Any] = {}
        if self.title_enabled:
            # The provider appends a short conversation title to the answer, so
            # the agent can store it without a second request.
            extensions["title"] = {"enabled": True}
        if self.safety_level == "high":
            # Server-side input/output risk control; a refusal ends the turn.
            extensions["safety_check"] = {"level": "high"}
        self.extensions = extensions
        # DeepSeek's server-side web tools, document parsing, and the safety
        # review exist only on the documented API endpoint, so one predicate
        # decides and nothing else re-derives the answer from a provider name.
        from ..deepseek import features as deepseek_features
        from ..deepseek import server_tools as deepseek_server_tools

        self.deepseek = deepseek_features(config)
        self.server_tools = deepseek_server_tools(config)
        self._on_server_tool: Callable[[dict[str, Any]], None] | None = None
        self._server_steps: list[dict[str, Any]] = []
        self._server_citations: list[dict[str, Any]] = []
        # Raw Messages blocks before they are merged into calls plus results.
        self._server_blocks_raw: list[dict[str, Any]] = []
        self._server_citations: list[dict[str, Any]] = []
        self.server_tool_events = True
        # Optional sink for retry notices; the agent points this at its activity
        # stream so ten silent retries do not look like a hang.
        self.notify: Callable[[str], None] | None = None
        api_key = provider_api_key(config)
        key_env = config.get("api_key_env")
        if not api_key and key_env and config.get("require_api_key", True):
            raise ProviderError(
                f"provider {name!r} requires API key in environment variable {key_env}"
            )

        if protocol == "deepseek_web" and api_key:
            # The browser sends `Bearer <token>`, but people paste the whole
            # header value into DEEPSEEK_WEB_TOKEN, which produced
            # `Authorization: Bearer Bearer <token>` and a 401.
            stripped = api_key.strip()
            if stripped.lower().startswith("bearer "):
                stripped = stripped[len("bearer ") :].strip()
            api_key = stripped or None
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
            if self.deepseek["documents"] and "anthropic-beta" not in custom_names:
                # Messages only accepts a `file_id` document block on this beta.
                headers["anthropic-beta"] = "files-api-2025-04-14"
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

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def set_title_request(self, enabled: bool) -> None:
        """Ask for, or stop asking for, a conversation title on the next turn.

        The provider generates the title alongside the answer, so only the first
        request of a session asks for one: after that the session already has a
        name, and the extra wait before the response ends buys nothing.
        """
        self.extensions.pop("title", None)
        if enabled and self.title_enabled:
            self.extensions["title"] = {"enabled": True}

    def set_safety_level(self, level: str) -> None:
        """Turn the server-side content review on (``high``) or off (``low``).

        ``low`` is the API default, so the extension is dropped instead of being
        sent: an unknown key on a strict gateway is a rejected request, and the
        live switch has to work without rebuilding the provider.
        """
        if level not in {"low", "high"}:
            raise ProviderError(f"invalid safety level {level!r}")
        self.safety_level = level
        self.extensions.pop("safety_check", None)
        if level == "high" and self.deepseek["available"]:
            self.extensions["safety_check"] = {"level": "high"}

    def set_server_tools(self, names: tuple[str, ...] | list[str] | None) -> None:
        """Enable the server-side web tools for the next turns.

        Refused when the endpoint is not the DeepSeek API: the block would be
        rejected by any other server, so failing here keeps the reason next to
        the switch that caused it.
        """
        from ..deepseek import SERVER_TOOL_NAMES

        if names is None:
            names = ()
        chosen = tuple(dict.fromkeys(str(name) for name in names))
        unknown = [name for name in chosen if name not in SERVER_TOOL_NAMES]
        if unknown:
            raise ProviderError(f"unknown server tool {unknown[0]!r}")
        if chosen and not self.deepseek["available"]:
            raise ProviderError(self.deepseek["reason"] or "server tools unavailable")
        self.server_tools = chosen

    def _server_tool_declarations(self) -> list[dict[str, Any]]:
        """Return this protocol's request entries for the enabled server tools."""
        if not self.server_tools:
            return []
        if self.protocol == "anthropic_messages":
            return [{"type": name, "name": name} for name in self.server_tools]
        return [{"type": name} for name in self.server_tools]

    def _emit_server_tool(self, blocks: Any) -> None:
        """Report server-tool blocks to the live display, if one is attached."""
        sink = self._on_server_tool
        if sink is None or not isinstance(blocks, list):
            return
        for item in blocks:
            if isinstance(item, dict) and item.get("id"):
                sink(item)

    def _endpoint(self) -> str:
        """Return the full protocol endpoint, accepting a full URL as-is.

        A manually configured endpoint may carry a query string (Azure OpenAI's
        ``api-version`` is the common case). Appending a path with plain string
        concatenation would put it after the query, so path and query are
        joined separately.
        """
        parsed = urlsplit(self.base_url)
        path = parsed.path.rstrip("/")
        if self.protocol == "deepseek_web":
            new_path = path if path.endswith(CHAT_PATH) else f"{path}{CHAT_PATH}"
            return urlunsplit(
                (parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment)
            )
        if self.protocol == "anthropic_messages":
            if path.endswith("/messages"):
                new_path = path
            elif path.endswith("/v1"):
                new_path = f"{path}/messages"
            else:
                # DeepSeek's Anthropic base is https://api.deepseek.com/anthropic,
                # so the version segment is appended unless the caller supplied it.
                new_path = f"{path}/v1/messages"
        elif self.protocol == "openai_responses":
            new_path = path if path.endswith("/responses") else f"{path}/responses"
        else:
            new_path = (
                path
                if path.endswith("/chat/completions")
                else f"{path}/chat/completions"
            )
        return urlunsplit(
            (parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment)
        )

    def _document_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Turn stored document attachments into this protocol's content parts.

        A session stores an uploaded document in one protocol-neutral form
        (``attachments: [{"file_id": ...}]``) so the same history can be
        replayed after a provider switch. Each dialect wants a different block,
        so the conversion happens once, here, instead of in every payload
        a ``document`` with a ``file`` source, and Responses calls it an
        ``input_file`` item. The ``deepseek_web`` protocol has no way to
        reference a ``file_id``, so an attachment is dropped there.
        """
        # Most sessions never attach a document: return the list untouched so the
        # common turn does not pay for a copy it would discard.
        if not any(
            isinstance(message, dict) and "attachments" in message
            for message in messages
        ):
            return messages
        converted: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict) or "attachments" not in message:
                converted.append(message)
                continue
            clean = {
                key: value for key, value in message.items() if key != "attachments"
            }
            ids = [
                str(item.get("file_id"))
                for item in message.get("attachments") or []
                if isinstance(item, dict) and item.get("file_id")
            ]
            content = clean.get("content")
            parts = (
                list(content)
                if isinstance(content, list)
                else ([{"type": "text", "text": content}] if content else [])
            )
            parts.extend(self._document_part(file_id) for file_id in ids)
            clean["content"] = parts
            converted.append(clean)
        return converted

    def _document_part(self, file_id: str) -> dict[str, Any]:
        """Return one parsed-document block in the active protocol's shape."""
        if self.protocol == "anthropic_messages":
            # Messages takes a document whose source is the Files API id.
            return {
                "type": "document",
                "source": {"type": "file", "file_id": file_id},
            }
        if self.protocol == "openai_responses":
            return {"type": "input_file", "file_id": file_id}
        return {"type": "file", "file_id": file_id}

    def _wire_tools(
        self, tools: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        """Return tool declarations with provider-reserved names replaced."""
        if not tools or not self._tool_aliases:
            return tools
        wire_tools: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            wire_name = self._tool_aliases.get(name, name)
            if wire_name == name:
                wire_tools.append(tool)
                continue
            updated = dict(tool)
            updated_function = dict(function)
            updated_function["name"] = wire_name
            updated["function"] = updated_function
            wire_tools.append(updated)
        return wire_tools

    def _wire_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rewrite tool names in replayed assistant and tool history."""
        if not self._tool_aliases:
            return messages
        wire_messages: list[dict[str, Any]] = []
        changed = False
        for message in messages:
            if not isinstance(message, dict):
                wire_messages.append(message)
                continue
            updated = message
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                wire_calls: list[Any] | None = None
                for index, call in enumerate(calls):
                    function = call.get("function") if isinstance(call, dict) else None
                    name = function.get("name") if isinstance(function, dict) else None
                    wire_name = self._tool_aliases.get(name, name)
                    if wire_name == name:
                        continue
                    if wire_calls is None:
                        wire_calls = list(calls)
                    updated_call = dict(call)
                    updated_function = dict(function)
                    updated_function["name"] = wire_name
                    updated_call["function"] = updated_function
                    wire_calls[index] = updated_call
                if wire_calls is not None:
                    updated = dict(updated)
                    updated["tool_calls"] = wire_calls
                    changed = True
            if updated.get("role") == "tool":
                name = updated.get("name")
                wire_name = self._tool_aliases.get(name, name)
                if wire_name != name:
                    updated = dict(updated)
                    updated["name"] = wire_name
                    changed = True
            raw_blocks = updated.get("provider_content")
            if isinstance(raw_blocks, list):
                wire_blocks: list[Any] | None = None
                for index, block in enumerate(raw_blocks):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    wire_name = self._tool_aliases.get(name, name)
                    if wire_name == name:
                        continue
                    if wire_blocks is None:
                        wire_blocks = list(raw_blocks)
                    updated_block = dict(block)
                    updated_block["name"] = wire_name
                    wire_blocks[index] = updated_block
                if wire_blocks is not None:
                    updated = dict(updated)
                    updated["provider_content"] = wire_blocks
                    changed = True
            wire_messages.append(updated)
        return wire_messages if changed else messages

    def _canonical_tool_names(self, reply: ProviderReply) -> ProviderReply:
        """Map provider-facing tool aliases back to the dispatcher's names."""
        for call in reply.tool_calls:
            call.name = self._reverse_tool_aliases.get(call.name, call.name)
        return reply

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_server_tool: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderReply:
        """Send one conversation turn using the configured wire protocol."""
        messages = self._document_messages(messages)
        messages = self._wire_messages(messages)
        tools = self._wire_tools(tools)
        self._on_server_tool = on_server_tool if tools is not None else None
        self._server_steps = []
        self._server_citations = []
        try:
            return self._stream_chat(messages, tools, on_text, on_thinking)
        finally:
            self._on_server_tool = None

    def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Dispatch one turn to the protocol that owns the wire format."""
        self._server_blocks_raw = []
        if self.protocol == "anthropic_messages":
            payload = self._anthropic_payload(messages, tools)
            if self.stream_enabled:
                reply = self._anthropic_stream_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
            else:
                reply = self._anthropic_regular_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
        elif self.protocol == "openai_responses":
            payload = self._responses_payload(messages, tools)
            if self.stream_enabled:
                reply = self._responses_stream_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
            else:
                reply = self._responses_regular_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
        else:
            payload = self._chat_payload(messages, tools)
            if self.stream_enabled:
                reply = self._chat_stream_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
            else:
                reply = self._chat_regular_request(
                    payload, on_text=on_text, on_thinking=on_thinking
                )
        return self._canonical_tool_names(reply)

    def complete(self, messages: list[dict[str, Any]]) -> ProviderReply:
        """Request one plain-text completion and reject abnormal finish reasons."""
        reply = self.stream_chat(messages, tools=None, on_text=None)
        if reply.finish_reason not in {None, "stop", "completed"}:
            raise ProviderError(
                "provider stopped the summary with finish reason "
                f"{reply.finish_reason!r}"
            )
        return reply

    def complete_text(self, messages: list[dict[str, Any]]) -> str:
        """Request a plain text completion, returning only its content."""
        return self.complete(messages).content
