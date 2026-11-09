from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigError
from .sandbox import NETWORK_POLICIES, SANDBOX_BACKENDS

APP_NAME = "MinimalCode"
APP_DIR_NAME = ".MinimalCode"
HOME_ENV = "MINIMALCODE_HOME"
EFFORT_LEVELS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "ultra",
    "max",
)
SETTINGS_FILENAME = "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = json.loads(
    files("minimalcode").joinpath("defaults.json").read_text(encoding="utf-8")
)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


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


def _coerce_bool(value: Any, label: str) -> bool:
    """Convert an expanded string setting to a real boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ConfigError(f"{label} must be a boolean (got {value!r})")


def _coerce_int(value: Any, label: str) -> int:
    """Convert an expanded string setting to an integer."""
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{label} must be an integer (got {value!r})") from exc


def _coerce_float(value: Any, label: str) -> float:
    """Convert an expanded string setting to a finite float."""
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{label} must be a number (got {value!r})") from exc
    if not math.isfinite(number):
        raise ConfigError(f"{label} must be a finite number")
    return number


def _coerce_provider_values(name: str, provider: dict[str, Any]) -> dict[str, Any]:
    """Re-type values that came from ``${ENV}`` placeholders.

    Validation runs before expansion and therefore only checks placeholders.
    Once expanded, ``"false"`` is still a string unless it is converted here;
    ``bool("false")`` is ``True``, and ``int("128000")`` is the only reason
    numeric placeholders work at all.
    """
    result = provider
    for key in (
        "stream",
        "include_usage",
        "send_reasoning_effort",
        "require_api_key",
        "prompt_cache",
        "web_search",
    ):
        if key in result:
            result[key] = _coerce_bool(result[key], f"provider {name!r} {key}")
    if "trust_env" in result:
        result["trust_env"] = _coerce_bool(
            result["trust_env"], f"provider {name!r} trust_env"
        )
    for key in ("context_window", "max_tokens"):
        if key in result:
            result[key] = _coerce_int(result[key], f"provider {name!r} {key}")
    if "timeout_seconds" in result:
        result["timeout_seconds"] = _coerce_float(
            result["timeout_seconds"], f"provider {name!r} timeout_seconds"
        )
    for block_name in ("web_tools", "vision"):
        block = result.get(block_name)
        if not isinstance(block, dict):
            continue
        for key in ("enabled", "trust_env"):
            if key in block:
                block[key] = _coerce_bool(
                    block[key], f"provider {name!r} {block_name}.{key}"
                )
        for key in ("timeout_seconds",):
            if key in block:
                block[key] = _coerce_float(
                    block[key], f"provider {name!r} {block_name}.{key}"
                )
        for key in (
            "default_count",
            "max_count",
            "max_content_chars",
            "max_image_bytes",
            "max_chars",
        ):
            if key in block:
                block[key] = _coerce_int(
                    block[key], f"provider {name!r} {block_name}.{key}"
                )
    return result


def _placeholder_paths(
    value: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    """Return the dotted paths whose string value contained ``${VAR}``."""
    paths: set[tuple[str, ...]] = set()
    if isinstance(value, str):
        if _ENV_PATTERN.search(value):
            paths.add(prefix)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                paths |= _placeholder_paths(item, (*prefix, key))
    elif isinstance(value, list):
        for item in value:
            paths |= _placeholder_paths(item, prefix)
    return paths


def _coerce_keys(
    section: dict[str, Any],
    label: str,
    paths: set[tuple[str, ...]],
    *,
    bools: tuple[str, ...] = (),
    ints: tuple[str, ...] = (),
    floats: tuple[str, ...] = (),
) -> None:
    """Re-type only the values that came from an environment placeholder."""
    prefix = tuple(label.split("."))
    for key in bools:
        if key in section and (*prefix, key) in paths:
            section[key] = _coerce_bool(section[key], f"{label}.{key}")
    for key in ints:
        if key in section and (*prefix, key) in paths:
            section[key] = _coerce_int(section[key], f"{label}.{key}")
    for key in floats:
        if key in section and (*prefix, key) in paths:
            section[key] = _coerce_float(section[key], f"{label}.{key}")


def _coerce_root_values(data: dict[str, Any], paths: set[tuple[str, ...]]) -> None:
    """Re-type non-provider values that came from ``${ENV}`` placeholders.

    Provider blocks are expanded lazily by ``provider_config`` because a
    placeholder in an unselected provider must not fail startup, so only the
    root settings are coerced here. A plain string such as ``"false"`` stays a
    string and is still rejected by validation; only an expanded placeholder
    is re-typed.
    """
    if "max_turns" in data and ("max_turns",) in paths:
        data["max_turns"] = _coerce_int(data["max_turns"], "max_turns")
    shell = data.get("shell")
    if isinstance(shell, dict):
        _coerce_keys(
            shell,
            "shell",
            paths,
            ints=("max_output_chars",),
            floats=("timeout_seconds",),
        )
    tools = data.get("tools")
    if isinstance(tools, dict):
        _coerce_keys(
            tools,
            "tools",
            paths,
            bools=("allow_outside_workspace",),
            ints=(
                "max_parallel",
                "max_file_chars",
                "max_file_bytes",
                "max_results",
            ),
        )
    subagent = data.get("subagent")
    if isinstance(subagent, dict):
        _coerce_keys(
            subagent,
            "subagent",
            paths,
            bools=("enabled",),
            ints=("max_depth", "max_turns", "max_parallel"),
            floats=("timeout_seconds",),
        )
    context = data.get("context")
    if isinstance(context, dict):
        _coerce_keys(
            context,
            "context",
            paths,
            ints=("keep_messages", "max_instruction_chars"),
            floats=("compact_at",),
        )
    session = data.get("session")
    if isinstance(session, dict):
        _coerce_keys(session, "session", paths, bools=("save",))
    output = data.get("output")
    if isinstance(output, dict):
        _coerce_keys(output, "output", paths, bools=("markdown_highlight",))
    sandbox = data.get("sandbox")
    if isinstance(sandbox, dict):
        _coerce_keys(
            sandbox,
            "sandbox",
            paths,
            bools=("enabled", "allow_sensitive_files"),
            ints=(
                "max_output_chars",
                "max_memory_mb",
                "max_processes",
            ),
            floats=("timeout_seconds",),
        )


def _expand_root_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Expand ``${VAR}`` placeholders outside the provider table.

    Provider values are expanded by ``provider_config`` when that provider is
    actually used, so selecting another provider never fails on its missing
    environment variable.
    """
    expanded = dict(data)
    for key, value in data.items():
        if key != "providers":
            expanded[key] = expand_env(value)
    return expanded


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
    for name, provider_config in providers.items():
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{path}: provider names must be non-empty strings")
        if not isinstance(provider_config, dict):
            raise ConfigError(f"{path}: provider {name!r} must be an object")
        _validate_provider(name, provider_config, path)

    max_turns = data.get("max_turns")
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or not 1 <= max_turns <= 10_000
    ):
        raise ConfigError(f"{path}: max_turns must be an integer from 1 to 10000")

    for key in (
        "shell",
        "tools",
        "subagent",
        "context",
        "session",
        "output",
        "sandbox",
    ):
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
    allow = tools.get("allow")
    if allow is not None:
        _validate_string_list(allow, path, "tools.allow")
        if not allow:
            # An empty allowlist would leave an agent with no tools at all;
            # the runtime rejects it too, but a config error names the file.
            raise ConfigError(f"{path}: tools.allow must not be empty")
    _validate_integer(
        tools.get("max_parallel"),
        path,
        "tools.max_parallel",
        minimum=1,
        maximum=32,
    )
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

    subagent = data["subagent"]
    if not isinstance(subagent.get("enabled"), bool):
        raise ConfigError(f"{path}: subagent.enabled must be a boolean")
    _validate_integer(
        subagent.get("max_depth"),
        path,
        "subagent.max_depth",
        minimum=0,
        maximum=10,
    )
    _validate_integer(
        subagent.get("max_turns"),
        path,
        "subagent.max_turns",
        minimum=1,
        maximum=10_000,
    )
    _validate_integer(
        subagent.get("max_parallel"),
        path,
        "subagent.max_parallel",
        minimum=1,
        maximum=32,
    )
    _validate_number(
        subagent.get("timeout_seconds"),
        path,
        "subagent.timeout_seconds",
        minimum=1,
        maximum=3600,
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

    _validate_sandbox(data["sandbox"], path)

    highlight = data["output"].get("markdown_highlight")
    if not isinstance(highlight, bool):
        raise ConfigError(f"{path}: output.markdown_highlight must be a boolean")
    theme = data["output"].get("theme")
    if not isinstance(theme, str) or theme not in {"dark", "light"}:
        raise ConfigError(f"{path}: output.theme must be 'dark' or 'light'")

    if "thinking" in data:
        thinking = data["thinking"]
        # `--thinking High` is accepted (argparse lowercases it), so a settings
        # file with the same spelling must not be rejected as invalid.
        if isinstance(thinking, str):
            thinking = thinking.strip().lower()
            data["thinking"] = thinking
        if not isinstance(thinking, str) or thinking not in EFFORT_LEVELS:
            raise ConfigError(
                f"{path}: thinking must be one of {', '.join(EFFORT_LEVELS)}"
            )


def _has_env_placeholder(value: Any) -> bool:
    return isinstance(value, str) and _ENV_PATTERN.search(value) is not None


def _validate_provider(name: str, provider: dict[str, Any], path: Path) -> None:
    protocol = provider.get("protocol", "openai_chat")
    if not _has_env_placeholder(protocol) and protocol not in {
        "openai_chat",
        "openai_responses",
        "anthropic_messages",
        "deepseek_web",
    }:
        raise ConfigError(
            f"{path}: provider {name!r} uses unsupported protocol {protocol!r}"
        )

    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise ConfigError(f"{path}: provider {name!r} requires base_url")
    if not _has_env_placeholder(base_url):
        try:
            parsed = urlsplit(base_url)
            _ = parsed.port
            hostname = parsed.hostname
        except ValueError as exc:
            raise ConfigError(
                f"{path}: provider {name!r} has an invalid base_url"
            ) from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise ConfigError(f"{path}: provider {name!r} has an invalid base_url")

    model = provider.get("model")
    if (
        model is not None
        and not _has_env_placeholder(model)
        and (not isinstance(model, str) or not model.strip())
    ):
        raise ConfigError(f"{path}: provider {name!r} model must be a string")

    for key in (
        "stream",
        "include_usage",
        "send_reasoning_effort",
        "require_api_key",
        "prompt_cache",
        "web_search",
    ):
        if (
            key in provider
            and not _has_env_placeholder(provider[key])
            and not isinstance(provider[key], bool)
        ):
            raise ConfigError(f"{path}: provider {name!r} {key} must be a boolean")

    for key in ("context_window", "max_tokens"):
        if key in provider and not _has_env_placeholder(provider[key]):
            _validate_integer(
                provider[key],
                path,
                f"providers.{name}.{key}",
                minimum=1,
            )
    if "timeout_seconds" in provider and not _has_env_placeholder(
        provider["timeout_seconds"]
    ):
        _validate_number(
            provider["timeout_seconds"],
            path,
            f"providers.{name}.timeout_seconds",
            minimum=0,
            inclusive_minimum=False,
        )

    for key in ("headers", "extra_body"):
        if key in provider and not isinstance(provider[key], dict):
            raise ConfigError(f"{path}: provider {name!r} {key} must be an object")
    for key in ("api_key", "api_key_env", "cookie", "cookie_env"):
        value = provider.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: provider {name!r} {key} must be a string")
    for key in ("web_tools", "vision"):
        if key in provider and not isinstance(provider[key], dict):
            raise ConfigError(f"{path}: provider {name!r} {key} must be an object")


def _validate_string_list(value: Any, path: Path, key: str) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"{path}: {key} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}: {key} must contain non-empty strings")


