"""Folder grouping for the chat web UI sidebar.

A folder is a label, not a move: the JSONL transcript stays where
``session.py`` wrote it, so the CLI keeps finding the same conversations.
This module owns one small document in the config home that records, per
workspace, which folders exist and which conversation was filed where.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import ConfigError, Settings

_VERSION = 1
_MAX_NAME = 40


def _serialized(method: Callable[..., Any]) -> Callable[..., Any]:
    """Hold the document lock for one load-modify-save cycle.

    Two browser tabs -- or a folder create racing an assignment -- would
    otherwise each write a snapshot taken before the other's change, so one of
    the two changes would silently disappear.
    """

    def wrapper(self: FolderLibrary, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = getattr(method, "__name__", "wrapper")
    wrapper.__doc__ = method.__doc__
    return wrapper


def _clean_name(raw: object) -> str:
    """Return a folder name with the whitespace a browser may send collapsed."""
    if not isinstance(raw, str):
        raise ConfigError("folder name must be a string")
    name = " ".join(raw.split())
    if not name:
        raise ConfigError("folder name is required")
    if len(name) > _MAX_NAME:
        raise ConfigError(f"folder name is too long (max {_MAX_NAME} characters)")
    return name


def _folder_id(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("folder id is required")
    return raw.strip()


class FolderLibrary:
    """Folders and conversation assignments recorded for one config home."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.home / "webui" / "folders.json"
        # One lock per library instance: the runtime serves every request from
        # the same object, so this is the whole contention surface.
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
            # A truncated or hand-edited file must not break the sidebar; the
            # next write replaces it with a document this module can read.
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        document = dict(data)
        document["version"] = _VERSION
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".folders.", dir=self.path.parent)
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

    @staticmethod
    def _key(workspace: Path) -> str:
        # Windows treats two spellings of one path as the same workspace, so
        # the key follows the same rule the session store uses.
        return os.path.normcase(str(workspace))

    def _bucket(
        self, data: dict[str, Any], workspace: Path, *, create: bool = False
    ) -> dict[str, Any]:
        """Return the workspace entry, detached from ``data`` when missing."""
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
            data["workspaces"] = workspaces
        bucket = workspaces.get(self._key(workspace))
        if not isinstance(bucket, dict):
            if not create:
                return {"folders": [], "assignments": {}}
            bucket = {"folders": [], "assignments": {}}
            workspaces[self._key(workspace)] = bucket
        if not isinstance(bucket.get("folders"), list):
            bucket["folders"] = []
        if not isinstance(bucket.get("assignments"), dict):
            bucket["assignments"] = {}
        return bucket

    @staticmethod
    def _find(bucket: dict[str, Any], identifier: object) -> dict[str, Any]:
        wanted = _folder_id(identifier)
        for item in bucket["folders"]:
            if isinstance(item, dict) and item.get("id") == wanted:
                return item
        raise ConfigError(f"unknown folder: {wanted}")

    @staticmethod
    def _rows(bucket: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in bucket["folders"]:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            name = item.get("name")
            if isinstance(identifier, str) and identifier and isinstance(name, str):
                rows.append({"id": identifier, "name": name})
        return rows

    def _snapshot(self, workspace: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
        bucket = self._bucket(self._load(), workspace)
        folders = self._rows(bucket)
        known = {row["id"] for row in folders}
        assignments: dict[str, str] = {}
        for session_id, folder_id in bucket["assignments"].items():
            # An assignment whose folder was removed by hand is not a folder.
            if isinstance(session_id, str) and folder_id in known:
                assignments[session_id] = str(folder_id)
        return folders, assignments

    # -- reads ------------------------------------------------------------

    def folders(self, workspace: Path) -> list[dict[str, Any]]:
        """Folders defined for ``workspace``, in the order they were created."""
        return self._snapshot(workspace)[0]

    def assignments(self, workspace: Path) -> dict[str, str]:
        """Map session id to folder id for every filed conversation."""
        return self._snapshot(workspace)[1]

    # -- writes -----------------------------------------------------------

    @_serialized
    def create(self, workspace: Path, name: object) -> dict[str, Any]:
        cleaned = _clean_name(name)
        data = self._load()
        bucket = self._bucket(data, workspace, create=True)
        for row in self._rows(bucket):
            if row["name"].casefold() == cleaned.casefold():
                raise ConfigError(f"folder already exists: {cleaned}")
        folder = {"id": uuid.uuid4().hex[:12], "name": cleaned}
        bucket["folders"].append(folder)
        self._save(data)
        return dict(folder)

    @_serialized
    def rename(self, workspace: Path, identifier: object, name: object) -> None:
        cleaned = _clean_name(name)
        data = self._load()
        bucket = self._bucket(data, workspace, create=True)
        target = self._find(bucket, identifier)
        for row in self._rows(bucket):
            if (
                row["id"] != target["id"]
                and row["name"].casefold() == cleaned.casefold()
            ):
                raise ConfigError(f"folder already exists: {cleaned}")
        target["name"] = cleaned
        self._save(data)

    @_serialized
    def delete(self, workspace: Path, identifier: object) -> None:
        """Remove one folder; its conversations fall back to uncategorized."""
        data = self._load()
        bucket = self._bucket(data, workspace, create=True)
        target = self._find(bucket, identifier)
        bucket["folders"].remove(target)
        for session_id, folder_id in list(bucket["assignments"].items()):
            if folder_id == target["id"]:
                bucket["assignments"].pop(session_id, None)
        self._save(data)

    @_serialized
    def assign(self, workspace: Path, session_id: str, identifier: object) -> None:
        """File ``session_id`` into a folder, or out of every folder for ''."""
        data = self._load()
        bucket = self._bucket(data, workspace, create=True)
        wanted = "" if identifier in (None, "") else _folder_id(identifier)
        if not wanted:
            bucket["assignments"].pop(session_id, None)
        else:
            bucket["assignments"][session_id] = self._find(bucket, wanted)["id"]
        self._save(data)

    @_serialized
    def forget(self, workspace: Path, session_id: str) -> None:
        """Drop the assignment of a conversation that no longer exists."""
        data = self._load()
        bucket = self._bucket(data, workspace)
        if bucket["assignments"].pop(session_id, None) is None:
            return
        self._save(data)


__all__ = ["FolderLibrary"]
