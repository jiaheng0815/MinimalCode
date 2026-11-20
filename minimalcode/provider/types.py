"""Provider-neutral reply and tool-call model objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """One function call requested by the model."""

    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class ProviderReply:
    """Normalized result of one provider turn."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    provider_content: list[dict[str, Any]] | None = None
    # Server-side web tool blocks (search, read links) this turn produced. Only
    # `read_link` ones are replayed: their `signature` is what restores the page
    # into a later turn.
    server_tools: list[dict[str, Any]] = field(default_factory=list)
    # Sources the answer cites, in the order the provider sent them.
    citations: list[dict[str, Any]] = field(default_factory=list)
    # Short conversation title the provider generated alongside the answer. Only
    # the first request of a session asks for one, so this is usually None.
    title: str | None = None

    def as_message(self) -> dict[str, Any]:
        """Render the reply as an assistant message for the conversation log."""
        message: dict[str, Any] = {
            "role": "assistant",
            # A Chat assistant message without tool calls must carry a string;
            # null content is rejected by strict OpenAI-compatible servers and
            # would poison every later request in the session.
            "content": self.content if self.content or not self.tool_calls else None,
        }
        if self.provider_content:
            message["provider_content"] = self.provider_content
        if self.server_tools:
            message["server_tools"] = self.server_tools
        if self.citations:
            message["citations"] = self.citations
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
