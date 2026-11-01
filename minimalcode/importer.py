from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import (
    DEFAULT_SETTINGS,
    ConfigError,
    load_settings,
    write_settings_document,
)

PI_PROTOCOLS = {
    "openai-completions": "openai_chat",
    "openai-chat": "openai_chat",
    "openai-responses": "openai_responses",
}


@dataclass(slots=True)
class ImportResult:
    provider: str
    model: str
    settings_path: Path
    imported: list[str]
    skipped: list[str]


def _read_json(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise ConfigError(f"Pi config file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level value must be an object")
    return data


def _valid_base_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _choose_model(
    provider_name: str,
    provider: dict[str, Any],
    default_provider: str,
    default_model: str,
) -> dict[str, Any] | None:
    models = provider.get("models")
    if not isinstance(models, list):
        return None
    candidates = [item for item in models if isinstance(item, dict) and item.get("id")]
    if not candidates:
        return None
    if provider_name == default_provider:
        for model in candidates:
            if str(model.get("id")) == default_model:
                return model
    return candidates[0]


def import_pi_settings(
    *,
    destination_home: str | Path,
    pi_home: str | Path | None = None,
    include_secrets: bool = False,
    overwrite: bool = False,
) -> ImportResult:
    try:
        pi_root = (
            Path(pi_home).expanduser().resolve()
            if pi_home
            else (Path.home() / ".pi" / "agent").resolve()
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ConfigError(
            f"invalid Pi config directory: {pi_home or '~/.pi/agent'}"
        ) from exc
    models_data = _read_json(pi_root / "models.json")
    # Provider defaults are optional: Pi still works when only models.json exists.
    settings_data = _read_json(pi_root / "settings.json", missing_ok=True)
    default_provider = str(settings_data.get("defaultProvider") or "")
    default_model = str(settings_data.get("defaultModel") or "")

    source_providers = models_data.get("providers")
    if not isinstance(source_providers, dict):
        raise ConfigError(f"{pi_root / 'models.json'}: providers must be an object")

    destination = load_settings(destination_home)
    raw = copy.deepcopy(destination.raw or {})
    raw_providers = raw.get("providers")
    if not isinstance(raw_providers, dict):
        raw_providers = {}
    presets = DEFAULT_SETTINGS.get("providers")
    if not isinstance(presets, dict):
        presets = {}
    imported: list[tuple[str, dict[str, Any], str]] = []
    imported_configs: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    already_present: list[str] = []

    for name, raw_provider in source_providers.items():
        if not isinstance(raw_provider, dict):
            skipped.append(str(name))
            continue
        provider_name = str(name)
        model = _choose_model(
            provider_name,
            raw_provider,
            default_provider,
            default_model,
        )
        if model is None:
            skipped.append(provider_name)
            continue
        api = str(model.get("api") or raw_provider.get("api") or "")
        protocol = PI_PROTOCOLS.get(api)
        base_url = raw_provider.get("baseUrl") or raw_provider.get("base_url")
        if not protocol or not _valid_base_url(base_url):
            skipped.append(provider_name)
            continue

        existing = raw_providers.get(provider_name)
        # A name that only matches a bundled preset carries no user data, so it
        # must not be reported as "already imported" or block a fresh import.
        if (
            existing is not None
            and existing != presets.get(provider_name)
            and not overwrite
        ):
            skipped.append(provider_name)
            already_present.append(provider_name)
            continue

        model_id = str(model["id"])
        try:
            context_window = int(model.get("contextWindow") or 128_000)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigError(
                f"provider {provider_name!r} has an invalid contextWindow"
            ) from exc
        if context_window < 1:
            raise ConfigError(
                f"provider {provider_name!r} has an invalid contextWindow"
            )
        provider_config: dict[str, Any] = {
            "protocol": protocol,
            "base_url": base_url,
            # Each imported provider keeps its own model; otherwise switching
            # to a later provider would silently reuse the selected one.
            "model": model_id,
            "context_window": context_window,
            "timeout_seconds": 120,
            "stream": True,
            "include_usage": protocol == "openai_chat",
            "send_reasoning_effort": False,
            "headers": {},
            "extra_body": {},
        }
        api_key = raw_provider.get("apiKey")
        if include_secrets and isinstance(api_key, str) and api_key:
            provider_config["api_key"] = api_key
        elif isinstance(api_key, str) and api_key:
            env_name = (
                "MINIMALCODE_"
                + "".join(
                    char if char.isalnum() else "_" for char in provider_name
                ).upper()
                + "_API_KEY"
            )
            provider_config["api_key_env"] = env_name
        imported.append((provider_name, provider_config, model_id))
        imported_configs[provider_name] = provider_config

    if not imported:
        if already_present and len(already_present) == len(skipped):
            raise ConfigError(
                "providers already imported: "
                + ", ".join(already_present)
                + "; rerun with --overwrite to replace"
            )
        detail = f"; skipped: {', '.join(skipped)}" if skipped else ""
        if already_present:
            detail += (
                "; already present (rerun with --overwrite to replace): "
                + ", ".join(already_present)
            )
        raise ConfigError(f"no compatible Pi providers found{detail}")

    selected = next(
        (item for item in imported if item[0] == default_provider),
        imported[0],
    )
    document = copy.deepcopy(raw)
    providers = document.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    # Replace each imported entry instead of deep-merging it: an overwrite must
    # not keep a stale `api_key` from the entry it replaces.
    for name, provider_config in imported_configs.items():
        providers[name] = provider_config
    document["providers"] = providers
    current_provider = document.get("provider")
    current_model = document.get("model")
    selection_is_default = current_provider in (
        None,
        DEFAULT_SETTINGS.get("provider"),
    ) and current_model in (None, DEFAULT_SETTINGS.get("model"))
    if selection_is_default or current_provider == selected[0]:
        document["provider"] = selected[0]
        document["model"] = selected[2]
    result_path = write_settings_document(document, destination_home)
    return ImportResult(
        provider=selected[0],
        model=selected[2],
        settings_path=result_path,
        imported=[item[0] for item in imported],
        skipped=skipped,
    )
