"""OpenAI-compatible provider transport and protocol parsing.

The package separates reply types, shared parsing, retry transport, and the two
wire protocols. ``__all__`` is the complete public surface; helpers that lived
on the pre-0.7 single ``provider`` module are imported from the submodule that
defines them.
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
