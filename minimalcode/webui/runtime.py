"""State for the chat web UI: settings, workspace, and one streamed turn.

The browser is only a view. Every tool still runs through the same
``ToolRuntime`` the CLI uses, bound to the workspace the user picked here, so
the web UI cannot widen the sandbox, the tool allowlist, or the file boundary.
"""

from __future__ import annotations

import os
import string
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__
from ..agent import Agent, AgentResult
from ..config import (
    ConfigError,
    Settings,
    provider_api_key,
    write_settings_document,
)
from ..errors import MinimalCodeError
from ..highlight import strip_ansi
from ..media import ProviderMediaClient
from ..provider import create_provider
from ..session import Session, SessionStore
from ..shell import detect_shell
from ..tools import ToolRuntime
from ..vision import ProviderVisionClient
from ..web import ProviderWebClient
from .library import FolderLibrary
from .thinking import ThinkingLog

# The wire formats a provider block may use. Kept in one place because the
# settings panel offers exactly these and the override is validated here.
PROTOCOLS = ("openai_chat", "openai_responses", "anthropic_messages", "deepseek_web")
# Roles a browser transcript can render. `latest_reminder` is a per-turn
# DeepSeek extension and tool results belong to the tool loop, not the chat.
_VISIBLE_ROLES = {"user": "user", "assistant": "assistant"}

Emitter = Callable[[str, dict[str, Any]], None]


def _folder_index(settings: Settings) -> FolderLibrary:
    """Return the folder index the sidebar groups conversations by.

    The document sits in the config home beside the settings it is scoped to,
    so a project checkout never gains a web-UI bookkeeping file.
    """
    return FolderLibrary(settings)


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Merge ``extra`` into a settings mapping without touching ``base``."""
    result = dict(base)
    for key, value in extra.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _merge(current, value)
        else:
            result[key] = value
    return result


def _is_loopback_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return any(token in value for token in ("//127.0.0.1", "//localhost", "//[::1]"))


class Busy(RuntimeError):
    """A second turn was requested while one is still streaming."""


def browse_directories(raw: str | None) -> dict[str, Any]:
    """List child directories so the browser can offer a folder picker.

    Only names and paths are returned: the picker must not become a way to read
    file contents from the web page.
    """
    text = (raw or "").strip()
    try:
        target = Path.home() if not text else Path(text).expanduser()
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigError(f"invalid folder: {text}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"not a directory: {resolved}")
    entries: list[dict[str, str]] = []
    try:
        with os.scandir(resolved) as scanner:
            for entry in scanner:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                entries.append({"name": entry.name, "path": str(resolved / entry.name)})
    except OSError as exc:
        raise ConfigError(f"cannot list {resolved}: {exc}") from exc
    entries.sort(key=lambda item: item["name"].lower())
    parent = resolved.parent if resolved.parent != resolved else None
    payload: dict[str, Any] = {
        "path": str(resolved),
        "parent": str(parent) if parent is not None else None,
        "directories": entries,
    }
    if parent is None and os.name == "nt":
        # Sitting on a drive root leaves no parent to walk up to, so the picker
        # offers the other drives instead of a dead end.
        payload["roots"] = [
            f"{letter}:\\"
            for letter in string.ascii_uppercase
            if Path(f"{letter}:\\").is_dir()
        ]
    return payload


class WebAgent(Agent):
    """Agent that reports deltas as events instead of writing a terminal.

    ``stream_output=False`` disables the ANSI renderers; the overrides below
    are the whole display surface, so the browser gets plain text and no
    terminal escape sequence can reach it.
    """

    def __init__(self, *, emit: Emitter, **kwargs: Any) -> None:
        self._emit = emit
        self._round = 0
        # Reasoning blocks of this turn, in the order the page showed them. The
        # page rebuilds itself from ``/api/history`` after a reload, so they are
        # handed to ``ThinkingLog`` once the turn finishes.
        self.rounds: list[dict[str, Any]] = []
        self._reasoning: list[str] = []
        self._started = time.monotonic()
        super().__init__(stream_output=False, **kwargs)

    def _settle_reasoning(self) -> None:
        """Close the reasoning block that is open, if any."""
        text = "".join(self._reasoning)
        self._reasoning.clear()
        elapsed = time.monotonic() - self._started
        self._started = time.monotonic()
        if text.strip():
            self.rounds.append({"text": text, "seconds": max(1, round(elapsed))})

    def take_reasoning(self) -> list[dict[str, Any]]:
        """Return this turn's reasoning blocks and close the last open one."""
        self._settle_reasoning()
        rounds, self.rounds = self.rounds, []
        return rounds

    def _begin_round(self) -> None:
        """Open a new reasoning/answer/activity block for the page.

        The browser appends every delta to one transcript, so without this
        boundary three tool rounds would collapse into one reasoning block and
        one answer block instead of one group per model request.
        """
        self._settle_reasoning()
        self._round += 1
        self._emit("round", {"index": self._round})

    def _write_delta(self, text: str) -> None:
        # The answer starts, so this round's reasoning block is complete.
        self._settle_reasoning()
        if text:
            self._emit("delta", {"text": text})

    def _write_thinking(self, text: str) -> None:
        if text:
            self._reasoning.append(text)
            self._emit("thinking", {"text": text})

    def _activity(self, message: str) -> None:
        text = strip_ansi(str(message)).strip()
        if text:
            self._emit("activity", {"text": text})

    def _provider_notice(self, message: str) -> None:
        text = strip_ansi(str(message)).strip()
        if text:
            self._emit("notice", {"text": text})


