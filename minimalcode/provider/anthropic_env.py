"""Claude Code style environment configuration for the Anthropic protocol.

Claude Code is configured through process environment variables
(``ANTHROPIC_BASE_URL``, ``ANTHROPIC_AUTH_TOKEN``, ``ANTHROPIC_MODEL`` and the
``CLAUDE_CODE_*`` knobs). ``minimalcode --anthropic-env`` reuses that convention
so an existing Claude Code setup can start MinimalCode without editing
``settings.json``. Nothing here is written to disk: the caller injects the
returned provider entry into the in-memory settings only.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..config import EFFORT_LEVELS
from ..errors import ConfigError

# The provider name a session records when the environment is the source. It is
# not written to settings.json, so it cannot collide with a configured provider.
ANTHROPIC_ENV_PROVIDER = "anthropic-env"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
# Anthropic's standard Messages window. DeepSeek and other 1M endpoints opt in
# with the Claude Code model suffix (``deepseek-flash[1m]``) or an explicit
# ANTHROPIC_CONTEXT_WINDOW, so no hostname check is needed here.
DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_MAX_TOKENS = 8192
# Claude Code parses ``model[1m]`` client side and sends the bare model name;
# the suffix only selects a context window.
_CONTEXT_SUFFIX = re.compile(r"^(?P<model>.+?)\[(?P<size>\d+)(?P<unit>[kKmM])\]$")


@dataclass(slots=True)
class AnthropicEnvConfig:
    """Provider entry derived from the environment, plus in-memory overrides."""

    provider: str
    config: dict[str, Any]
    model: str
    thinking: str | None
    compact_at: float | None


def _text(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _positive_int(text: str, name: str) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer: {text!r}") from exc
    if value < 1:
        raise ConfigError(f"{name} must be a positive integer: {text!r}")
    return value


def _split_model(text: str) -> tuple[str, int | None]:
    """Strip Claude Code's ``[Nk]``/``[Nm]`` suffix and return its window."""
    match = _CONTEXT_SUFFIX.match(text)
    if match is None:
        return text, None
    size = int(match.group("size"))
    multiplier = 1_000 if match.group("unit").lower() == "k" else 1_000_000
    return match.group("model"), max(1, size * multiplier)


def from_anthropic_env(
    environ: Mapping[str, str] | None = None,
    *,
    model: str | None = None,
    thinking: str | None = None,
) -> AnthropicEnvConfig:
    """Build an Anthropic provider entry from Claude Code environment variables.

    Explicit ``model`` / ``thinking`` arguments (the CLI flags) win over the
    environment. The API key is never echoed in an error message.
    """
    env = os.environ if environ is None else environ

    base_url = _text(env, "ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL
    auth_token = _text(env, "ANTHROPIC_AUTH_TOKEN")
    api_key = _text(env, "ANTHROPIC_API_KEY")
    if not auth_token and not api_key:
        raise ConfigError(
            "--anthropic-env requires ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY"
        )

    raw_model = (
        (model or "").strip()
        or _text(env, "ANTHROPIC_MODEL")
        or _text(env, "ANTHROPIC_DEFAULT_SONNET_MODEL")
    )
    if not raw_model:
        raise ConfigError(
            "--anthropic-env requires a model: pass --model or set ANTHROPIC_MODEL"
        )
    model_id, suffix_window = _split_model(raw_model)

    context_window = suffix_window or DEFAULT_CONTEXT_WINDOW
    window_override = _text(env, "ANTHROPIC_CONTEXT_WINDOW")
    if window_override is not None:
        context_window = _positive_int(window_override, "ANTHROPIC_CONTEXT_WINDOW")

    max_tokens = DEFAULT_MAX_TOKENS
    max_tokens_override = _text(env, "ANTHROPIC_MAX_TOKENS")
    if max_tokens_override is not None:
        max_tokens = _positive_int(max_tokens_override, "ANTHROPIC_MAX_TOKENS")

    compact_at: float | None = None
    compact_window = _text(env, "CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if compact_window is not None:
        threshold = _positive_int(compact_window, "CLAUDE_CODE_AUTO_COMPACT_WINDOW")
        ratio = threshold / context_window
        # context.compact_at must stay inside (0, 1); a window larger than the
        # context size means "compact as late as possible", not "never".
        compact_at = round(min(0.95, max(0.1, ratio)), 4)

    headers: dict[str, str] = {}
    config: dict[str, Any] = {
        "protocol": "anthropic_messages",
        "base_url": base_url,
        "model": model_id,
        "context_window": context_window,
        "max_tokens": max_tokens,
        "timeout_seconds": 120,
        "stream": True,
        "send_reasoning_effort": True,
        "headers": headers,
        "extra_body": {},
    }
    if auth_token is not None:
        # Claude Code's ANTHROPIC_AUTH_TOKEN is a bearer credential; an
        # ANTHROPIC_API_KEY keeps the x-api-key header the client generates.
        headers["Authorization"] = f"Bearer {auth_token}"
    else:
        config["api_key"] = api_key

    effort = thinking or _text(env, "CLAUDE_CODE_EFFORT_LEVEL")
    if effort is not None:
        effort = effort.strip().lower()
        if effort not in EFFORT_LEVELS:
            if thinking is not None:
                # An explicit flag is validated by argparse; reaching here means
                # the caller passed something else and should hear about it.
                raise ConfigError(f"unknown effort: {thinking!r}")
            effort = None

    return AnthropicEnvConfig(
        provider=ANTHROPIC_ENV_PROVIDER,
        config=config,
        model=model_id,
        thinking=effort,
        compact_at=compact_at,
    )
