"""Provider construction from merged settings."""

from __future__ import annotations

import httpx2

from ..config import Settings
from .client import OpenAICompatibleProvider


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
    provider = OpenAICompatibleProvider(
        name=name,
        config=config,
        model=effective_model,
        thinking=effective_thinking,
        transport=transport,
    )
    return provider, name, effective_model