def _validate_sandbox(sandbox: dict[str, Any], path: Path) -> None:
    if not isinstance(sandbox.get("enabled"), bool):
        raise ConfigError(f"{path}: sandbox.enabled must be a boolean")
    backend = sandbox.get("backend")
    if not isinstance(backend, str) or backend not in SANDBOX_BACKENDS:
        raise ConfigError(
            f"{path}: sandbox.backend must be one of {', '.join(SANDBOX_BACKENDS)}"
        )
    _validate_string_list(sandbox.get("allowed_roots"), path, "sandbox.allowed_roots")
    network_policy = sandbox.get("network_policy")
    if network_policy not in NETWORK_POLICIES:
        raise ConfigError(
            f"{path}: sandbox.network_policy must be one of "
            f"{', '.join(NETWORK_POLICIES)}"
        )
    _validate_string_list(sandbox.get("allowed_hosts"), path, "sandbox.allowed_hosts")
    if network_policy == "allowlist" and not sandbox.get("allowed_hosts"):
        raise ConfigError(
            f"{path}: sandbox.network_policy 'allowlist' requires sandbox.allowed_hosts"
        )
    _validate_number(
        sandbox.get("timeout_seconds"),
        path,
        "sandbox.timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    _validate_integer(
        sandbox.get("max_output_chars"),
        path,
        "sandbox.max_output_chars",
        minimum=1,
    )
    if not isinstance(sandbox.get("allow_sensitive_files"), bool):
        raise ConfigError(f"{path}: sandbox.allow_sensitive_files must be a boolean")
    _validate_integer(
        sandbox.get("max_memory_mb"),
        path,
        "sandbox.max_memory_mb",
        minimum=0,
        maximum=1_048_576,
    )
    _validate_integer(
        sandbox.get("max_processes"),
        path,
        "sandbox.max_processes",
        minimum=0,
        maximum=65_536,
    )
    docker_image = sandbox.get("docker_image")
    if not isinstance(docker_image, str) or not docker_image.strip():
        raise ConfigError(f"{path}: sandbox.docker_image must be a non-empty string")


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
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigError(f"{path}: {key} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ConfigError(f"{path}: {key} must be a finite number")
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
    # The settings file as written by the user (not merged with defaults and
    # not env-expanded). Config writes start from this, so `config set-model`
    # neither freezes every preset nor replaces `${VAR}` with its value.
    raw: dict[str, Any] | None = None

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
        expanded = _coerce_provider_values(provider_name, expanded)
        _validate_provider(provider_name, expanded, self.source)
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
        _coerce_root_values(data, set())
        _validate(data, source)
        return Settings(data=data, home=root, source=source, raw={})
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
    unexpanded = {key: value for key, value in data.items() if key != "providers"}
    placeholders = _placeholder_paths(unexpanded)
    data = _expand_root_settings(data)
    unresolved = _unresolved_env(
        {key: value for key, value in data.items() if key != "providers"}
    )
    if unresolved is not None:
        raise ConfigError(f"{source}: unset environment variable: {unresolved}")
    _coerce_root_values(data, placeholders)
    _validate(data, source)
    return Settings(data=data, home=root, source=source, raw=raw)


def _atomic_write(path: Path, text: str) -> None:
    # Write through a symlinked settings file instead of replacing the link
    # with a regular file (dotfile repositories are the common setup).
    try:
        path = Path(os.path.realpath(path))
    except (OSError, RuntimeError, ValueError):
        pass
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
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _ensure_home_layout(root: Path) -> Path:
    """Create the config home and session directory with private permissions."""
    path = root / SETTINGS_FILENAME
    root.mkdir(parents=True, exist_ok=True)
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
        os.chmod(sessions, 0o700)
    except OSError:
        pass
    return path


_OMIT = object()


def _prune_defaults(value: Any, default: Any) -> Any:
    """Drop values that the bundled defaults already provide.

    Persisting the merged settings froze every preset on disk: a later release
    could no longer correct an existing key. Writing only the user's
    differences keeps the defaults live. A missing key in the file simply
    means "use the default", which is the same effective value.
    """
    if isinstance(value, dict) and isinstance(default, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key not in default:
                result[key] = item
                continue
            pruned = _prune_defaults(item, default[key])
            if pruned is not _OMIT:
                result[key] = pruned
        return result if result else _OMIT
    if value == default:
        return _OMIT
    return value


def file_settings(settings: Settings) -> dict[str, Any]:
    """Return the settings document a user would edit.

    Bundled defaults are merged in so the output is complete, and ``raw``
    values are returned unexpanded so ``${VAR}`` placeholders stay visible.
    """
    return _deep_merge(DEFAULT_SETTINGS, copy.deepcopy(settings.raw or {}))


def write_settings(data: dict[str, Any], home: str | Path | None = None) -> Path:
    """Write a complete effective settings mapping.

    Keys equal to the bundled defaults are pruned before writing, so the file
    stays a delta and a later release can still correct a preset.
    """
    root = home_dir(home)
    path = _ensure_home_layout(root)
    _validate(data, path)
    pruned = _prune_defaults(data, DEFAULT_SETTINGS)
    payload = {} if pruned is _OMIT else pruned
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def write_settings_document(
    document: dict[str, Any],
    home: str | Path | None = None,
) -> Path:
    """Validate and persist a complete user settings document.

    The document is the raw file the user would edit: defaults are merged only
    for validation, and values matching a bundled default are pruned so a
    later release can still correct them.
    """
    root = home_dir(home)
    path = _ensure_home_layout(root)
    unexpanded = _deep_merge(DEFAULT_SETTINGS, copy.deepcopy(document))
    placeholders = _placeholder_paths(
        {key: value for key, value in unexpanded.items() if key != "providers"}
    )
    effective = _expand_root_settings(unexpanded)
    _coerce_root_values(effective, placeholders)
    _validate(effective, path)
    pruned = _prune_defaults(document, DEFAULT_SETTINGS)
    payload = {} if pruned is _OMIT else pruned
    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"cannot serialize settings: {exc}") from exc
    _atomic_write(path, text)
    return path


def save_settings(
    settings: Settings,
    updates: dict[str, Any],
    home: str | Path | None = None,
) -> Path:
    """Merge ``updates`` into the user's file and persist only that delta.

    ``settings.data`` is the effective view (defaults merged, environment
    expanded); writing it back would freeze every preset and replace
    placeholders with secrets. The raw file plus the requested keys is what a
    `config set-*` command actually changes.
    """
    document = _deep_merge(copy.deepcopy(settings.raw or {}), updates)
    # Fall back to the home the settings were loaded from: defaulting to
    # `home_dir(None)` elsewhere would write a different file than the one
    # these settings came from.
    return write_settings_document(document, home or settings.home)


def ensure_config(
    home: str | Path | None = None,
    *,
    overwrite: bool = False,
    minimal: bool = False,
) -> Path:
    """Create the settings file.

    ``minimal`` is used for the automatic first-run creation: pinning only the
    provider/model keeps every other value live under the bundled defaults, so
    a later release can still correct a preset. ``minimalcode init`` keeps
    writing the full template for users who want a file to edit.
    """
    root = home_dir(home)
    path = _ensure_home_layout(root)
    if overwrite or not path.exists():
        payload = (
            {
                "provider": DEFAULT_SETTINGS["provider"],
                "model": DEFAULT_SETTINGS["model"],
            }
            if minimal
            else DEFAULT_SETTINGS
        )
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "x-api-key",
}
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


