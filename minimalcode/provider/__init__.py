"""OpenAI-compatible provider transport and protocol parsing.

The package separates reply types, shared parsing, retry transport, and the two
wire protocols. The names in ``__all__`` are the stable surface. Pre-split
module-level names are served by a temporary alias table that warns on use; see
``docs/compatibility.md`` for replacement paths and the removal target.
"""

from __future__ import annotations

from typing import Any

from .._compat import AliasTable, deprecated_attribute
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

# Removed in 0.7.0. Patching ``minimalcode.provider.time.sleep`` no longer
# affects retries; patch ``minimalcode.provider.transport.time.sleep`` instead.
_DEPRECATED_ATTRIBUTES: AliasTable = {
    "_connection_backoff": ("minimalcode.provider.transport", "connection_backoff"),
    "_content_text": ("minimalcode.provider.common", "content_text"),
    "_decode_json_body": ("minimalcode.provider.common", "decode_json_body"),
    "_is_event_stream": ("minimalcode.provider.common", "is_event_stream"),
    "_normalize_usage": ("minimalcode.provider.common", "normalize_usage"),
    "_stream_event_error": ("minimalcode.provider.common", "stream_event_error"),
    "_tool_arguments": ("minimalcode.provider.common", "tool_arguments"),
    "time": ("time", None),
}


def __getattr__(name: str) -> Any:
    """Serve pre-split names with a deprecation warning."""
    return deprecated_attribute(__name__, name, _DEPRECATED_ATTRIBUTES)
