"""Report MinimalCode activity to the Clawd on Desk desktop pet.

Clawd animates one state per session from short lifecycle events posted as
JSON to a loopback endpoint. Its port lives in ``~/.clawd/runtime.json`` and
changes across restarts, so the file is re-read whenever a cached URL fails.

Clawd only accepts an ``agent_id`` it knows: one of its built-in agents or a
custom application registered in ``clawd-prefs.json``. A MinimalCode install
therefore registers itself as a custom application (see
:func:`register_custom_application`) and reports under the resulting id.

Everything here is fail-open. Clawd not running, an unregistered id, a stale
port and a slow socket must never delay or break a turn, so events are queued
to one daemon thread with short timeouts and every drop is silent. This module
never sends prompts, tool arguments or file contents: Clawd needs lifecycle
metadata only.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CLAWD_APP_ID = "clawd-on-desk"
# Display name used for the custom application entry. Clawd derives the id
# slug from it, so changing this renames the id as well.
CUSTOM_APPLICATION_NAME = "MinimalCode"
# Id Clawd uses when it ships native MinimalCode support. It is never sent
# automatically: Clawd accepts an unknown id and folds it into claude-code's
# subagent stream, which is worse than staying silent. Users who patch Clawd
# from source can set ``integrations.clawd.agent_id`` to this value.
NATIVE_AGENT_ID = "minimalcode"

_PREFS_FILENAME = "clawd-prefs.json"
_RUNTIME_FILENAME = "runtime.json"
_QUEUE_LIMIT = 64
# A failed probe (Clawd closed, stale runtime file) backs off instead of
# touching the filesystem on every tool call of a long turn.
_PROBE_BACKOFF_SECONDS = 5.0
_REQUEST_TIMEOUT_SECONDS = 1.0
_MAX_PORT = 65535
# Set by the parent for a subagent child: the parent already reports
# SubagentStart/SubagentStop, and a child card would only flash and vanish.
SUPPRESS_ENV = "MINIMALCODE_NO_CLAWD"
_TRUE_FLAGS = frozenset({"1", "true", "yes", "on"})


def runtime_path(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the runtime file Clawd publishes while it is running."""
    if override:
        return Path(override).expanduser()
    return Path.home() / ".clawd" / _RUNTIME_FILENAME


def prefs_paths() -> list[Path]:
    """Return the candidate Clawd preference files, most likely first."""
    if sys.platform.startswith("win"):
        roots = [os.environ.get("APPDATA")]
        base = [Path(root) for root in roots if root]
    elif sys.platform == "darwin":
        base = [Path.home() / "Library" / "Application Support"]
    else:
        base = [
            Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"),
        ]
    return [root / CLAWD_APP_ID / _PREFS_FILENAME for root in base]


def read_endpoint(path: Path) -> str | None:
    """Return the ``/state`` URL of a running Clawd, or None when it is off.

    The identity field is verified because the file is user-writable and a
    stale copy from another program must not become a request target.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("app") != CLAWD_APP_ID:
        return None
    port = data.get("port")
    if isinstance(port, str):
        port = int(port) if port.isdigit() else 0
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    if not 1 <= port <= _MAX_PORT:
        return None
    return f"http://127.0.0.1:{port}/state"


def application_id(
    executable: Path,
    *,
    name: str = CUSTOM_APPLICATION_NAME,
    platform: str | None = None,
) -> str:
    """Reproduce Clawd's custom application id for one executable.

    The value is a pure function of the path and the display name, so a
    registration can be recomputed without reading Clawd's preferences.
    """
    key = str(executable)
    if (platform or sys.platform).startswith("win"):
        key = key.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "app"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"custom-{slug}-{digest}"


def agent_executable() -> Path:
    """Return the executable Clawd should attribute MinimalCode sessions to.

    The console script is preferred because its name is what a user sees in
    Clawd, and it stays stable across virtual environment renames. A source
    checkout has no console script, so the interpreter is the fallback.
    """
    found = shutil.which("minimalcode")
    if found:
        return Path(found)
    return Path(sys.executable)


def _normalized_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def registered_agent_id(paths: Iterable[Path] | None = None) -> str | None:
    """Return the id of MinimalCode's custom application entry, if any."""
    wanted = _normalized_name(CUSTOM_APPLICATION_NAME)
    for path in paths if paths is not None else prefs_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        entries = data.get("customApplications")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            agent_id = entry.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                continue
            names = (entry.get("name"), entry.get("processName"))
            if any(_normalized_name(item) == wanted for item in names):
                return agent_id
    return None


