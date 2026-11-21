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
# Values accepted by the `latest_reminder` platform field and the `safety_check`
# level. Duplicated here rather than imported from `reminder.py` because that
# module imports this one.
REMINDER_PLATFORMS = frozenset({"App", "Web"})
SAFETY_LEVELS = frozenset({"low", "high"})
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


def valid_provider_name(name: str) -> bool:
    """True when a new provider name is safe to use as a settings key.

    The name becomes part of the settings document and of every error message,
    so it stays an identifier instead of being escaped later. Existing blocks are
    not re-validated: a name an older file already uses keeps working.
    """
    if not 1 <= len(name) <= 32:
        return False
    first = name[0]
    if not (first.isascii() and first.isalnum()):
        return False
    return all(char.isascii() and (char.isalnum() or char in "._-") for char in name)


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
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        _coerce_keys(
            mcp,
            "mcp",
            paths,
            bools=("enabled",),
            ints=("max_output_chars", "max_tools"),
            floats=("startup_timeout_seconds", "timeout_seconds"),
        )
        servers = mcp.get("servers")
        if isinstance(servers, dict):
            for name, server in servers.items():
                if not isinstance(server, dict):
                    continue
                _coerce_keys(
                    server,
                    f"mcp.servers.{name}",
                    paths,
                    bools=("enabled", "read_only"),
                    floats=("startup_timeout_seconds", "timeout_seconds"),
                )
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


def _validate_integrations(integrations: dict[str, Any], path: Path) -> None:
    """Validate the optional desktop-integration blocks.

    Every integration is opt-in decoration around the terminal UI, so a bad
    value must fail loudly here instead of silently disabling reporting at
    runtime.
    """
    clawd = integrations.get("clawd")
    if not isinstance(clawd, dict):
        raise ConfigError(f"{path}: integrations.clawd must be an object")
    if not isinstance(clawd.get("enabled"), bool):
        raise ConfigError(f"{path}: integrations.clawd.enabled must be a boolean")
    if not isinstance(clawd.get("agent_id"), str):
        raise ConfigError(f"{path}: integrations.clawd.agent_id must be a string")
    runtime_file = clawd.get("runtime_file")
    if runtime_file is not None and (
        not isinstance(runtime_file, str) or not runtime_file.strip()
    ):
        raise ConfigError(
            f"{path}: integrations.clawd.runtime_file must be null or a "
            "non-empty string"
        )
    _validate_number(
        clawd.get("timeout_seconds"),
        path,
        "integrations.clawd.timeout_seconds",
        minimum=0.1,
        maximum=10,
    )


def _validate_reminder(reminder: dict[str, Any], path: Path) -> None:
    """Validate the user meta information sent as a ``latest_reminder`` turn."""
    if not isinstance(reminder.get("enabled"), bool):
        raise ConfigError(f"{path}: reminder.enabled must be a boolean")
    location = reminder.get("location")
    if not isinstance(location, str):
        raise ConfigError(f"{path}: reminder.location must be a string")
    platform = reminder.get("platform")
    if platform not in REMINDER_PLATFORMS:
        raise ConfigError(
            f"{path}: reminder.platform must be one of "
            f"{', '.join(sorted(REMINDER_PLATFORMS))}"
        )
    language = reminder.get("language")
    if not isinstance(language, str) or not language.strip():
        raise ConfigError(f"{path}: reminder.language must be a non-empty string")


def _validate_session_title(title: dict[str, Any], path: Path, key: str) -> None:
    """Validate a provider's conversation-title switch."""
    if not isinstance(title.get("enabled"), bool):
        raise ConfigError(f"{path}: {key}.enabled must be a boolean")


def _validate_safety_check(safety: dict[str, Any], path: Path, key: str) -> None:
    """Validate a provider's model content-safety level.

    ``low`` is the API default (no server-side review loop) and ``high`` turns
    on the provider's basic risk control, so anything else is a typo that would
    otherwise run with no moderation at all.
    """
    level = safety.get("level")
    if level not in SAFETY_LEVELS:
        raise ConfigError(
            f"{path}: {key}.level must be one of {', '.join(sorted(SAFETY_LEVELS))}"
        )


