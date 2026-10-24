from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _workspace_key(cwd: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", cwd.name or "root").strip("-")[:32] or "root"
    digest = hashlib.sha256(str(cwd).casefold().encode("utf-8")).hexdigest()[:12]
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_compact_json(event) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def append_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.append_event({"type": "message", "message": message})

    def replace_messages(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        summary: str,
        dropped_messages: int,
    ) -> None:
        self.messages = list(messages)
        self.append_event(
            {
                "type": "compact",
                "summary": summary,
                "dropped_messages": dropped_messages,
                "messages": self.messages,
                "at": _utc_now(),
            }
        )

    def append_usage(self, usage: dict[str, int]) -> None:
        self.append_event({"type": "usage", "usage": usage, "at": _utc_now()})


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

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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

    def _candidate_files(self, cwd: Path | None = None) -> list[Path]:
        root = self.workspace_dir(cwd) if cwd is not None else self.root
        if not root.is_dir():
            return []
        return sorted(root.rglob("*.jsonl"), key=lambda path: path.name, reverse=True)

    def latest(self, cwd: Path) -> Session | None:
        files = self._candidate_files(cwd)
        return self.load(files[0]) if files else None

    def resolve(self, identifier: str, cwd: Path) -> Session:
        candidate = Path(identifier).expanduser()
        if candidate.is_file():
            return self.load(candidate)

        files = self._candidate_files(cwd)
        if not files:
            files = self._candidate_files()
        matches = [path for path in files if identifier in path.stem or identifier in path.name]
        if not matches:
            raise FileNotFoundError(f"session not found: {identifier}")
        if len(matches) > 1:
            exact = [path for path in matches if path.stem == identifier]
            if len(exact) != 1:
                raise ValueError(f"session identifier is ambiguous: {identifier}")
            matches = exact
        return self.load(matches[0])

    def load(self, path: Path) -> Session:
        messages: list[dict[str, Any]] = []
        session_id = path.stem
        workspace = path.parent
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "meta":
                        session_id = str(event.get("session_id", session_id))
                        workspace = Path(str(event.get("workspace", workspace)))
                    elif event.get("type") == "message":
                        message = event.get("message")
                        if isinstance(message, dict):
                            messages.append(message)
                    elif event.get("type") == "compact":
                        compacted = event.get("messages")
                        if isinstance(compacted, list):
                            messages = [item for item in compacted if isinstance(item, dict)]
        except OSError as exc:
            raise FileNotFoundError(f"cannot read session {path}: {exc}") from exc
        return Session(session_id=session_id, path=path, cwd=workspace, messages=messages)

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._candidate_files():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    first = json.loads(handle.readline())
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "session_id": first.get("session_id", path.stem),
                    "workspace": first.get("workspace", ""),
                    "provider": first.get("provider", ""),
                    "model": first.get("model", ""),
                    "created_at": first.get("created_at", ""),
                    "path": str(path),
                }
            )
        return rows
