from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, load_settings, write_settings

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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Pi config file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level value must be an object")
    return data


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
    include_secrets: bool = True,
    overwrite: bool = False,
) -> ImportResult:
    pi_root = (
        Path(pi_home).expanduser().resolve()
        if pi_home
        else (Path.home() / ".pi" / "agent").resolve()
    )
    models_data = _read_json(pi_root / "models.json")
    settings_data = _read_json(pi_root / "settings.json")
    default_provider = str(settings_data.get("defaultProvider") or "")
    default_model = str(settings_data.get("defaultModel") or "")

    source_providers = models_data.get("providers")
    if not isinstance(source_providers, dict):
        raise ConfigError(f"{pi_root / 'models.json'}: providers must be an object")

    destination = load_settings(destination_home)
    data = copy.deepcopy(destination.data)
    imported: list[tuple[str, dict[str, Any], str]] = []
    skipped: list[str] = []

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
        if not protocol or not isinstance(base_url, str) or not base_url:
            skipped.append(provider_name)
            continue

        existing = data["providers"].get(provider_name)
        if existing is not None and not overwrite:
            skipped.append(provider_name)
            continue

        model_id = str(model["id"])
        provider_config: dict[str, Any] = {
            "protocol": protocol,
            "base_url": base_url,
            "context_window": int(model.get("contextWindow") or 128_000),
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
                + "".join(char if char.isalnum() else "_" for char in provider_name).upper()
                + "_API_KEY"
            )
            provider_config["api_key_env"] = env_name
        data["providers"][provider_name] = provider_config
        imported.append((provider_name, provider_config, model_id))

    if not imported:
        detail = f"; skipped: {', '.join(skipped)}" if skipped else ""
        raise ConfigError(f"no compatible Pi providers found{detail}")

    selected = next(
        (item for item in imported if item[0] == default_provider),
        imported[0],
    )
    data["provider"] = selected[0]
    data["model"] = selected[2]
    result_path = write_settings(data, destination_home)
    return ImportResult(
        provider=selected[0],
        model=selected[2],
        settings_path=result_path,
        imported=[item[0] for item in imported],
        skipped=skipped,
    )
