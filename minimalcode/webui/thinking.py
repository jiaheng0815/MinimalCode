"""Reasoning text the page shows, kept beside the transcript instead of in it.

Reasoning deltas are display-only: they never enter an assistant message, the
JSONL transcript, or ``--json``, so the CLI can replay the conversation to any
provider without leaking a block that only one protocol understands. The
browser, however, rebuilds the whole transcript from ``/api/history`` after
every reload, so without a copy of its own the page lost every 思考 block on F5
and could not open it again.

This module owns one small document in the config home, keyed by session id. An
entry is matched back to an assistant message by the answer text, newest first,
so a compacted or hand-edited transcript loses the reasoning it no longer has
instead of pulling an unrelated block onto the wrong answer.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import ConfigError, Settings

_VERSION = 1
# A long tool loop can reason once per round; keep the newest turns only so one
# conversation cannot grow this file without a bound.
_MAX_ENTRIES = 200


def _serialized(method: Callable[..., Any]) -> Callable[..., Any]:
    """Hold the document lock for one load-modify-save cycle.

    ``remember`` runs on the turn's own thread and ``forget`` on a request
    thread, so without the lock a delete landing between the load and the save
    of a finishing turn would resurrect the blocks it just dropped.
    """

    def wrapper(self: ThinkingLog, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = getattr(method, "__name__", "wrapper")
    wrapper.__doc__ = method.__doc__
    return wrapper


def _clean_rounds(raw: object) -> list[dict[str, Any]]:
    """Return only the well-formed reasoning blocks of ``raw``."""
    if not isinstance(raw, list):
        return []
    rounds: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        seconds = item.get("seconds")
        rounds.append(
            {
                "text": text,
                "seconds": seconds if isinstance(seconds, int) and seconds > 0 else 0,
            }
        )
    return rounds


class ThinkingLog:
    """Reasoning blocks recorded per session for the chat page."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.home / "webui" / "thinking.json"
        # One lock per log instance: the runtime holds a single ThinkingLog and
        # every request goes through it.
        self._lock = threading.Lock()

    # -- storage ----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ConfigError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # A truncated or hand-edited file must not break the page; the next
            # write replaces it with a document this module can read.
            return {}
        return data if isinstance(data, dict) else {}

    def _entries(self, data: dict[str, Any], session_id: str) -> list[dict[str, Any]]:
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
            data["sessions"] = sessions
        entries = sessions.get(session_id)
        if not isinstance(entries, list):
            entries = []
            sessions[session_id] = entries
        return entries

    def _save(self, data: dict[str, Any]) -> None:
        document = dict(data)
        document["version"] = _VERSION
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".thinking.", dir=self.path.parent)
        except OSError as exc:
            raise ConfigError(f"cannot write {self.path}: {exc}") from exc
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise ConfigError(f"cannot write {self.path}: {exc}") from exc
        finally:
            temp_path.unlink(missing_ok=True)

    # -- reads and writes -------------------------------------------------

    @_serialized
    def remember(self, session_id: str, reply: str, rounds: object) -> None:
        """Record one finished turn's reasoning under its answer text."""
        cleaned = _clean_rounds(rounds)
        if not session_id or not reply.strip() or not cleaned:
            # There is no assistant message to match the blocks against later.
            return
        data = self._load()
        entries = self._entries(data, session_id)
        entries.append({"reply": reply, "rounds": cleaned})
        del entries[:-_MAX_ENTRIES]
        self._save(data)

    def recall(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        """Map assistant-message index to the reasoning recorded for it."""
        if not session_id:
            return {}
        entries = self._entries(self._load(), session_id)
        if not entries:
            return {}
        used: set[int] = set()
        found: dict[int, list[dict[str, Any]]] = {}
        # Newest first: a compacted transcript is missing its oldest answers, so
        # the closest entry has to win instead of one from the start of the file.
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content:
                continue
            for position in range(len(entries) - 1, -1, -1):
                if position in used:
                    continue
                entry = entries[position]
                if isinstance(entry, dict) and entry.get("reply") == content:
                    used.add(position)
                    rounds = _clean_rounds(entry.get("rounds"))
                    if rounds:
                        found[index] = rounds
                    break
        return found

    @_serialized
    def forget(self, session_id: str) -> None:
        """Drop the reasoning of a conversation that no longer exists."""
        data = self._load()
        sessions = data.get("sessions")
        if not isinstance(sessions, dict) or sessions.pop(session_id, None) is None:
            return
        self._save(data)


__all__ = ["ThinkingLog"]