def _validate_server_tools(block: dict[str, Any], path: Path, key: str) -> None:
    """Validate the DeepSeek server-side web tool switches.

    These tools are declared on the request and executed by the provider, so a
    typo has to be an error: a silently-off switch is indistinguishable from
    "the model chose not to search".
    """
    for field in ("enabled", "web_search", "read_url"):
        value = block.get(field)
        if value is not None and not isinstance(value, bool):
            raise ConfigError(f"{path}: {key}.{field} must be a boolean")


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
        "mcp",
        "integrations",
        "reminder",
    ):
        if not isinstance(data.get(key), dict):
            raise ConfigError(f"{path}: {key} must be an object")

    _validate_integrations(data["integrations"], path)
    _validate_reminder(data["reminder"], path)

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
    _validate_mcp(data["mcp"], path)

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
    for key in (
        "web_tools",
        "vision",
        "media",
        "server_tools",
        "session_title",
        "safety_check",
    ):
        if key in provider and not isinstance(provider[key], dict):
            raise ConfigError(f"{path}: provider {name!r} {key} must be an object")
    # The DeepSeek extensions below are opt-in per provider: they are rejected
    # by strict OpenAI-compatible servers, so a preset only turns them on for an
    # endpoint that documents them.
    if isinstance(provider.get("server_tools"), dict):
        _validate_server_tools(
            provider["server_tools"], path, f"providers.{name}.server_tools"
        )
    if isinstance(provider.get("session_title"), dict):
        _validate_session_title(
            provider["session_title"], path, f"providers.{name}.session_title"
        )
    if "thinking" in provider and not _has_env_placeholder(provider["thinking"]):
        # A provider may pin its own effort (a local model that only answers
        # well at `xhigh`, say). `--thinking` and `/effort` still win at runtime.
        thinking = provider["thinking"]
        if isinstance(thinking, str):
            provider["thinking"] = thinking = thinking.strip().lower()
        if not isinstance(thinking, str) or thinking not in EFFORT_LEVELS:
            raise ConfigError(
                f"{path}: provider {name!r} thinking must be one of "
                f"{', '.join(EFFORT_LEVELS)}"
            )


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


_MCP_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_MCP_TRANSPORTS = ("stdio", "http", "sse")


def _validate_string_map(value: Any, path: Path, key: str) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {key} must be an object of strings")
    for name, item in value.items():
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{path}: {key} keys must be non-empty strings")
        if not isinstance(item, str):
            raise ConfigError(f"{path}: {key}.{name} must be a string")


def _validate_mcp(mcp: dict[str, Any], path: Path) -> None:
    """Validate the MCP block and every server definition in it."""
    if not isinstance(mcp.get("enabled"), bool):
        raise ConfigError(f"{path}: mcp.enabled must be a boolean")
    _validate_number(
        mcp.get("startup_timeout_seconds"),
        path,
        "mcp.startup_timeout_seconds",
        minimum=1,
        maximum=300,
    )
    _validate_number(
        mcp.get("timeout_seconds"),
        path,
        "mcp.timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    _validate_integer(
        mcp.get("max_output_chars"),
        path,
        "mcp.max_output_chars",
        minimum=1,
    )
    _validate_integer(
        mcp.get("max_tools"),
        path,
        "mcp.max_tools",
        minimum=1,
        maximum=1000,
    )
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: mcp.servers must be an object")
    for name, server in servers.items():
        # The name becomes part of every tool name the model sees, so it is
        # restricted to identifier characters instead of being escaped later.
        if not isinstance(name, str) or not _MCP_SERVER_NAME.fullmatch(name):
            raise ConfigError(
                f"{path}: MCP server names must match "
                f"[A-Za-z0-9][A-Za-z0-9_-]{{0,31}} (got {name!r})"
            )
        if not isinstance(server, dict):
            raise ConfigError(f"{path}: mcp.servers.{name} must be an object")
        _validate_mcp_server(name, server, path)


def _validate_mcp_server(name: str, server: dict[str, Any], path: Path) -> None:
    label = f"mcp.servers.{name}"
    transport = server.get("transport", "stdio")
    if transport not in _MCP_TRANSPORTS:
        raise ConfigError(
            f"{path}: {label}.transport must be one of {', '.join(_MCP_TRANSPORTS)}"
        )
    for key in ("enabled", "read_only"):
        value = server.get(key)
        if value is not None and not isinstance(value, bool):
            raise ConfigError(f"{path}: {label}.{key} must be a boolean")
    # A per-server timeout is optional: omitting it inherits
    # ``mcp.timeout_seconds`` / ``mcp.startup_timeout_seconds``.
    if "timeout_seconds" in server:
        _validate_number(
            server["timeout_seconds"],
            path,
            f"{label}.timeout_seconds",
            minimum=1,
            maximum=3600,
        )
    if "startup_timeout_seconds" in server:
        _validate_number(
            server["startup_timeout_seconds"],
            path,
            f"{label}.startup_timeout_seconds",
            minimum=1,
            maximum=300,
        )
    for key in ("args", "include_tools", "exclude_tools"):
        value = server.get(key)
        if value is not None:
            _validate_string_list(value, path, f"{label}.{key}")
    if transport == "stdio":
        command = server.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"{path}: {label}.command must be a non-empty string")
        cwd = server.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise ConfigError(f"{path}: {label}.cwd must be null or a non-empty string")
        env = server.get("env")
        if env is not None:
            _validate_string_map(env, path, f"{label}.env")
        return
    url = server.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ConfigError(f"{path}: {label}.url must be a non-empty string")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{path}: {label}.url is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(f"{path}: {label}.url must be an http or https URL")
    headers = server.get("headers")
    if headers is not None:
        _validate_string_map(headers, path, f"{label}.headers")


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

    def reminder(self, now: Any = None) -> str | None:
        """Return the `latest_reminder` content for this configuration.

        Imported lazily because :mod:`minimalcode.reminder` reads this module's
        constants; the agent calls this once per turn, right before the user
        message it annotates.
        """
        from .reminder import build_reminder

        return build_reminder(self, now=now)


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
