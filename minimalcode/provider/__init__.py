"""Provider transport and protocol parsing for three wire formats.

The package normalizes the Chat Completions, Responses, and Anthropic Messages
wire formats into one ``ProviderReply``/``ToolCall`` shape, so the agent loop
never handles any dialect's JSON directly. ``__all__`` is the complete public
surface: the client class, its factory, the reply types, and the error type
every failure leaves through. Conversation history, tool dispatch, and
retry-notice rendering live outside this package; here one call performs exactly
one HTTP round trip and parses the answer. All requests must go through
``TransportMixin._open``, which holds the only copy of the timeout and retry
policy; helpers that lived on the pre-0.7 single ``provider`` module are
imported from the submodule that defines them.
"""

from __future__ import annotations

from ..errors import ProviderError
from .client import OpenAICompatibleProvider
from .factory import create_provider
from .types import ProviderReply, ToolCall

__all__ = [
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderReply",
    "ToolCall",
    "create_provider",
]
