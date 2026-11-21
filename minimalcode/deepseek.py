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
# Request-side features (server tools, documents, safety, titles) ride on the
# chat request itself, so they need the chat provider to be the DeepSeek API
# even when another configured provider supplies the HTTP-call features.
_NOT_ACTIVE = (
    "server tools, documents, and the safety check are declared on the DeepSeek "
    "API request; the selected provider is another endpoint"
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


def _active_reason(config: dict[str, Any]) -> str:
    """Explain why the request-side features are off on the chat provider."""
    if is_deepseek_api(config):
        return ""
    if str(config.get("protocol", "openai_chat")) == "deepseek_web":
        return (
            "the DeepSeek web-app protocol has no tools API; "
            "use a DeepSeek API key instead"
        )
    return _NOT_ACTIVE


def _preferred_names(settings: Any, active: str | None) -> list[str]:
    """Return provider names to try for the DeepSeek feature set, in order."""
    names: list[str] = []
    active_name = active or str(settings.data.get("provider") or "")
    if active_name:
        names.append(active_name)
    table = settings.data.get("providers")
    if isinstance(table, dict):
        # The documented presets first, then the rest alphabetically, so two
        # blocks pointing at the same host still resolve to a predictable one.
        for name in ("deepseek", "deepseek-anthropic"):
            if name in table:
                names.append(name)
        names.extend(sorted(name for name in table if name not in names))
    return names


def feature_source(
    settings: Any, active: str | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Return the provider that supplies the DeepSeek-only features.

    The chat provider wins whenever it is the DeepSeek API. Otherwise any
    DeepSeek API provider in the settings file can supply the features that are
    plain HTTP calls (OCR, speech, the standalone Search API), so a session
    talking to another vendor keeps them. A block whose ``${VAR}`` is unset, or
    that has no credential while requiring one, is skipped instead of failing
    startup: it could not run them anyway.
    """
    from .config import provider_api_key
    from .errors import ConfigError

    seen: set[str] = set()
    for name in _preferred_names(settings, active):
        if name in seen:
            continue
        seen.add(name)
        try:
            _, config = settings.provider_config(name)
        except (ConfigError, ValueError):
            continue
        if not is_deepseek_api(config):
            continue
        if config.get("require_api_key", True) and provider_api_key(config) is None:
            # Advertising OCR and speech on a keyless block would only fail on
            # the first call, with a 401 from a service the page never named.
            continue
        return name, config
    return None


def declarations(config: dict[str, Any]) -> list[dict[str, str]]:
    """Return the protocol-neutral ``tools`` entries for this provider.

    These are *not* function schemas: the model never calls them, the service
    does. Each protocol adds what it needs on top (Messages also carries a
    ``name``), so the shared list stays a plain ``{"type": ...}``.
    """
    return [{"type": name} for name in server_tools(config)]


def web_tools_enabled(config: dict[str, Any]) -> bool:
    """True when the standalone Search/Fetch API is the configured path."""
    tools = _block(config, "web_tools")
    return tools.get("enabled") is True


def migration_notice(config: dict[str, Any]) -> str | None:
    """Explain a configuration that still pays for the standalone API.

    An older settings file turned ``web_tools.enabled`` on before the server-side
    tools existed. Rewriting it would surprise the user — the standalone API does
    work — so the notice reports the per-call cost and the one key that switches
    the default path.
    """
    if not is_deepseek_api(config) or not web_tools_enabled(config):
        return None
    block = _block(config, "server_tools")
    if block.get("enabled") is True:
        return None
    return (
        "[minimalcode] web_tools.enabled=true still routes web search through the "
        "per-call Search/Fetch API (5 CNY/1k requests). Set "
        "server_tools.enabled=true to let the model call deepseek_web_search and "
        "deepseek_read_url instead."
    )


def features(
    config: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
    source_name: str = "",
) -> dict[str, Any]:
    """Describe every capability the page may offer, and why one is missing.

    Each flag answers "may this be used right now". ``config`` is the chat
    provider: server tools, documents, safety, and titles are declared on its
    request, so they also need it to be the DeepSeek API. ``source`` is the
    provider that supplies the plain HTTP calls (OCR, speech, the standalone
    Search API); it defaults to ``config``, which keeps a single-provider setup
    exactly as it was. The reasons are shown in the settings panel instead of a
    silent dead button.
    """
    source = config if source is None else source
    available = unavailable_reason(source) is None
    active = is_deepseek_api(config)
    reason = unavailable_reason(source)
    tools = _block(source, "web_tools")
    media = _block(source, "media")
    title = _block(config, "session_title")
    server = set(server_tools(config))
    media_on = bool(media.get("enabled") is True)

    def media_path(key: str) -> bool:
        path = media.get(key)
        return media_on and isinstance(path, str) and bool(path.strip("/"))

    return {
        "available": available,
        # The chat provider itself may not be the DeepSeek API: the page keeps
        # the HTTP-call features and hides the ones that ride on the request.
        "active": active,
        "provider": source_name,
        "reason": reason or "",
        "active_reason": _active_reason(config),
        "endpoint": api_host(source) or "",
        # Server-side web tools, declared on the request.
        "web_search": active and WEB_SEARCH_TOOL in server,
        "read_url": active and READ_URL_TOOL in server,
        # Standalone Search/Fetch API, called by our own tools.
        "search_api": available
        and tools.get("enabled") is True
        and bool(str(tools.get("search_path") or "").strip("/")),
        "fetch_api": available
        and tools.get("enabled") is True
        and bool(str(tools.get("fetch_path") or "").strip("/")),
        # Files API + tools API: documents, OCR, speech. A document's file_id is
        # attached to the chat request, so only the chat provider can take it.
        "documents": active and media_path("files_path"),
        "ocr": available and media_path("ocr_path"),
        "asr": available and media_path("asr_path"),
        "tts": available and media_path("tts_path"),
        # Request extensions. `safety` answers "may the switch be turned on":
        # the review is a request field, so it needs the DeepSeek chat provider,
        # but not a preset that already enabled it.
        "safety": active,
        "title": active and title.get("enabled") is True,
    }


__all__ = [
    "API_HOSTS",
    "READ_URL_TOOL",
    "SERVER_TOOL_NAMES",
    "WEB_SEARCH_TOOL",
    "api_host",
    "declarations",
    "feature_source",
    "features",
    "is_deepseek_api",
    "migration_notice",
    "server_tools",
    "unavailable_reason",
    "web_tools_enabled",
]
