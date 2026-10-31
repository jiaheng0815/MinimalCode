"""Provider construction from merged settings."""

from __future__ import annotations

import re

import httpx2

from ..config import Settings
from .client import OpenAICompatibleProvider

_CONTEXT_SUFFIX = re.compile(r"^(?P<model>.+?)\[(?P<size>\d+)(?P<unit>[kKmM])\]$")


def _strip_context_suffix(
    model: str, config: dict[str, object]
) -> tuple[str, int | None]:
    """Strip Claude Code's ``[1m]`` suffix and return the window it selects."""
    if config.get("protocol") != "anthropic_messages":
        return model, None
    match = _CONTEXT_SUFFIX.match(model)
    if match is None:
        return model, None
    multiplier = 1_000 if match.group("unit").lower() == "k" else 1_000_000
    window = max(1, int(match.group("size")) * multiplier)
    return match.group("model"), window


def create_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> tuple[OpenAICompatibleProvider, str, str]:
    """Resolve provider/model/effort and return a ready client.

    An explicit argument wins over the provider default, which wins over the
    global model setting.
    """
    name, config = settings.provider_config(provider_name)
    provider_default_model = config.get("model")
    effective_model = (
        model
        or (str(provider_default_model) if provider_default_model else None)
        or settings.effective_model()
    )
    effective_thinking = thinking or str(settings.get("thinking", "high"))
    effective_model, suffix_window = _strip_context_suffix(effective_model, config)
    if suffix_window is not None:
        # The suffix is a client-side window selector; it must never reach the
        # wire, and it overrides a context_window that was configured without
        # knowing which model suffix the user asked for.
        config["context_window"] = suffix_window
    provider = OpenAICompatibleProvider(
        name=name,
        config=config,
        model=effective_model,
        thinking=effective_thinking,
        transport=transport,
    )
    return provider, name, effective_model
