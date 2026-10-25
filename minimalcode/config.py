from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ConfigError

APP_NAME = "MinimalCode"
APP_DIR_NAME = ".MinimalCode"
HOME_ENV = "MINIMALCODE_HOME"
SETTINGS_FILENAME = "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = json.loads(
    files("minimalcode").joinpath("defaults.json").read_text(encoding="utf-8")
)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def home_dir(path: str | Path | None = None) -> Path:
    candidate: Path | None = None
    try:
        if path is not None:
            candidate = Path(path)
        else:
            configured = os.environ.get(HOME_ENV)
            candidate = Path(configured) if configured else Path.home() / APP_DIR_NAME
        return candidate.expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        detail = candidate if candidate is not None else path or "default home"
        raise ConfigError(f"invalid home directory: {detail}") from exc


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


def _unresolved_env(value: Any) -> str | None:
    if isinstance(value, str):
        match = _ENV_PATTERN.search(value)
        return match.group(0) if match else None
    if isinstance(value, list):
        for item in value:
            unresolved = _unresolved_env(item)
            if unresolved is not None:
                return unresolved
    if isinstance(value, dict):
        for item in value.values():
            unresolved = _unresolved_env(item)
            if unresolved is not None:
                return unresolved
    return None


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
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or not 1 <= max_turns <= 10_000
    ):
        raise ConfigError(f"{path}: max_turns must be an integer from 1 to 10000")

    for key in ("shell", "tools", "context", "session", "output"):
        if not isinstance(data.get(key), dict):
            raise ConfigError(f"{path}: {key} must be an object")

    shell = data["shell"]
    shell_path = shell.get("path")
    if shell_path is not None and (
        not isinstance(shell_path, str) or not shell_path.strip()
    ):
        raise ConfigError(f"{path}: shell.path must be null or a non-empty string")
    _validate_number(
        shell.get("timeout_seconds"),
        path,
        "shell.timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    _validate_integer(
        shell.get("max_output_chars"),
        path,
        "shell.max_output_chars",
        minimum=1,
    )

    tools = data["tools"]
    if not isinstance(tools.get("allow_outside_workspace"), bool):
        raise ConfigError(f"{path}: tools.allow_outside_workspace must be a boolean")
    _validate_integer(
        tools.get("max_file_chars"),
        path,
        "tools.max_file_chars",
        minimum=1,
    )
    _validate_integer(
        tools.get("max_file_bytes"),
        path,
        "tools.max_file_bytes",
        minimum=1,
    )
    _validate_integer(
        tools.get("max_results"),
        path,
        "tools.max_results",
        minimum=1,
        maximum=5000,
    )

    context = data["context"]
    _validate_number(
        context.get("compact_at"),
        path,
        "context.compact_at",
        minimum=0,
        maximum=1,
        inclusive_minimum=False,
    )
    _validate_integer(
        context.get("keep_messages"),
        path,
        "context.keep_messages",
        minimum=4,
    )
    _validate_integer(
        context.get("max_instruction_chars"),
        path,
        "context.max_instruction_chars",
        minimum=1,
    )

    if not isinstance(data["session"].get("save"), bool):
        raise ConfigError(f"{path}: session.save must be a boolean")

    highlight = data["output"].get("markdown_highlight")
    if not isinstance(highlight, bool):
        raise ConfigError(f"{path}: output.markdown_highlight must be a boolean")
    theme = data["output"].get("theme")
    if not isinstance(theme, str) or theme not in {"dark", "light"}:
        raise ConfigError(f"{path}: output.theme must be 'dark' or 'light'")

    if "thinking" in data and not isinstance(data["thinking"], str):
        raise ConfigError(f"{path}: thinking must be a string")


def _validate_number(
    value: Any,
    path: Path,
    key: str,
    *,
    minimum: float,
    maximum: float | None = None,
    inclusive_minimum: bool = True,
) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{path}: {key} must be a number")
    minimum_ok = value >= minimum if inclusive_minimum else value > minimum
    if not minimum_ok or (maximum is not None and value > maximum):
        bounds = f">{minimum}" if not inclusive_minimum else f">={minimum}"
        if maximum is not None:
            bounds += f" and <={maximum}"
        raise ConfigError(f"{path}: {key} must be {bounds}")


def _validate_integer(
    value: Any,
    path: Path,
    key: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{path}: {key} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f">={minimum}"
        if maximum is not None:
            bounds += f" and <={maximum}"
        raise ConfigError(f"{path}: {key} must be {bounds}")


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
        expanded = expand_env(copy.deepcopy(provider))
        unresolved = _unresolved_env(expanded)
        if unresolved is not None:
            raise ConfigError(
                f"provider {provider_name!r} has an unset environment variable: "
                f"{unresolved}"
            )
        return provider_name, expanded

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
    try:
        text = source.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if require_file:
            raise ConfigError(f"settings file not found: {source}") from None
        data = copy.deepcopy(DEFAULT_SETTINGS)
        _validate(data, source)
        return Settings(data=data, home=root, source=source)
    except (OSError, UnicodeDecodeError) as exc:
        # An existing but unreadable file must never silently fall back to
        # defaults: the user would be talking to a different provider than
        # the one their settings file describes.
        raise ConfigError(f"cannot read {source}: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
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
        _atomic_write(
            path, json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False) + "\n"
        )
    return path


_SENSITIVE_EXACT_KEYS = {"api_key", "apikey", "authorization", "x-api-key"}
# Token budgets and counters describe costs, not credentials, and environment
# variable names are safe to print. Redacting them made `config show` hide
# ordinary settings such as `max_tokens` or `prompt_tokens`.
_SAFE_COMPACT_KEYS = {
    "maxtokens",
    "maxoutputtokens",
    "maxinputtokens",
    "maxcompletiontokens",
    "prompttokens",
    "completiontokens",
    "totaltokens",
    "inputtokens",
    "outputtokens",
    "cachedtokens",
    "reasoningtokens",
    "tokenizer",
    "tokencount",
    "tokenlimit",
    "tokenbudget",
}


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    if compact in _SAFE_COMPACT_KEYS or compact.endswith("env"):
        return False
    return (
        "apikey" in compact
        or "secret" in compact
        or "password" in compact
        or "credential" in compact
        or compact.endswith(("token", "tokens"))
    )


def redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
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