class WebRuntime:
    """One workspace, one conversation, and the settings the page may change."""

    def __init__(
        self,
        settings: Settings,
        workspace: Path,
        *,
        provider: str | None = None,
        model: str | None = None,
        read_only: bool = False,
        save_session: bool = True,
    ):
        self.settings = settings
        self.workspace = workspace.resolve()
        self.read_only = read_only
        self.save_session = save_session
        self.store = SessionStore(settings)
        # Folders are a view over the sidebar, not part of a transcript, so the
        # page's grouping never changes the file the CLI resumes from.
        self.library = _folder_index(settings)
        # Reasoning is display-only and never enters the JSONL transcript, so
        # the page needs its own record to reopen a 思考 block after a reload.
        self.reasoning = ThinkingLog(settings)
        self.provider_name = provider or str(settings.data.get("provider", ""))
        # An empty selection means "let the provider preset and the global
        # setting decide", which is what the CLI does without -m/--model.
        self.model = (model or "").strip()
        self._agent: WebAgent | None = None
        self._session: Session | None = None
        # A session picked in the sidebar; adopted instead of a new one when the
        # agent is next built.
        self._resume: Session | None = None
        # Empty means "use the configured effort"; "none" is the composer's
        # 深度思考 switch turned off.
        self._thinking = ""
        self._shell = detect_shell(settings.get("shell.path"))
        self._clients: list[Any] = []
        self._lock = threading.Lock()
        self._sink: Emitter | None = None

    # -- settings ---------------------------------------------------------

    def _providers(self) -> dict[str, Any]:
        table = self.settings.data.get("providers")
        return table if isinstance(table, dict) else {}

    def _provider_block(self, name: str) -> dict[str, Any]:
        block = self._providers().get(name)
        if not isinstance(block, dict):
            raise ConfigError(f"provider {name!r} is not defined")
        return block

    def _default_model(self) -> str:
        return str(self.settings.data.get("model", ""))

    def effective_model(self) -> str:
        """Return the model this runtime would ask for right now."""
        if self.model:
            return self.model
        try:
            preset = self._provider_block(self.provider_name).get("model")
        except ConfigError:
            preset = None
        if isinstance(preset, str) and preset.strip():
            return preset
        return self._default_model()

    def describe(self) -> dict[str, Any]:
        """Return everything the settings panel and header need."""
        providers: list[dict[str, Any]] = []
        default_model = self._default_model()
        for name, block in self._providers().items():
            if not isinstance(block, dict):
                continue
            models: list[str] = []
            for candidate in (block.get("model"), default_model):
                if isinstance(candidate, str) and candidate and candidate not in models:
                    models.append(candidate)
            providers.append(
                {
                    "name": name,
                    "protocol": str(block.get("protocol", "openai_chat")),
                    "base_url": str(block.get("base_url", "")),
                    "models": models,
                    "has_api_key": provider_api_key(block) is not None,
                    "api_key_env": str(block.get("api_key_env") or ""),
                    "local": _is_loopback_url(block.get("base_url")),
                }
            )
        providers.sort(key=lambda item: item["name"])
        current = next(
            (item for item in providers if item["name"] == self.provider_name), None
        )
        session = self._session
        return {
            "version": __version__,
            "workspace": str(self.workspace),
            "provider": self.provider_name,
            "protocol": current["protocol"] if current else "",
            "model": self.effective_model(),
            "providers": providers,
            "protocols": list(PROTOCOLS),
            "read_only": self.read_only,
            "settings_path": str(self.settings.source),
            "busy": self._lock.locked(),
            "thinking": self._thinking != "none",
            "session": (
                {"id": session.session_id, "title": session.title}
                if session is not None
                else None
            ),
        }

    def sessions(self) -> list[dict[str, Any]]:
        """Past conversations recorded for this workspace, newest first."""
        rows: list[dict[str, Any]] = []
        workspace = os.path.normcase(str(self.workspace))
        filed = self.library.assignments(self.workspace)
        for row in self.store.list():
            recorded = str(row.get("workspace") or "")
            if not recorded or os.path.normcase(recorded) != workspace:
                continue
            rows.append(
                {
                    "id": row["session_id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "folder": filed.get(row["session_id"]),
                }
            )
        return rows

    def folders(self) -> list[dict[str, Any]]:
        """Folders the sidebar shows for this workspace, in creation order."""
        return self.library.folders(self.workspace)

    def library_state(self) -> dict[str, Any]:
        """Everything the sidebar needs to re-render after it changes."""
        return {
            "state": self.describe(),
            "sessions": self.sessions(),
            "folders": self.folders(),
        }

    def open_session(self, identifier: object) -> dict[str, Any]:
        """Reopen a recorded conversation instead of starting a new one."""
        self._require_idle()
        if not isinstance(identifier, str) or not identifier:
            raise ConfigError("session id is required")
        row = next(
            (row for row in self.store.list() if row["session_id"] == identifier),
            None,
        )
        if row is None:
            raise ConfigError(f"unknown session: {identifier}")
        session = self.store.load(
            Path(str(row["path"])), default_workspace=self.workspace
        )
        self._reset()
        self._session = session
        self._resume = session
        recorded = session.provider
        if recorded and recorded in self._providers():
            self.provider_name = recorded
            self.model = session.model
        return self.describe()

    def delete_session(self, identifier: object) -> dict[str, Any]:
        """Delete one recorded conversation from this workspace.

        The file is unlinked, so the conversation is gone for the CLI too; the
        page cannot delete anything it does not list.
        """
        self._require_idle()
        if not isinstance(identifier, str) or not identifier:
            raise ConfigError("session id is required")
        if not any(row["id"] == identifier for row in self.sessions()):
            # The sidebar only lists this workspace, so nothing else is in reach.
            raise ConfigError(f"unknown session: {identifier}")
        recorded = next(
            (row for row in self.store.list() if row["session_id"] == identifier), None
        )
        if recorded is None:
            raise ConfigError(f"unknown session: {identifier}")
        self.store.delete(Path(str(recorded["path"])))
        self.library.forget(self.workspace, identifier)
        try:
            self.reasoning.forget(identifier)
        except (ConfigError, OSError, ValueError):
            pass
        current = self._session
        if current is not None and current.session_id == identifier:
            # The transcript is gone, so a live agent must not append to it.
            self._reset()
        return self.library_state()

    def create_folder(self, name: object) -> dict[str, Any]:
        """Add a folder to the sidebar and report the id it was given."""
        self._require_idle()
        folder = self.library.create(self.workspace, name)
        payload = self.library_state()
        payload["folder"] = folder
        return payload

    def rename_folder(self, identifier: object, name: object) -> dict[str, Any]:
        self._require_idle()
        self.library.rename(self.workspace, identifier, name)
        return self.library_state()

    def delete_folder(self, identifier: object) -> dict[str, Any]:
        """Remove a folder; the conversations filed in it stay, unfiled."""
        self._require_idle()
        self.library.delete(self.workspace, identifier)
        return self.library_state()

    def assign_folder(self, identifier: object, folder: object) -> dict[str, Any]:
        """File a conversation into a folder, or back to uncategorized."""
        self._require_idle()
        if not isinstance(identifier, str) or not identifier:
            raise ConfigError("session id is required")
        if folder is not None and not isinstance(folder, str):
            raise ConfigError("folder id must be a string or null")
        if not any(row["id"] == identifier for row in self.sessions()):
            raise ConfigError(f"unknown session: {identifier}")
        self.library.assign(self.workspace, identifier, folder or "")
        return self.library_state()

    def set_thinking(self, enabled: object) -> dict[str, Any]:
        """Turn the composer's 深度思考 switch on or off."""
        self._require_idle()
        if not isinstance(enabled, bool):
            raise ConfigError("thinking must be a boolean")
        self._thinking = "" if enabled else "none"
        self._reload_agent()
        return self.describe()

    def apply_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the provider/format/model/API key chosen in the page.

        Only the keys that were sent are written, and they are written as a
        delta on top of the user's own settings file, so every other preset and
        every ``${VAR}`` placeholder stays live. Command-line overrides applied
        by this process (``--sandbox`` and friends) survive because the live
        settings mapping is updated instead of reloaded.

        A provider that only exists in this process (``--anthropic-env``
        injects one into ``settings.data``) is never written: a block holding a
        model and nothing else would fail validation on the next start, and a
        pinned default provider would name something that file cannot define.
        """
        self._require_idle()
        if not isinstance(payload, dict):
            raise ConfigError("settings payload must be an object")
        name = payload.get("provider")
        if name is not None and (
            not isinstance(name, str) or name not in self._providers()
        ):
            raise ConfigError(f"unknown provider: {name!r}")
        protocol = payload.get("protocol")
        if protocol is not None and protocol not in PROTOCOLS:
            raise ConfigError(f"unsupported protocol: {protocol!r}")
        model = payload.get("model")
        if model is not None:
            if not isinstance(model, str):
                raise ConfigError("model must be a string")
            model = model.strip()
        api_key = payload.get("api_key")
        if api_key is not None:
            if not isinstance(api_key, str):
                raise ConfigError("api_key must be a string")
            api_key = api_key.strip()

        target = name or self.provider_name
        block: dict[str, Any] = {}
        updates: dict[str, Any] = {}
        if name:
            updates["provider"] = name
        if model:
            updates["model"] = model
            block["model"] = model
        if protocol:
            block["protocol"] = protocol
        if api_key:
            block["api_key"] = api_key
        if block:
            updates["providers"] = {target: block}
        if updates:
            if self._persistable(target):
                document = _merge(dict(self.settings.raw or {}), updates)
                write_settings_document(document, self.settings.home)
                self.settings.raw = document
            self.settings.data = _merge(self.settings.data, updates)
        if name:
            self.provider_name = name
        if model:
            self.model = model
        self._reload_agent()
        return self.describe()

    def _persistable(self, name: str) -> bool:
        """True when ``name`` is a provider this settings file can define.

        Bundled presets and providers the user already wrote into the file can
        gain an override; one injected in memory (``--anthropic-env``) cannot.
        """
        from ..config import DEFAULT_SETTINGS

        raw_providers = (self.settings.raw or {}).get("providers")
        if isinstance(raw_providers, dict) and name in raw_providers:
            return True
        return name in DEFAULT_SETTINGS.get("providers", {})

    def set_workspace(self, raw: object) -> dict[str, Any]:
        """Point the agent at another local folder and start a new session."""
        self._require_idle()
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError("workspace path is required")
        try:
            resolved = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(f"invalid workspace: {raw}") from exc
        if not resolved.is_dir():
            raise ConfigError(f"workspace is not a directory: {resolved}")
        if resolved != self.workspace:
            self._reset()
            self.workspace = resolved
        return self.describe()

    def new_session(self) -> dict[str, Any]:
        """Forget the conversation and start an empty one in this workspace."""
        self._require_idle()
        self._reset()
        return self.describe()

    def _require_idle(self) -> None:
        if self._lock.locked():
            raise Busy("a turn is still running; wait for it to finish")

    # -- conversation -----------------------------------------------------

    def history(self) -> dict[str, Any]:
        """Return the visible transcript of the current session.

        An assistant message carries the reasoning the page recorded for it, so
        a reload rebuilds the same 思考 blocks instead of dropping them.
        """
        session = self._session
        messages: list[dict[str, Any]] = []
        if session is not None:
            for message in session.messages:
                role = _VISIBLE_ROLES.get(str(message.get("role")))
                content = message.get("content")
                if role is None or not isinstance(content, str) or not content:
                    continue
                messages.append({"role": role, "content": content})
            try:
                recorded = self.reasoning.recall(session.session_id, messages)
            except (ConfigError, OSError, ValueError):
                recorded = {}
            for index, rounds in recorded.items():
                messages[index]["thinking"] = rounds
        return {
            "messages": messages,
            "session": (
                {"id": session.session_id, "title": session.title}
                if session is not None
                else None
            ),
        }

    def chat(self, message: str, emit: Emitter) -> None:
        """Run one turn and stream its events to ``emit``.

        The turn owns ``self._lock`` for its whole life, so settings and
        workspace changes are refused instead of pulling the runtime out from
        under a running tool.
        """
        text = (message or "").strip()
        if not text:
            raise ValueError("message is empty")
        if not self._lock.acquire(blocking=False):
            raise Busy("a turn is still running")
        self._sink = emit
        try:
            agent = self._ensure_agent()
            # A turn that died while writing to a closed socket can leave blocks
            # behind; this turn must not inherit them.
            agent.take_reasoning()
            emit(
                "start",
                {
                    "provider": agent.provider.name,
                    "model": agent.provider.model,
                    "workspace": str(self.workspace),
                },
            )
            result = self._run_turn(agent, text, emit)
            reasoning = agent.take_reasoning()
            if result is not None:
                self._remember_reasoning(agent, result, reasoning)
                emit("done", self._done_payload(agent, result))
        finally:
            self._sink = None
            self._lock.release()

    def _remember_reasoning(
        self, agent: WebAgent, result: AgentResult, reasoning: list[dict[str, Any]]
    ) -> None:
        """Keep this turn's reasoning so a reload can still open it.

        The transcript stays provider-neutral, so this record lives beside it in
        the config home. Losing it must not fail a turn whose answer already
        reached the page.
        """
        session = agent.session
        if session.path is None:
            # ``--no-session`` keeps the transcript in memory only; the page
            # starts over after a reload, so there is nothing to restore.
            return
        try:
            self.reasoning.remember(session.session_id, result.text or "", reasoning)
        except (ConfigError, OSError, ValueError):
            return

    def _run_turn(
        self, agent: WebAgent, text: str, emit: Emitter
    ) -> AgentResult | None:
        try:
            return agent.run(text)
        except (MinimalCodeError, OSError, ValueError) as exc:
            emit("error", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - the page must show the failure
            emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        return None

    def _done_payload(self, agent: WebAgent, result: AgentResult) -> dict[str, Any]:
        return {
            "text": result.text,
            "completed": result.completed,
            "turns": result.turns,
            "usage": result.run_usage,
            "usage_estimated": result.usage_estimated,
            "session": {
                "id": agent.session.session_id,
                "title": agent.session.title,
            },
        }

    # -- agent lifecycle --------------------------------------------------

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        sink = self._sink
        if sink is not None:
            sink(event, data)

    def _build(self) -> tuple[Any, str, str, list[Any], ToolRuntime]:
        """Create a provider, its optional clients, and a tool runtime."""
        provider, name, model = create_provider(
            self.settings,
            provider_name=self.provider_name or None,
            model=self.model or None,
            thinking=self._thinking or None,
        )
        web_client = ProviderWebClient(name, provider.config)
        vision_client = ProviderVisionClient(name, provider.config, model=model)
        media_client = ProviderMediaClient(name, provider.config)
        tools = ToolRuntime(
            self.settings,
            self.workspace,
            self._shell,
            web_client=web_client,
            vision_client=vision_client,
            media_client=media_client,
            provider_name=name,
            model=model,
            thinking=provider.thinking,
            read_only=self.read_only,
        )
        clients = [web_client, vision_client, media_client]
        return provider, name, model, clients, tools

    def _ensure_agent(self) -> WebAgent:
        if self._agent is not None:
            return self._agent
        if not self.workspace.is_dir():
            raise ConfigError(f"workspace is no longer a directory: {self.workspace}")
        provider, name, model, clients, tools = self._build()
        resumed, self._resume = self._resume, None
        if resumed is not None:
            session = resumed
            self._record_switch(session, name, model)
        else:
            save = bool(self.settings.get("session.save", True)) and self.save_session
            session = self.store.create(
                self.workspace, provider=name, model=model, save=save
            )
        # The runtime's own ``_emit`` dispatches to whichever socket is open for
        # the current turn, so one agent serves every request.
        agent = WebAgent(
            emit=self._emit,
            settings=self.settings,
            provider=provider,
            session=session,
            tool_runtime=tools,
            shell=self._shell,
        )
        self.provider_name, self.model = name, model
        self._agent = agent
        self._session = session
        self._clients = clients
        return agent

    @staticmethod
    def _record_switch(session: Session, provider: str, model: str) -> None:
        """Record the provider/model a reopened conversation now runs on."""
        if session.provider == provider and session.model == model:
            return
        session.provider, session.model = provider, model
        try:
            session.append_event({"type": "meta", "provider": provider, "model": model})
        except (OSError, TypeError, ValueError):
            pass

    def _reload_agent(self) -> None:
        """Rebuild provider and tools but keep the conversation.

        A model or format change is the web equivalent of `/model`: the
        transcript stays addressable, the provider sees it under its own
        protocol, and a session that already exists records the switch in its
        own meta event.
        """
        agent = self._agent
        if agent is None:
            return
        old_provider = agent.provider
        old_clients, self._clients = self._clients, []
        old_mcp = getattr(agent.tools, "mcp", None)
        provider, name, model, clients, tools = self._build()
        agent.switch_runtime(provider, tools)
        self.provider_name, self.model = name, model
        self._clients = clients
        for client in old_clients:
            client.close()
        old_provider.close()
        if old_mcp is not None:
            # The new runtime started its own servers; the old child processes
            # must not outlive the switch.
            old_mcp.close()

    def _reset(self) -> None:
        agent, self._agent = self._agent, None
        self._session = None
        self._resume = None
        clients, self._clients = self._clients, []
        if agent is None:
            return
        provider = agent.provider
        mcp_manager = getattr(agent.tools, "mcp", None)
        agent.close()
        for client in clients:
            client.close()
        provider.close()
        if mcp_manager is not None:
            # A stdio MCP server is a child process; leaving it behind while the
            # browser moves to another folder would keep it alive.
            mcp_manager.close()

    def close(self) -> None:
        """Release the provider, its optional clients, and every MCP server."""
        self._reset()


__all__ = [
    "PROTOCOLS",
    "Busy",
    "WebAgent",
    "WebRuntime",
    "browse_directories",
]
