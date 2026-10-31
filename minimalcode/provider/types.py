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
    # Anthropic thinking blocks must be replayed with their signatures while a
    # tool call is open. Other protocols ignore this field, and the Chat payload
    # builder strips it before sending.
    provider_content: list[dict[str, Any]] | None = None

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
