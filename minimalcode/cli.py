from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .config import (
    APP_NAME,
    Settings,
    ensure_config,
    home_dir,
    load_settings,
    redact_config,
    settings_path,
    write_settings,
)
from .errors import ConfigError, MinimalCodeError, ProviderError

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
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true")
    parser.add_argument("--session", help="Session ID, partial ID, or JSONL path")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--provider", help="Provider name from settings")
    parser.add_argument("-m", "--model", help="Provider model ID")
    parser.add_argument("--thinking", help="Provider reasoning effort")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--cwd", help="Workspace directory")
    parser.add_argument("--home", help="Config directory (default ~/.MinimalCode)")
    parser.add_argument("--quiet", action="store_true", help="Hide tool activity")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
        print(path)
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
        if args.action == "init":
            path = ensure_config(args.home)
            print(path)
            return 0
        if args.action == "path":
            print(settings_path(args.home))
            return 0
        settings = load_settings(args.home)
        if args.action == "set-model":
            if not args.value or not args.value.strip():
                raise ConfigError("model ID must not be empty")
            settings.data["model"] = args.value.strip()
            path = write_settings(settings.data, args.home)
            print(f"{path}: model={args.value.strip()}")
            return 0
        if args.action == "set-provider":
            if not args.value:
                raise ConfigError("provider name must not be empty")
            provider = args.value.strip()
            if provider not in settings.data["providers"]:
                raise ConfigError(f"provider {provider!r} is not defined")
            settings.data["provider"] = provider
            path = write_settings(settings.data, args.home)
            print(f"{path}: provider={provider}")
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
            print(
                f"{row['created_at']}  {row['session_id'][:10]}  "
                f"{row['provider']}/{row['model']}  {row['workspace']}"
            )
        return 0

    if command == "shell":
        from .shell import detect_shell

        parser = argparse.ArgumentParser(prog="minimalcode shell")
        parser.add_argument("--home")
        args = parser.parse_args(argv[1:])
        settings = load_settings(args.home)
        shell = detect_shell(settings.get("shell.path"))
        print(f"{shell.label}\t{shell.executable}")
        return 0

    if command == "import-pi":
        from .importer import import_pi_settings

        parser = argparse.ArgumentParser(prog="minimalcode import-pi")
        parser.add_argument("--home", help="MinimalCode config directory")
        parser.add_argument("--pi-home", help="Pi agent config directory")
        parser.add_argument("--no-secrets", action="store_true")
        parser.add_argument("--overwrite", action="store_true")
        args = parser.parse_args(argv[1:])
        result = import_pi_settings(
            destination_home=args.home or home_dir(),
            pi_home=args.pi_home,
            include_secrets=not args.no_secrets,
            overwrite=args.overwrite,
        )
        print(
            f"imported {result.provider}/{result.model} into {result.settings_path}"
        )
        if result.skipped:
            print(f"skipped: {', '.join(result.skipped)}")
        return 0
    return None


def _resolve_cwd(raw: str | None) -> Path:
    from .errors import ConfigError

    path = Path(raw).expanduser() if raw else Path.cwd()
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ConfigError(f"workspace is not a directory: {resolved}")
    return resolved


def _load_or_create_session(
    store: SessionStore,
    args: argparse.Namespace,
    settings: Settings,
    cwd: Path,
    provider: str,
    model: str,
) -> tuple[Session, Path]:
    from .errors import ConfigError

    if args.continue_session and args.session:
        raise ConfigError("--continue and --session cannot be used together")
    if args.continue_session:
        session = store.latest(cwd)
        if session is None:
            raise FileNotFoundError(f"no saved session for {cwd}")
        return session, session.cwd if not args.cwd else cwd
    if args.session:
        session = store.resolve(args.session, cwd)
        return session, session.cwd if not args.cwd else cwd
    save = bool(settings.get("session.save", True)) and not args.no_session
    return store.create(cwd, provider=provider, model=model, save=save), cwd


def _print_banner(
    provider: str,
    model: str,
    shell_label: str,
    cwd: Path,
    session: Session,
) -> None:
    print(f"{APP_NAME} {__version__} | {provider}/{model} | {shell_label}")
    print(f"cwd: {cwd}")
    if session.path:
        print(f"session: {session.path}")
    print("Type /help for commands.")


def _ensure_newline(result: AgentResult) -> None:
    if result.text and not result.text.endswith("\n"):
        print()


def _run_once(agent: Agent, args: argparse.Namespace, prompt: str) -> int:
    result = agent.run(prompt)
    if args.json_output:
        payload = {
            "text": result.text,
            "completed": result.completed,
            "turns": result.turns,
            "usage": result.usage,
            "session": str(agent.session.path) if agent.session.path else None,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        _ensure_newline(result)
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
                tool_runtime=ToolRuntime(settings, session.cwd, agent.shell),
                shell=agent.shell,
                max_turns=args.max_turns,
                quiet=args.quiet,
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
                print(f"compact failed: {exc}", file=sys.stderr)
                continue
            print("compacted" if changed else "nothing to compact")
            continue
        if command == "/session":
            print(agent.session.path or "<memory>")
            continue
        if command == "/help":
            print("/new  /clear  /compact  /session  /status  /exit")
            continue
        if text.startswith("/"):
            print(f"unknown command: {text}", file=sys.stderr)
            continue
        try:
            result = agent.run(text)
            _ensure_newline(result)
        except KeyboardInterrupt:
            print("\ncancelled", file=sys.stderr)
        except ProviderError as exc:
            print(f"provider error: {exc}", file=sys.stderr)


def run(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    managed = _management_command(raw_args)
    if managed is not None:
        return managed

    parser = _parser()
    args = parser.parse_args(raw_args)
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be at least 1")
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

    cwd = _resolve_cwd(args.cwd)
    settings = load_settings(args.home)
    if not settings.source.exists():
        ensure_config(args.home)
        settings = load_settings(args.home, require_file=True)
    shell = detect_shell(settings.get("shell.path"))
    provider, provider_name, model = create_provider(
        settings,
        provider_name=args.provider,
        model=args.model,
        thinking=args.thinking,
    )
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
        tools = ToolRuntime(settings, effective_cwd, shell)
        agent = Agent(
            settings=settings,
            provider=provider,
            session=session,
            tool_runtime=tools,
            shell=shell,
            max_turns=args.max_turns,
            quiet=args.quiet or args.json_output,
            stream_output=not args.json_output,
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
        provider.close()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    except (MinimalCodeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
