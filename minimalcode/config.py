from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

APP_NAME = "MinimalCode"
APP_DIR_NAME = ".MinimalCode"
HOME_ENV = "MINIMALCODE_HOME"
SETTINGS_FILENAME = "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "deepseek",
    "model": "deepseek-flash",
    "thinking": "high",
    "max_turns": 50,
    "session": {
        "save": True,
    },
    "shell": {
        "path": None,
        "timeout_seconds": 120,
        "max_output_chars": 60_000,
    },
    "tools": {
        "allow_outside_workspace": False,
        "max_file_chars": 160_000,
        "max_file_bytes": 5_000_000,
        "max_results": 200,
    },
    "context": {
        "compact_at": 0.8,
        "keep_messages": 12,
        "max_instruction_chars": 60_000,
    },
    "providers": {
        "deepseek": {
            "protocol": "openai_chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "context_window": 1_000_000,
            "timeout_seconds": 120,
            "stream": True,
            "include_usage": True,
            "send_reasoning_effort": False,
            "headers": {},
            "extra_body": {},
        }
    },
}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def home_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    configured = os.environ.get(HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / APP_DIR_NAME).resolve()


def settings_path(home: str | Path | None = None) -> Path:
    return home_dir(home) / SETTINGS_FILENAME


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)),
            value,
        )
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def _validate(data: dict[str, Any], path: Path) -> None:
    provider = data.get("provider")
    model = data.get("model")
    providers = data.get("providers")

    if not isinstance(provider, str) or not provider.strip():
        raise ConfigError(f"{path}: provider must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(f"{path}: model must be a non-empty string")
    if not isinstance(providers, dict) or provider not in providers:
        raise ConfigError(f"{path}: provider {provider!r} is not defined in providers")

    max_turns = data.get("max_turns")
    if not isinstance(max_turns, int) or not 1 <= max_turns <= 10_000:
        raise ConfigError(f"{path}: max_turns must be an integer from 1 to 10000")

    for key in ("shell", "tools", "context", "session"):
        if not isinstance(data.get(key), dict):
            raise ConfigError(f"{path}: {key} must be an object")


@dataclass(slots=True)
class Settings:
    data: dict[str, Any]
    home: Path
    source: Path

    def get(self, path: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def provider_config(self, name: str | None = None) -> tuple[str, dict[str, Any]]:
        provider_name = name or str(self.data["provider"])
        providers = self.data["providers"]
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            raise ConfigError(f"provider {provider_name!r} is not defined")
        return provider_name, expand_env(copy.deepcopy(provider))

    def effective_provider(self, override: str | None = None) -> str:
        return override or str(self.data["provider"])

    def effective_model(self, override: str | None = None) -> str:
        return override or str(self.data["model"])


def load_settings(
    home: str | Path | None = None,
    *,
    require_file: bool = False,
) -> Settings:
    root = home_dir(home)
    source = root / SETTINGS_FILENAME
    if not source.exists():
        if require_file:
            raise ConfigError(f"settings file not found: {source}")
        data = copy.deepcopy(DEFAULT_SETTINGS)
        _validate(data, source)
        return Settings(data=data, home=root, source=source)

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: top-level value must be an object")

    data = _deep_merge(DEFAULT_SETTINGS, raw)
    _validate(data, source)
    return Settings(data=data, home=root, source=source)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def write_settings(data: dict[str, Any], home: str | Path | None = None) -> Path:
    root = home_dir(home)
    path = root / SETTINGS_FILENAME
    _validate(data, path)
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def ensure_config(home: str | Path | None = None, *, overwrite: bool = False) -> Path:
    root = home_dir(home)
    path = root / SETTINGS_FILENAME
    root.mkdir(parents=True, exist_ok=True)
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
        os.chmod(sessions, 0o700)
    except OSError:
        pass
    if overwrite or not path.exists():
        _atomic_write(path, json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False) + "\n")
    return path


def redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            sensitive = (
                lowered in {"api_key", "authorization", "x-api-key"}
                or "secret" in lowered
                or "token" in lowered
            )
            if sensitive:
                result[key] = "<redacted>" if item else item
            else:
                result[key] = redact_config(item)
        return result
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value


def provider_api_key(provider: dict[str, Any]) -> str | None:
    direct = provider.get("api_key")
    if isinstance(direct, str) and direct:
        return direct
    env_name = provider.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        key = os.environ.get(env_name)
        if key:
            return key
    return None
