from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
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
    print("Type /help for commands.")


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


def _interactive(
    agent: Agent,
    args: argparse.Namespace,
    settings: Settings,
    store: SessionStore,
    provider_name: str,
    model: str,
) -> int:
    from .agent import Agent
    from .tools import ToolRuntime

    _print_banner(
        provider_name,
        model,
        agent.shell.label,
        agent.provider.thinking,
        agent.session.cwd,
        agent.session,
    )
    while True:
        try:
            text = input("> ")
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
                provider=provider_name,
                model=model,
                save=save,
            )
            agent = Agent(
                settings=settings,
                provider=agent.provider,
                session=session,
                tool_runtime=ToolRuntime(
                    settings,
                    session.cwd,
                    agent.shell,
                    web_client=agent.tools.web_client,
                    vision_client=agent.tools.vision_client,
                    provider_name=agent.provider.name,
                    model=agent.provider.model,
                    thinking=agent.provider.thinking,
                ),
                shell=agent.shell,
                max_turns=args.max_turns,
                quiet=args.quiet,
                stream_output=not args.json_output,
                markdown_highlight=False if args.no_highlight else None,
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
        if command == "/help":
            print("/new  /clear  /compact  /session  /status  /effort  /exit")
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
    shell = detect_shell(settings.get("shell.path"))
    provider, provider_name, model = create_provider(
        settings,
        provider_name=args.provider,
        model=args.model,
        thinking=args.thinking,
    )
    web_client = None
    vision_client = None
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
