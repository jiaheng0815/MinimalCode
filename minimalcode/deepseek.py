"""The DeepSeek-API-only capabilities, and the rule that gates them.

Web search, read-links, document parsing, OCR, speech, and the safety review all
run on DeepSeek's own servers. They are documented for ``api.deepseek.com``
only, so every other endpoint gets none of them:

* another vendor speaking the same wire protocol has no such tools;
* ``deepseek_web`` drives the consumer chat app, which has no API surface at
  all for them;
* a self-hosted gateway in front of DeepSeek may or may not forward them, and
  guessing wrong turns a working turn into a 400.

One predicate decides, and every caller asks it instead of re-deriving the
answer from a provider name.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

# The only hosts the feature set is documented for. Matching the name is not
# enough: a preset called "deepseek" pointed at a proxy must stay unsupported.
API_HOSTS = frozenset({"api.deepseek.com"})

# Server-side tools, in the order they are declared. `deepseek_web_search`
# covers search (and deep-search while thinking), `deepseek_read_url` reads the
# links the user pasted.
WEB_SEARCH_TOOL = "deepseek_web_search"
READ_URL_TOOL = "deepseek_read_url"
SERVER_TOOL_NAMES = (WEB_SEARCH_TOOL, READ_URL_TOOL)

_NOT_API = (
    "DeepSeek-only features need the DeepSeek API (api.deepseek.com); "
    "this provider is not it"
)


def api_host(config: dict[str, Any]) -> str | None:
    """Return the lowercased host of ``base_url``, or None when unusable."""
    base_url = config.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    try:
        return (urlsplit(base_url.strip()).hostname or "").lower() or None
    except ValueError:
        return None


def is_deepseek_api(config: dict[str, Any]) -> bool:
    """True when this provider is an endpoint the feature set is documented for.

    ``deepseek_web`` is rejected even though its host is deepseek.com: it is the
    consumer chat app behind a private protocol, not the documented API.
    """
    if str(config.get("protocol", "openai_chat")) == "deepseek_web":
        return False
    return api_host(config) in API_HOSTS


def unavailable_reason(config: dict[str, Any]) -> str | None:
    """Explain why the feature set is off, or return None when it is usable."""
    if str(config.get("protocol", "openai_chat")) == "deepseek_web":
        return (
            "the DeepSeek web-app protocol has no tools API; "
            "use a DeepSeek API key instead"
        )
    if is_deepseek_api(config):
        return None
    return _NOT_API


def _block(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def server_tools(config: dict[str, Any]) -> tuple[str, ...]:
    """Return the server-side tools this provider should declare.

    A preset opts in once with ``server_tools.enabled``; the two tools are then
    switched individually, and the whole group still needs the DeepSeek API.
    """
    if not is_deepseek_api(config):
        return ()
    block = _block(config, "server_tools")
    if block.get("enabled") is not True:
        return ()
    return tuple(
        name
        for name, key in (
            (WEB_SEARCH_TOOL, "web_search"),
            (READ_URL_TOOL, "read_url"),
        )
        if block.get(key, True) is not False
    )


def features(config: dict[str, Any]) -> dict[str, Any]:
    """Describe every capability the page may offer, and why one is missing.

    Each flag answers "may this be used right now": the endpoint must be the
    DeepSeek API and the capability's own config block must be enabled. The
    reasons are shown in the settings panel instead of a silent dead button.
    """
    reason = unavailable_reason(config)
    available = reason is None
    tools = _block(config, "web_tools")
    media = _block(config, "media")
    title = _block(config, "session_title")
    safety = _block(config, "safety_check")
    server = set(server_tools(config))
    media_on = bool(media.get("enabled") is True)

    def media_path(key: str) -> bool:
        path = media.get(key)
        return media_on and isinstance(path, str) and bool(path.strip("/"))

    return {
        "available": available,
        "reason": reason or "",
        "endpoint": api_host(config) or "",
        # Server-side web tools, declared on the request.
        "web_search": available and WEB_SEARCH_TOOL in server,
        "read_url": available and READ_URL_TOOL in server,
        # Standalone Search/Fetch API, called by our own tools.
        "search_api": available
        and tools.get("enabled") is True
        and bool(str(tools.get("search_path") or "").strip("/")),
        "fetch_api": available
        and tools.get("enabled") is True
        and bool(str(tools.get("fetch_path") or "").strip("/")),
        # Files API + tools API: documents, OCR, speech.
        "documents": media_path("files_path"),
        "ocr": media_path("ocr_path"),
        "asr": media_path("asr_path"),
        "tts": media_path("tts_path"),
        # Request extensions.
        "safety": available and safety.get("level") == "high",
        "title": available and title.get("enabled") is True,
    }


__all__ = [
    "API_HOSTS",
    "READ_URL_TOOL",
    "SERVER_TOOL_NAMES",
    "WEB_SEARCH_TOOL",
    "api_host",
    "features",
    "is_deepseek_api",
    "server_tools",
    "unavailable_reason",
]
