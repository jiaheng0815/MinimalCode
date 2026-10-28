from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _workspace_key(cwd: Path) -> str:
    name = (
        re.sub(r"[^A-Za-z0-9._-]+", "-", cwd.name or "root").strip("-")[:32] or "root"
    )
    key = str(cwd)
    if os.name == "nt":
        key = key.casefold()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{name}-{digest}"


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class Session:
    session_id: str
    path: Path | None
    cwd: Path
    messages: list[dict[str, Any]] = field(default_factory=list)

    def append_event(self, event: dict[str, Any]) -> None:
        if self.path is None:
            return
        if self.path.suffix.lower() != ".jsonl":
            # ``--session <path>`` once accepted any existing file, so a typo
            # appended JSONL to README.md. A session is only ever a .jsonl file.
            raise ValueError(f"session file must end in .jsonl: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        line = _compact_json(event) + "\n"
        # Create session files with restrictive permissions from the first
        # byte instead of tightening them after conversation content is
        # already on disk.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)

    def append_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        try:
            self.append_event({"type": "message", "message": message})
        except (OSError, TypeError, ValueError):
            self.messages.pop()
            raise

    def replace_messages(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        summary: str,
        dropped_messages: int,
    ) -> None:
        previous = self.messages
        self.messages = list(messages)
        try:
            self.append_event(
                {
                    "type": "compact",
                    "summary": summary,
                    "dropped_messages": dropped_messages,
                    "messages": self.messages,
                    "at": _utc_now(),
                }
            )
        except (OSError, TypeError, ValueError):
            self.messages = previous
            raise

    def append_usage(self, usage: dict[str, int]) -> None:
        """Record token usage without aborting the turn when the write fails.

        Usage is telemetry: losing one record only affects statistics, while
        raising here would discard a reply the model already produced.
        """
        try:
            self.append_event({"type": "usage", "usage": usage, "at": _utc_now()})
        except (OSError, TypeError, ValueError):
            return


class SessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.home / "sessions"

    def workspace_dir(self, cwd: Path) -> Path:
        return self.root / _workspace_key(cwd.resolve())

    def create(
        self,
        cwd: Path,
        *,
        provider: str,
        model: str,
        save: bool = True,
    ) -> Session:
        session_id = uuid.uuid4().hex
        if not save:
            return Session(session_id=session_id, path=None, cwd=cwd.resolve())

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self.workspace_dir(cwd) / f"{stamp}-{session_id[:10]}.jsonl"
        session = Session(session_id=session_id, path=path, cwd=cwd.resolve())
        session.append_event(
            {
                "type": "meta",
                "version": __version__,
                "session_id": session_id,
                "workspace": str(cwd.resolve()),
                "provider": provider,
                "model": model,
                "created_at": _utc_now(),
            }
        )
        return session

    @staticmethod
    def _recency_key(path: Path) -> tuple[int, str]:
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return modified, path.name

    def _candidate_files(self, cwd: Path | None = None) -> list[Path]:
        root = self.workspace_dir(cwd) if cwd is not None else self.root
        if not root.is_dir():
            return []
        return sorted(root.rglob("*.jsonl"), key=self._recency_key, reverse=True)

    def latest(self, cwd: Path) -> Session | None:
        for path in self._candidate_files(cwd):
            if self._first_event(path) is not None:
                return self.load(path, default_workspace=cwd)
        return None

    def resolve(self, identifier: str, cwd: Path) -> Session:
        try:
            candidate = Path(identifier).expanduser()
            is_file = candidate.is_file()
        except (OSError, RuntimeError, ValueError):
            is_file = False
            candidate = None
        if is_file and candidate is not None:
            return self.load(candidate, default_workspace=cwd)

        files = self._candidate_files(cwd)
        if not files:
            files = self._candidate_files()
        identities = [(path, self._recorded_session_id(path)) for path in files]
        matches = [
            path
            for path, session_id in identities
            if identifier in session_id
            or identifier in path.stem
            or identifier in path.name
        ]
        if not matches:
            raise FileNotFoundError(f"session not found: {identifier}")
        if len(matches) > 1:
            exact = [
                path
                for path, session_id in identities
                if identifier == session_id or identifier == path.stem
            ]
            if len(exact) != 1:
                raise ValueError(f"session identifier is ambiguous: {identifier}")
            matches = exact
        return self.load(matches[0], default_workspace=cwd)

    @staticmethod
    def _first_event(path: Path) -> dict[str, Any] | None:
        """Return the session's meta event, or None when it is unreadable."""
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                event = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(event, dict) and event.get("type") == "meta":
            return event
        return None

    @classmethod
    def _recorded_session_id(cls, path: Path) -> str:
        event = cls._first_event(path)
        if event is not None and event.get("type") == "meta":
            session_id = event.get("session_id")
            if isinstance(session_id, str):
                return session_id
        return path.stem

    def _within_root(self, path: Path) -> bool:
        """Return True when ``path`` is inside the store's sessions directory."""
        try:
            Path(path).resolve(strict=False).relative_to(
                self.root.resolve(strict=False)
            )
        except (OSError, RuntimeError, ValueError):
            return False
        return True

    def load(self, path: Path, *, default_workspace: Path | None = None) -> Session:
        messages: list[dict[str, Any]] = []
        session_id = path.stem
        if path.suffix.lower() != ".jsonl":
            raise ValueError(f"not a session file (expected .jsonl): {path}")
        # Never default to the sessions directory: a metadata-less file loaded
        # through --session would otherwise run every tool there.
        workspace = default_workspace or Path.cwd()
        # The recorded workspace is tool-restriction input, so it is trusted
        # only for files the store itself wrote. An arbitrary JSONL handed to
        # --session must not be able to point every tool at C:\ or /.
        trust_recorded_workspace = self._within_root(path)
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "meta":
                        recorded_id = event.get("session_id")
                        if isinstance(recorded_id, str) and recorded_id:
                            session_id = recorded_id
                        recorded_workspace = event.get("workspace")
                        if (
                            trust_recorded_workspace
                            and isinstance(recorded_workspace, str)
                            and recorded_workspace
                        ):
                            workspace = Path(recorded_workspace).expanduser()
                    elif event.get("type") == "message":
                        message = event.get("message")
                        if isinstance(message, dict):
                            messages.append(message)
                    elif event.get("type") == "compact":
                        compacted = event.get("messages")
                        if isinstance(compacted, list):
                            messages = [
                                item for item in compacted if isinstance(item, dict)
                            ]
        except OSError as exc:
            raise FileNotFoundError(f"cannot read session {path}: {exc}") from exc
        try:
            workspace = workspace.resolve()
        except (OSError, RuntimeError):
            pass
        return Session(
            session_id=session_id,
            path=path,
            cwd=workspace,
            messages=messages,
        )

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._candidate_files():
            first = self._first_event(path)
            if first is None:
                continue
            rows.append(
                {
                    "session_id": str(first.get("session_id") or path.stem),
                    "workspace": str(first.get("workspace") or ""),
                    "provider": str(first.get("provider") or ""),
                    "model": str(first.get("model") or ""),
                    "created_at": str(first.get("created_at") or ""),
                    "path": str(path),
                }
            )
        return rows
