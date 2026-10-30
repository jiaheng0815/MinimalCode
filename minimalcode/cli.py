from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .config import (
    APP_NAME,
    EFFORT_LEVELS,
    Settings,
    ensure_config,
    home_dir,
    load_settings,
    redact_config,
    settings_path,
    write_settings,
)
from .errors import ConfigError, MinimalCodeError, ProviderError
from .highlight import sanitize_terminal_text, style_activity_tag
from .sandbox import NETWORK_POLICIES, SANDBOX_BACKENDS

if TYPE_CHECKING:
    from .agent import Agent, AgentResult
    from .provider.anthropic_env import AnthropicEnvConfig
    from .session import Session, SessionStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minimalcode",
        description="Fast, minimal terminal coding agent.",
    )
    parser.add_argument("prompt", nargs="*", help="Initial message")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true")
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.add_argument(
        "-c", "--continue", dest="continue_session", action="store_true"
    )
    parser.add_argument("--session", help="Session ID, partial ID, or JSONL path")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--provider", help="Provider name from settings")
    parser.add_argument(
        "--anthropic-env",
        action="store_true",
        help=(
            "Use the Anthropic Messages protocol configured by Claude Code style "
            "ANTHROPIC_* / CLAUDE_CODE_* environment variables without editing "
            "settings.json"
        ),
    )
    parser.add_argument("-m", "--model", help="Provider model ID")
    parser.add_argument(
        "--thinking",
        type=str.lower,
        choices=EFFORT_LEVELS,
        metavar="EFFORT",
        help=(
            f"Provider reasoning effort ({', '.join(EFFORT_LEVELS)}); "
            "'none' disables thinking"
        ),
    )
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--cwd", help="Workspace directory")
    parser.add_argument("--home", help="Config directory (default ~/.MinimalCode)")
    parser.add_argument(
        "--sandbox",
        dest="sandbox_enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable the sandbox execution layer (off by default). The workspace "
            "and sensitive-file boundary applies either way; any --sandbox-* "
            "option also enables it"
        ),
    )
    parser.add_argument(
        "--sandbox-backend",
        choices=SANDBOX_BACKENDS,
        help="Sandbox backend: auto, local, bwrap, firejail, sandbox-exec, docker",
    )
    parser.add_argument(
        "--sandbox-root",
        action="append",
        metavar="PATH",
        help="Extra writable/readable root (repeatable)",
    )
    parser.add_argument(
        "--sandbox-network",
        choices=NETWORK_POLICIES,
        help="Network policy: deny (default), allowlist, or allow",
    )
    parser.add_argument(
        "--sandbox-allow-host",
        action="append",
        metavar="HOST",
        help="Allowed host for --sandbox-network allowlist (repeatable)",
    )
    parser.add_argument(
        "--sandbox-timeout",
        type=float,
        metavar="SECONDS",
        help="Maximum runtime for one sandboxed command",
    )
    parser.add_argument(
        "--sandbox-max-output",
        type=int,
        metavar="CHARS",
        help="Maximum stdout/stderr returned for a sandboxed command",
    )
    parser.add_argument(
        "--sandbox-allow-sensitive",
        dest="sandbox_allow_sensitive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow sandboxed tools to read and write credential-like files",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide tool activity and the token usage summary",
    )
    parser.add_argument(
        "--no-highlight",
        action="store_true",
        help="Show plain Markdown without terminal highlighting",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def _management_command(argv: Sequence[str]) -> int | None:
    if not argv:
        return None
    command = argv[0]
    if command == "init":
        parser = argparse.ArgumentParser(prog="minimalcode init")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--home")
        args = parser.parse_args(argv[1:])
        path = ensure_config(args.home, overwrite=args.force)
        print(sanitize_terminal_text(str(path)))
        return 0

    if command == "config":
        parser = argparse.ArgumentParser(prog="minimalcode config")
        parser.add_argument(
            "action",
            nargs="?",
            choices=("show", "path", "init", "set-model", "set-provider"),
            default="show",
        )
        parser.add_argument("value", nargs="?")
        parser.add_argument("--home")
        args = parser.parse_args(argv[1:])
        if args.action in {"show", "path", "init"} and args.value is not None:
            parser.error(f"unexpected argument: {args.value}")
        if args.action == "init":
            path = ensure_config(args.home)
            print(sanitize_terminal_text(str(path)))
            return 0
        if args.action == "path":
            print(sanitize_terminal_text(str(settings_path(args.home))))
            return 0
        settings = load_settings(args.home)
        if args.action == "set-model":
            if not args.value or not args.value.strip():
                raise ConfigError("model ID must not be empty")
            settings.data["model"] = args.value.strip()
            path = write_settings(settings.data, args.home)
            print(sanitize_terminal_text(f"{path}: model={args.value.strip()}"))
            return 0
        if args.action == "set-provider":
            if not args.value:
                raise ConfigError("provider name must not be empty")
            provider = args.value.strip()
            if provider not in settings.data["providers"]:
                raise ConfigError(f"provider {provider!r} is not defined")
            settings.data["provider"] = provider
            path = write_settings(settings.data, args.home)
            print(sanitize_terminal_text(f"{path}: provider={provider}"))
            return 0
        print(
            json.dumps(
                redact_config(settings.data),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if command == "sessions":
        from .session import SessionStore

        parser = argparse.ArgumentParser(prog="minimalcode sessions")
        parser.add_argument("--home")
        args = parser.parse_args(argv[1:])
        settings = load_settings(args.home)
        rows = SessionStore(settings).list()
        for row in rows:
            line = (
                f"{row['created_at']}  {row['session_id'][:10]}  "
                f"{row['provider']}/{row['model']}  {row['workspace']}"
            )
            print(sanitize_terminal_text(line))
        return 0

    if command == "shell":
        from .shell import detect_shell

        parser = argparse.ArgumentParser(prog="minimalcode shell")
        parser.add_argument("--home")
        args = parser.parse_args(argv[1:])
        settings = load_settings(args.home)
        shell = detect_shell(settings.get("shell.path"))
        print(sanitize_terminal_text(f"{shell.label}\t{shell.executable}"))
        return 0

    if command == "sandbox":
        from .sandbox import SandboxRunner, available_backends

        parser = argparse.ArgumentParser(prog="minimalcode sandbox")
        parser.add_argument("--home")
        parser.add_argument("--cwd", help="Workspace directory")
        args = parser.parse_args(argv[1:])
        settings = load_settings(args.home)
        workspace = _resolve_cwd(args.cwd)
        runner = SandboxRunner.from_settings(settings, workspace)
        payload = runner.summary()
        payload["available_backends"] = available_backends()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if command == "import-pi":
        from .importer import import_pi_settings

        parser = argparse.ArgumentParser(prog="minimalcode import-pi")
        parser.add_argument("--home", help="MinimalCode config directory")
        parser.add_argument("--pi-home", help="Pi agent config directory")
        # Copying credentials is opt-in: a migration command should not write a
        # second plaintext copy of an API key unless it is asked to.
        secrets = parser.add_mutually_exclusive_group()
        secrets.add_argument(
            "--secrets",
            dest="copy_secrets",
            action="store_true",
            help="Copy API keys from Pi into settings.json",
        )
        secrets.add_argument(
            "--no-secrets",
            dest="copy_secrets",
            action="store_false",
            help="Skip API keys and reference environment variables (default)",
        )
        parser.set_defaults(copy_secrets=False)
        parser.add_argument("--overwrite", action="store_true")
        args = parser.parse_args(argv[1:])
        result = import_pi_settings(
            destination_home=args.home or home_dir(),
            pi_home=args.pi_home,
            include_secrets=args.copy_secrets,
            overwrite=args.overwrite,
        )
        print(
            sanitize_terminal_text(
                f"imported {result.provider}/{result.model} into {result.settings_path}"
            )
        )
        if result.skipped:
            print(sanitize_terminal_text(f"skipped: {', '.join(result.skipped)}"))
        return 0
    return None


def _resolve_cwd(raw: str | None) -> Path:
    try:
        path = Path(raw).expanduser() if raw else Path.cwd()
        resolved = path.resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid workspace: {raw}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"workspace is not a directory: {resolved}")
    return resolved


def _sandbox_tuning_requested(args: argparse.Namespace) -> bool:
    """Return True when the CLI asked for sandbox behaviour, not just its state."""
    if args.sandbox_backend or args.sandbox_root or args.sandbox_network:
        return True
    if args.sandbox_allow_host:
        return True
    if args.sandbox_timeout is not None or args.sandbox_max_output is not None:
        return True
    return args.sandbox_allow_sensitive is not None


def _apply_sandbox_overrides(settings: Settings, args: argparse.Namespace) -> None:
    """Apply CLI sandbox flags on top of the loaded settings (in memory)."""
    sandbox = settings.data.setdefault("sandbox", {})
    if args.sandbox_enabled is not None:
        sandbox["enabled"] = args.sandbox_enabled
    elif _sandbox_tuning_requested(args):
        # The sandbox is off by default, so a flag that only tunes it would
        # otherwise be a silent no-op.
        sandbox["enabled"] = True
    if args.sandbox_backend is not None:
        sandbox["backend"] = args.sandbox_backend
    if args.sandbox_root:
        roots = list(sandbox.get("allowed_roots") or [])
        for root in args.sandbox_root:
            if root not in roots:
                roots.append(root)
        sandbox["allowed_roots"] = roots
    if args.sandbox_network is not None:
        sandbox["network_policy"] = args.sandbox_network
    if args.sandbox_allow_host:
        hosts = list(sandbox.get("allowed_hosts") or [])
        for host in args.sandbox_allow_host:
            cleaned = host.strip().lower()
            if cleaned and cleaned not in hosts:
                hosts.append(cleaned)
        sandbox["allowed_hosts"] = hosts
    if args.sandbox_timeout is not None:
        sandbox["timeout_seconds"] = args.sandbox_timeout
    if args.sandbox_max_output is not None:
        sandbox["max_output_chars"] = args.sandbox_max_output
    if args.sandbox_allow_sensitive is not None:
        sandbox["allow_sensitive_files"] = args.sandbox_allow_sensitive
    if sandbox.get("network_policy") == "allowlist" and not sandbox.get(
        "allowed_hosts"
    ):
        raise ConfigError(
            "--sandbox-network allowlist requires at least one "
            "--sandbox-allow-host (or sandbox.allowed_hosts)"
        )


def _apply_anthropic_env(
    settings: Settings, args: argparse.Namespace
) -> AnthropicEnvConfig:
    """Install the environment-derived provider in memory and return it.

    The settings file stays untouched: only this process's ``settings.data``
    gains the provider entry, so the next ordinary run behaves as before.
    """
    from .provider.anthropic_env import from_anthropic_env

    env_config = from_anthropic_env(model=args.model, thinking=args.thinking)
    settings.data.setdefault("providers", {})[env_config.provider] = env_config.config
    settings.data["provider"] = env_config.provider
    if env_config.compact_at is not None:
        settings.data.setdefault("context", {})["compact_at"] = env_config.compact_at
    return env_config


def _load_or_create_session(
    store: SessionStore,
    args: argparse.Namespace,
    settings: Settings,
    cwd: Path,
    provider: str,
    model: str,
) -> tuple[Session, Path]:
    if args.continue_session and args.session:
        raise ConfigError("--continue and --session cannot be used together")
    if args.no_session and (args.continue_session or args.session):
        raise ConfigError(
            "--no-session cannot be combined with --continue or --session"
        )
    if args.continue_session:
        session = store.latest(cwd)
        if session is None:
            raise FileNotFoundError(f"no saved session for {cwd}")
        if args.cwd:
            session.cwd = cwd
        return session, session.cwd
    if args.session:
        identifier = args.session.strip()
        if not identifier:
            raise ConfigError("--session must not be empty")
        session = store.resolve(identifier, cwd)
        if args.cwd:
            session.cwd = cwd
        return session, session.cwd
    save = bool(settings.get("session.save", True)) and not args.no_session
    return store.create(cwd, provider=provider, model=model, save=save), cwd


def _print_banner(
    provider: str,
    model: str,
    shell_label: str,
    effort: str,
    cwd: Path,
    session: Session,
) -> None:
    print(
        sanitize_terminal_text(
            f"{APP_NAME} {__version__} | {provider}/{model} | {shell_label}"
            f" | effort {effort}"
        )
    )
    print(f"cwd: {sanitize_terminal_text(str(cwd))}")
    if session.path:
        print(f"session: {sanitize_terminal_text(str(session.path))}")
    print("Type /help for commands. End a line with \\ to continue, or paste text.")


def _ensure_newline(result: AgentResult) -> None:
    if result.text and not result.text.endswith("\n"):
        print()


def _format_usage(usage: dict[str, int], *, estimated: bool = False) -> str:
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0) or prompt + completion
    if not (prompt or completion or total):
        return ""
    parts = [
        f"{prompt:,} prompt",
        f"{completion:,} completion",
        f"{total:,} total",
    ]
    cached = int(usage.get("cached_tokens", 0) or 0)
    reasoning = int(usage.get("reasoning_tokens", 0) or 0)
    if cached:
        parts.append(f"{cached:,} cached")
    if reasoning:
        parts.append(f"{reasoning:,} reasoning")
    line = "[usage] " + " | ".join(parts)
    if estimated:
        # Say so instead of presenting a character-count guess as billed usage.
        line += " (estimated)"
    return line


def _print_usage(result: AgentResult) -> None:
    # The summary goes to stderr so piped stdout still contains only the answer.
    usage = getattr(result, "run_usage", None) or getattr(result, "usage", None)
    if not usage:
        return
    line = _format_usage(
        usage, estimated=bool(getattr(result, "usage_estimated", False))
    )
    if line:
        print(style_activity_tag(line, sys.stderr), file=sys.stderr)
        sys.stderr.flush()


def _handle_effort(agent: Agent, text: str) -> None:
    parts = text.split()
    if len(parts) > 2:
        print(f"usage: /effort [{'|'.join(EFFORT_LEVELS)}]", file=sys.stderr)
        return
    if len(parts) == 1:
        print(
            f"effort: {agent.provider.thinking} (available: {', '.join(EFFORT_LEVELS)})"
        )
        return
    requested = parts[1].lower()
    if requested not in EFFORT_LEVELS:
        print(
            f"unknown effort: {sanitize_terminal_text(parts[1])} "
            f"(choose from {', '.join(EFFORT_LEVELS)})",
            file=sys.stderr,
        )
        return
    agent.provider.thinking = requested
    tools = getattr(agent, "tools", None)
    if tools is not None and hasattr(tools, "thinking"):
        tools.thinking = requested
    print(f"effort: {requested}")


def _provider_credential(config: dict[str, object]) -> tuple[str, bool]:
    """Describe a provider key without printing its value and say if it is usable."""
    if config.get("api_key"):
        return "inline key (set)", True
    headers = config.get("headers")
    if isinstance(headers, dict):
        names = {str(name).lower() for name in headers}
        if "authorization" in names or "x-api-key" in names:
            return "header credential (set)", True
    env_name = config.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        if os.environ.get(env_name):
            return f"{env_name} (set)", True
        if config.get("require_api_key", True) is False:
            return f"{env_name} (not required)", True
        return f"{env_name} (missing)", False
    if config.get("require_api_key", True) is False:
        return "no key required", True
    return "no key", False


def _print_models(agent: Agent, settings: Settings) -> None:
    print(
        "model: "
        f"{sanitize_terminal_text(str(agent.provider.name))}/"
        f"{sanitize_terminal_text(str(agent.provider.model))}"
    )
    providers = settings.data.get("providers", {})
    detected: list[tuple[str, str, str]] = []
    for name in sorted(providers):
        config = providers[name]
        if not isinstance(config, dict):
            continue
        credential, available = _provider_credential(config)
        if not available:
            continue
        default_model = config.get("model")
        model_text = (
            str(default_model)
            if isinstance(default_model, str) and default_model.strip()
            else "-"
        )
        detected.append((str(name), model_text, credential))
    if detected:
        print(f"providers with detected credentials ({len(detected)}):")
        for name, model_text, credential in detected:
            print(
                "  "
                f"{sanitize_terminal_text(name)}  "
                f"{sanitize_terminal_text(model_text)}  "
                f"{sanitize_terminal_text(credential)}"
            )
    else:
        print("no provider credentials detected")
    print("use /model provider/model to switch")


def _switch_runtime(
    agent: Agent,
    settings: Settings,
    provider_name: str,
    model: str,
) -> tuple[str, str]:
    """Build and install a new provider without losing the session history."""
    from .provider import create_provider
    from .tools import ToolRuntime
    from .vision import ProviderVisionClient
    from .web import ProviderWebClient

    provider, name, resolved_model = create_provider(
        settings,
        provider_name=provider_name,
        model=model,
        thinking=agent.provider.thinking,
    )
    web_client: ProviderWebClient | None = None
    vision_client: ProviderVisionClient | None = None
    try:
        web_client = ProviderWebClient(name, provider.config)
        vision_client = ProviderVisionClient(
            name, provider.config, model=resolved_model
        )
        tools = ToolRuntime(
            settings,
            agent.session.cwd,
            agent.shell,
            web_client=web_client,
            vision_client=vision_client,
            provider_name=name,
            model=resolved_model,
            thinking=provider.thinking,
        )
        old_provider = agent.provider
        old_tools = agent.tools
        agent.switch_runtime(provider, tools)
    except BaseException:
        if web_client is not None:
            web_client.close()
        if vision_client is not None:
            vision_client.close()
        provider.close()
        raise
    old_provider.close()
    for client in (old_tools.web_client, old_tools.vision_client):
        if client is not None:
            client.close()
    return name, resolved_model


def _handle_model(agent: Agent, settings: Settings, text: str) -> bool:
    parts = text.split()
    if len(parts) == 1:
        _print_models(agent, settings)
        return False
    if len(parts) != 2:
        print("usage: /model [provider/model]", file=sys.stderr)
        return False

    provider_name, separator, model = parts[1].partition("/")
    provider_name = provider_name.strip()
    model = model.strip()
    if not provider_name or (separator and not model):
        print("usage: /model [provider/model]", file=sys.stderr)
        return False

    providers = settings.data.get("providers")
    config = providers.get(provider_name) if isinstance(providers, dict) else None
    if not isinstance(config, dict):
        available = ", ".join(sorted(providers)) if isinstance(providers, dict) else ""
        message = f"unknown provider: {sanitize_terminal_text(provider_name)}"
        if available:
            message += f" (available: {sanitize_terminal_text(available)})"
        print(message, file=sys.stderr)
        return False
    if not separator:
        try:
            _, resolved_config = settings.provider_config(provider_name)
        except ConfigError as exc:
            print(
                f"model switch failed: {sanitize_terminal_text(str(exc))}",
                file=sys.stderr,
            )
            return False
        default_model = resolved_config.get("model")
        if not isinstance(default_model, str) or not default_model.strip():
            print(
                f"provider {sanitize_terminal_text(provider_name)} has no default "
                f"model; use /model {sanitize_terminal_text(provider_name)}/<model>",
                file=sys.stderr,
            )
            return False
        model = default_model.strip()

    try:
        name, resolved_model = _switch_runtime(agent, settings, provider_name, model)
    except (MinimalCodeError, OSError, TypeError, ValueError) as exc:
        print(
            f"model switch failed: {sanitize_terminal_text(str(exc))}",
            file=sys.stderr,
        )
        return False
    print(
        "model: "
        f"{sanitize_terminal_text(name)}/{sanitize_terminal_text(resolved_model)}"
    )
    return True


def _run_once(agent: Agent, args: argparse.Namespace, prompt: str) -> int:
    result = agent.run(prompt)
    if args.json_output:
        payload = {
            "text": result.text,
            "completed": result.completed,
            "turns": result.turns,
            # ``usage`` is this run; ``session_usage`` is the whole session, so a
            # resumed run cannot silently report the cumulative total as its own.
            "usage": result.run_usage,
            "session_usage": result.usage,
            "usage_estimated": result.usage_estimated,
            "session": str(agent.session.path) if agent.session.path else None,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        _ensure_newline(result)
        if not args.quiet:
            _print_usage(result)
    return 0 if result.completed else 1


_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"
_PASTE_SETTLE_SECONDS = 0.02


def _terminal_can_paste() -> bool:
    """Return True when bracketed paste can be enabled safely."""
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _write_terminal(sequence: str) -> None:
    try:
        sys.stdout.write(sequence)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


@contextmanager
def _bracketed_paste() -> Iterator[None]:
    """Let the terminal mark pasted text so its newlines do not submit."""
    enabled = _terminal_can_paste()
    if enabled:
        from .highlight.color import _enable_windows_ansi

        # On Windows the enable sequence only becomes a control code once
        # virtual-terminal output is on; otherwise it prints as garbage.
        enabled = _enable_windows_ansi(sys.stdout)
    if enabled:
        _write_terminal("\x1b[?2004h")
    try:
        yield
    finally:
        if enabled:
            _write_terminal("\x1b[?2004l")


def _read_paste(line: str) -> str:
    """Collect a bracketed paste that continues after its first line.

    Reading stops exactly at the closing marker, so a newline inside the pasted
    block never submits the message and text typed after the block is left for
    the next prompt.
    """
    before, _, pasted = line.partition(_PASTE_START)
    while _PASTE_END not in pasted:
        try:
            char = sys.stdin.read(1)
        except (AttributeError, OSError, ValueError):
            break
        if not char:
            break
        pasted += char
    end = pasted.find(_PASTE_END)
    return before + (pasted if end < 0 else pasted[:end])


def _input_pending(timeout: float = _PASTE_SETTLE_SECONDS) -> bool:
    """Return True when stdin already holds more input (a multi-line paste).

    A terminal without bracketed-paste support hands a pasted block over one
    line at a time, so the lines are drained with a short settle window before
    the first line is submitted as a message.
    """
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            return False
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)
    try:
        import select
    except ImportError:
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return False
    return bool(readable)


def _read_message(prompt: str = "> ") -> str:
    """Read one message, keeping pasted lines and manual continuations.

    A trailing backslash continues the message on the next line; ``\\\\`` at the
    end of the last line means a literal backslash instead.
    """
    line = input(prompt)
    if _PASTE_START in line:
        return _read_paste(line)
    lines = [line]
    while _input_pending():
        lines.append(input(""))
    if len(lines) > 1:
        # Pasted lines are literal text: no continuation markers to interpret.
        return "\n".join(lines)
    while lines[-1].endswith("\\") and not lines[-1].endswith("\\\\"):
        lines[-1] = lines[-1][:-1]
        lines.append(input("... "))
    if lines[-1].endswith("\\\\"):
        lines[-1] = lines[-1][:-1]
    return "\n".join(lines)


def _interactive(
    agent: Agent,
    args: argparse.Namespace,
    settings: Settings,
    store: SessionStore,
    provider_name: str,
    model: str,
) -> int:
    _print_banner(
        provider_name,
        model,
        agent.shell.label,
        agent.provider.thinking,
        agent.session.cwd,
        agent.session,
    )
    with _bracketed_paste():
        return _chat_loop(agent, args, settings, store)


def _chat_loop(
    agent: Agent,
    args: argparse.Namespace,
    settings: Settings,
    store: SessionStore,
) -> int:
    from .tools import ToolRuntime

    while True:
        try:
            text = _read_message("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        text = text.strip()
        if not text:
            continue
        command = text.lower()
        if command in {"/exit", "/quit"}:
            return 0
        if command in {"/new", "/clear"}:
            save = bool(settings.get("session.save", True)) and not args.no_session
            session = store.create(
                agent.session.cwd,
                provider=agent.provider.name,
                model=agent.provider.model,
                save=save,
            )
            agent.new_session(
                session,
                ToolRuntime(
                    settings,
                    session.cwd,
                    agent.shell,
                    web_client=agent.tools.web_client,
                    vision_client=agent.tools.vision_client,
                    provider_name=agent.provider.name,
                    model=agent.provider.model,
                    thinking=agent.provider.thinking,
                ),
            )
            print("new session")
            continue
        if command == "/status":
            print(json.dumps(agent.status(), indent=2, ensure_ascii=False))
            continue
        if command == "/compact":
            try:
                changed = agent.compact()
            except (ProviderError, RuntimeError) as exc:
                print(
                    f"compact failed: {sanitize_terminal_text(str(exc))}",
                    file=sys.stderr,
                )
                continue
            print("compacted" if changed else "nothing to compact")
            continue
        if command == "/summary":
            try:
                summary_path = agent.write_summary()
            except (MinimalCodeError, OSError) as exc:
                print(
                    f"summary failed: {sanitize_terminal_text(str(exc))}",
                    file=sys.stderr,
                )
                continue
            print(f"summary written to {sanitize_terminal_text(str(summary_path))}")
            continue
        if command == "/session":
            print(
                sanitize_terminal_text(
                    str(agent.session.path) if agent.session.path else "<memory>"
                )
            )
            continue
        if command.split()[0] == "/effort":
            _handle_effort(agent, text)
            continue
        if command.split()[0] == "/model":
            _handle_model(agent, settings, text)
            continue
        if command == "/help":
            print(
                "/new  /clear  /compact  /summary  /session  /status  "
                "/model  /effort  /exit"
            )
            print("multiline: end a line with \\ to continue, or paste a block")
            continue
        if text.startswith("/"):
            print(f"unknown command: {sanitize_terminal_text(text)}", file=sys.stderr)
            continue
        try:
            result = agent.run(text)
            _ensure_newline(result)
            if not args.quiet:
                _print_usage(result)
        except KeyboardInterrupt:
            print("\ncancelled", file=sys.stderr)
        except (MinimalCodeError, OSError) as exc:
            # A failed turn must not end the session: report and keep reading.
            print(f"error: {sanitize_terminal_text(str(exc))}", file=sys.stderr)


def run(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # `minimalcode -- init` is the prompt "init", not the subcommand: a bare
    # management word is otherwise consumed before argparse ever sees it.
    if "--" not in raw_args:
        managed = _management_command(raw_args)
        if managed is not None:
            return managed

    parser = _parser()
    args = parser.parse_args(raw_args)
    if args.max_turns is not None and not 1 <= args.max_turns <= 10_000:
        parser.error("--max-turns must be from 1 to 10000")
    if args.sandbox_timeout is not None and (
        not math.isfinite(args.sandbox_timeout) or not 1 <= args.sandbox_timeout <= 3600
    ):
        parser.error("--sandbox-timeout must be from 1 to 3600")
    if args.sandbox_max_output is not None and args.sandbox_max_output < 1:
        parser.error("--sandbox-max-output must be at least 1")
    if args.json_output:
        args.print_mode = True
    if args.anthropic_env and args.provider:
        parser.error("--anthropic-env cannot be combined with --provider")

    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt and args.print_mode:
        parser.error("--print requires a prompt or piped stdin")

    from .agent import Agent
    from .provider import create_provider
    from .session import SessionStore
    from .shell import detect_shell
    from .tools import ToolRuntime
    from .vision import ProviderVisionClient
    from .web import ProviderWebClient

    cwd = _resolve_cwd(args.cwd)
    settings = load_settings(args.home)
    if not settings.source.exists():
        ensure_config(args.home)
        settings = load_settings(args.home, require_file=True)
    _apply_sandbox_overrides(settings, args)
    env_config = _apply_anthropic_env(settings, args) if args.anthropic_env else None
    shell = detect_shell(settings.get("shell.path"))
    provider, provider_name, model = create_provider(
        settings,
        provider_name=args.provider,
        model=args.model,
        thinking=args.thinking or (env_config.thinking if env_config else None),
    )
    web_client = None
    vision_client = None
    agent = None
    try:
        store = SessionStore(settings)
        session, effective_cwd = _load_or_create_session(
            store,
            args,
            settings,
            cwd,
            provider_name,
            model,
        )
        if not effective_cwd.is_dir():
            raise ConfigError(f"session workspace no longer exists: {effective_cwd}")
        web_client = ProviderWebClient(provider_name, provider.config)
        vision_client = ProviderVisionClient(
            provider_name, provider.config, model=model
        )
        tools = ToolRuntime(
            settings,
            effective_cwd,
            shell,
            web_client=web_client,
            vision_client=vision_client,
            provider_name=provider_name,
            model=model,
            thinking=provider.thinking,
        )
        agent = Agent(
            settings=settings,
            provider=provider,
            session=session,
            tool_runtime=tools,
            shell=shell,
            max_turns=args.max_turns,
            quiet=args.quiet or args.json_output,
            stream_output=not args.json_output,
            markdown_highlight=False if args.no_highlight else None,
        )

        if prompt:
            return _run_once(agent, args, prompt)
        return _interactive(
            agent,
            args,
            settings,
            store,
            provider_name,
            model,
        )
    finally:
        # `/model` swaps the provider and its optional clients. Close whatever is
        # active now instead of the objects created before the interactive loop.
        if agent is not None:
            web_client = agent.tools.web_client
            vision_client = agent.tools.vision_client
            provider = agent.provider
        if web_client is not None:
            web_client.close()
        if vision_client is not None:
            vision_client.close()
        provider.close()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    except (MinimalCodeError, OSError, ValueError) as exc:
        print(f"error: {sanitize_terminal_text(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