def prefs_document(target: Path) -> dict[str, Any]:
    """Read Clawd's preference document, tolerating a first run.

    A source checkout that has never been started has no preference file yet.
    ``migrate()`` in Clawd's ``prefs.js`` backfills every missing key from its
    own schema and keeps unknown ones, so a fresh document holding only
    ``customApplications`` is a valid starting point and Clawd saves the full
    snapshot on its next start. An unreadable or malformed file still raises:
    replacing a real preference document would discard the user's settings.
    """
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read Clawd preferences at {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError(f"unexpected Clawd preferences at {target}")
    return data


def _write_prefs_document(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)


def register_custom_application(
    executable: Path | None = None,
    *,
    prefs: Path | None = None,
) -> tuple[str, bool]:
    """Add MinimalCode to Clawd's registered custom applications.

    Returns the agent id and whether an entry was written. Clawd rewrites its
    preference file from an in-memory snapshot, so a registration written
    while Clawd runs can be overwritten; Clawd picks it up after a restart.

    The file is created when it does not exist yet, so a source checkout can be
    prepared before its first start. An unreadable or malformed file is still
    an error: replacing a real preference document with a fresh one would throw
    away the user's settings.
    """
    target = prefs or (prefs_paths() or [None])[0]
    if target is None:
        raise RuntimeError("cannot locate Clawd's preference file")
    data = prefs_document(target)
    resolved = (executable or agent_executable()).expanduser()
    new_id = application_id(resolved)
    entries = data.get("customApplications")
    if not isinstance(entries, list):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") == new_id:
            return new_id, False
    entries.append(
        {
            "id": new_id,
            "name": CUSTOM_APPLICATION_NAME,
            "sourcePath": str(resolved),
            "executablePath": str(resolved),
            "processName": resolved.name,
            "category": "code",
        }
    )
    data["customApplications"] = entries
    _write_prefs_document(target, data)
    return new_id, True


def unregister_custom_application(*, prefs: Path | None = None) -> str | None:
    """Remove MinimalCode's custom application entry from Clawd's prefs.

    Returns the removed id, or None when no entry matched. Clawd only reads
    its preference file at startup, so the change is visible after a restart.
    """
    target = prefs or (prefs_paths() or [None])[0]
    if target is None:
        raise RuntimeError("cannot locate Clawd's preference file")
    data = prefs_document(target)
    entries = data.get("customApplications")
    if not isinstance(entries, list):
        return None
    wanted = _normalized_name(CUSTOM_APPLICATION_NAME)
    kept: list[Any] = []
    removed: str | None = None
    for entry in entries:
        if isinstance(entry, dict) and removed is None:
            names = (entry.get("name"), entry.get("processName"))
            if any(_normalized_name(item) == wanted for item in names):
                candidate = entry.get("id")
                removed = candidate if isinstance(candidate, str) else ""
                continue
        kept.append(entry)
    if removed is None:
        return None
    data["customApplications"] = kept
    _write_prefs_document(target, data)
    return removed


def prefs_version(paths: Iterable[Path] | None = None) -> int | None:
    """Return the schema version of Clawd's preference file, when readable.

    Reported by ``minimalcode clawd status``: a source checkout that is ahead
    of the installed build can write a newer version, and that mismatch is the
    one thing a user needs to see before wondering why a card behaves oddly.
    """
    for path in paths if paths is not None else prefs_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            version = data.get("version")
            if isinstance(version, int) and not isinstance(version, bool):
                return version
            return None
    return None


def probe_state(
    *,
    agent_id: str,
    session_id: str,
    cwd: Path,
    runtime_file: Path | None = None,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
) -> int | None:
    """POST one harmless event and return Clawd's HTTP status.

    ``200`` means the agent gate accepted the id; ``204`` means Clawd is
    running but this agent is unknown, disabled, or its registration was
    removed. ``None`` means Clawd did not answer at all. The probe is the only
    way to tell "registered" from "written into a file Clawd never loaded",
    which is exactly the failure mode a running Clawd causes.

    A custom id is a reliable signal: Clawd rejects the whole ``custom-``
    namespace when it is not registered, so a 200 cannot be a misunderstanding.
    """
    endpoint = read_endpoint(runtime_file or runtime_path())
    if endpoint is None:
        return None
    payload = {
        "agent_id": agent_id,
        "session_id": session_id,
        "state": "idle",
        "event": "SessionStart",
        "cwd": str(cwd),
        "platform": "terminal",
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=max(0.1, float(timeout))) as response:
            response.read(64)
            return getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        # 4xx is a verdict, not a transport failure: report the code.
        exc.close()
        return exc.code
    except OSError:
        return None


class ClawdBridge:
    """Queue lifecycle events for Clawd and post them from a daemon thread.

    A disabled bridge drops every event, so call sites never need a guard.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        session_id: str,
        cwd: Path,
        enabled: bool = True,
        runtime_file: Path | None = None,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.agent_id = agent_id
        self.session_id = session_id
        self.cwd = str(cwd)
        self.timeout = max(0.1, float(timeout))
        self._runtime_file = runtime_file or runtime_path()
        self._endpoint_url: str | None = None
        self._retry_after = 0.0
        self._running = enabled and bool(agent_id)
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=_QUEUE_LIMIT
        )
        self._thread: threading.Thread | None = None
        # A local loopback call must never be routed through an environment
        # proxy: Clawd rejects foreign authorities and some proxies refuse
        # 127.0.0.1 outright.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if self._running:
            self._thread = threading.Thread(
                target=self._worker,
                name="clawd-bridge",
                daemon=True,
            )
            self._thread.start()

    # -- lifecycle -----------------------------------------------------
    def session_start(self) -> None:
        self._emit("idle", "SessionStart")

    def prompt(self) -> None:
        self._emit("thinking", "UserPromptSubmit")

    def tool_start(self, name: str) -> None:
        self._emit("working", "PreToolUse", tool_name=name)

    def tool_end(self, name: str, *, ok: bool = True) -> None:
        if ok:
            self._emit("working", "PostToolUse", tool_name=name)
        else:
            self._emit("error", "PostToolUseFailure", tool_name=name)

    def subagent_start(self) -> None:
        self._emit("juggling", "SubagentStart")

    def subagent_end(self) -> None:
        self._emit("working", "SubagentStop")

    def compact_start(self) -> None:
        self._emit("sweeping", "PreCompact")

    def compact_end(self) -> None:
        self._emit("thinking", "PostCompact")

    def turn_end(self) -> None:
        self._emit("attention", "Stop")

    def turn_failed(self) -> None:
        self._emit("error", "StopFailure")

    def notify(self) -> None:
        self._emit("notification", "Notification")

    def session_end(self) -> None:
        """Ask Clawd to remove this session's card."""
        self._emit("idle", "SessionEnd")

    def retarget(self, session_id: str, cwd: Path) -> None:
        """Point later events at a new session without restarting the worker.

        ``/new`` swaps the conversation inside one process; spawning a second
        worker would leave the old thread and its port probe running for the
        same pet.
        """
        self.session_id = session_id
        self.cwd = str(cwd)

    def shutdown(self) -> None:
        """Report the end of the session once, then stop the worker."""
        self.session_end()
        self.close()

    def close(self, *, flush_timeout: float = 1.5) -> None:
        """Stop the worker after handing it every queued event.

        The CLI exits right after the last turn, so an unflushed ``SessionEnd``
        would leave a stale card behind until Clawd's own idle timeout. The
        join is bounded: a pet that stopped responding must not delay exit.
        """
        if not self._running:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._running = False
        thread = self._thread
        if thread is not None:
            thread.join(timeout=flush_timeout)

    # -- transport -----------------------------------------------------
    def _emit(self, state: str, event: str, *, tool_name: str | None = None) -> None:
        if not self._running:
            return
        payload: dict[str, Any] = {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "state": state,
            "event": event,
            "cwd": self.cwd,
            "platform": "terminal",
        }
        if tool_name:
            payload["tool_name"] = tool_name
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            return

    def _worker(self) -> None:
        while True:
            payload = self._queue.get()
            try:
                if payload is None:
                    return
                self._post(payload)
            finally:
                self._queue.task_done()

    def _endpoint(self) -> str | None:
        if self._endpoint_url:
            return self._endpoint_url
        now = time.monotonic()
        if now < self._retry_after:
            return None
        url = read_endpoint(self._runtime_file)
        if url is None:
            self._retry_after = now + _PROBE_BACKOFF_SECONDS
            return None
        self._endpoint_url = url
        return url

    def _post(self, payload: dict[str, Any]) -> None:
        endpoint = self._endpoint()
        if endpoint is None:
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response.read(64)
        except OSError:
            # A restarted Clawd listens on a new port; drop the cached URL so
            # the next event re-reads the runtime file.
            self._endpoint_url = None


def disabled_bridge() -> ClawdBridge:
    """Return a bridge that drops every event.

    Tests and embedders construct agents without a desktop pet attached, and a
    no-op object keeps every reporting call site free of ``None`` checks.
    """
    return ClawdBridge(agent_id="", session_id="", cwd=Path(), enabled=False)


def create_bridge(
    settings: Any,
    *,
    session_id: str,
    cwd: Path,
) -> ClawdBridge:
    """Build the bridge described by ``settings.integrations.clawd``.

    ``agent_id`` comes from the setting or from the registration MinimalCode
    wrote into Clawd's preferences. There is deliberately no guessed default:
    Clawd accepts an unknown id and folds it into claude-code's subagent
    stream, which is worse than reporting nothing. ``minimalcode clawd
    install`` writes the registration.
    """
    enabled = bool(settings.get("integrations.clawd.enabled", True))
    configured = str(settings.get("integrations.clawd.agent_id", "") or "").strip()
    override = settings.get("integrations.clawd.runtime_file")
    timeout = settings.get("integrations.clawd.timeout_seconds", 1.0)
    if os.environ.get(SUPPRESS_ENV, "").strip().lower() in _TRUE_FLAGS:
        return disabled_bridge()
    agent_id = configured
    if enabled and not agent_id:
        agent_id = registered_agent_id() or ""
    if not enabled or not agent_id:
        return disabled_bridge()
    return ClawdBridge(
        agent_id=agent_id,
        session_id=session_id,
        cwd=cwd,
        enabled=True,
        runtime_file=runtime_path(str(override)) if override else None,
        timeout=float(timeout or _REQUEST_TIMEOUT_SECONDS),
    )