_ENV_REFERENCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_HEADER_NAMES = {
    "accept",
    "accept-language",
    "anthropic-beta",
    "anthropic-version",
    "content-type",
    "user-agent",
}


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    if compact in _SAFE_COMPACT_KEYS:
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
            if (
                isinstance(key, str)
                and key.lower() == "headers"
                and isinstance(item, dict)
            ):
                # Custom headers carry credentials too, and an arbitrary name
                # such as `X-Auth` does not look sensitive. Only the standard
                # protocol headers are safe to print.
                result[key] = {
                    header: (
                        "<redacted>"
                        if str(header).lower() not in _SAFE_HEADER_NAMES
                        else header_value
                    )
                    for header, header_value in item.items()
                }
            elif (
                isinstance(key, str)
                and key.lower().endswith("env")
                and not (isinstance(item, str) and _ENV_REFERENCE_RE.fullmatch(item))
            ):
                # An `*_env` key is only safe when its value is a variable
                # name; a literal secret under that name must not print. A
                # non-string value is a setting (`trust_env`), not a name.
                result[key] = (
                    "<redacted>" if isinstance(item, str) else redact_config(item)
                )
            elif _is_sensitive_key(key):
                # An `*_env` entry holds the *name* of a variable, which is
                # safe and useful to print; any other shape is a value.
                if (
                    isinstance(key, str)
                    and key.lower().endswith("env")
                    and isinstance(item, str)
                    and _ENV_REFERENCE_RE.fullmatch(item)
                ):
                    result[key] = item
                else:
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
