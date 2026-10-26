"""Grep and find implementations exposed as ToolRuntime mixins."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import SENSITIVE_SEARCH_GLOBS
from . import search as search_module
from .search import (
    _MAX_GREP_LINE_CHARS,
    IGNORED_DIRECTORIES,
    IGNORED_GLOBS,
    inline_python_grep,
    run_python_grep,
    smart_case_ignore,
)
from .validation import positive_limit


class SearchToolsMixin:
    """Workspace-scoped text and filename search."""

    def grep(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Search file contents with ripgrep semantics and a Python fallback."""
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("pattern must be a non-empty string")
        if glob is not None and not isinstance(glob, str):
            raise ToolError("glob must be a string or null")
        target = self._path(path, operation="read")
        if not target.exists():
            raise ToolError(f"path not found: {target}")
        if not target.is_file() and not target.is_dir():
            raise ToolError(f"path is not a file or directory: {target}")
        limit = positive_limit(max_results, self.max_results, 2000, "max_results")

        # Prefer ripgrep when it is installed and the backend can spawn argv.
        rg = shutil.which("rg")
        if rg and self._can_spawn_argv():
            command = [
                rg,
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--hidden",
                "--no-ignore",
                "--no-follow",
                "--smart-case",
                "--max-columns",
                str(_MAX_GREP_LINE_CHARS),
                "--max-columns-preview",
            ]
            if glob:
                command.extend(["--glob", glob])
            # Exclusions must follow the positive glob: ripgrep uses the last
            # matching --glob, so this order stays exclusion-only.
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            for sandbox_glob in self._sandbox_globs():
                command.extend(["--glob", sandbox_glob])
            command.extend(["--", pattern, str(target)])
            returncode, lines, truncated, stderr = search_module.run_bounded_command(
                command, cwd=self.cwd, limit=limit, sandbox=self.sandbox
            )
            # Exit 1 means "no matches" and is not an error.
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"grep exited {returncode}")
            return {
                "status": "success",
                "matches": lines[:limit],
                "truncated": truncated,
            }
        return self._python_grep(pattern, target, glob, limit)

    def _ignore_globs(self, target: Path) -> tuple[str, ...]:
        # An explicitly requested ignored path must still be searchable;
        # otherwise `grep(..., path="build")` silently returns nothing.
        try:
            relative = target.relative_to(self.cwd)
        except ValueError:
            relative = target

        parts = relative.parts[:-1] if target.is_file() else relative.parts
        if any(part in IGNORED_DIRECTORIES for part in parts):
            return ()
        return IGNORED_GLOBS

    def _sandbox_globs(self) -> tuple[str, ...]:
        # The sensitive-file filter is a path policy, not an execution setting,
        # so it also applies when `sandbox.enabled` is false.
        if self.sandbox.policy.allow_sensitive_files:
            return ()
        return SENSITIVE_SEARCH_GLOBS

    def _can_spawn_argv(self) -> bool:
        """Return True when the active backend can start a child process."""
        return (
            not self.sandbox.policy.enabled
            or "argv" in self.sandbox.backend.capabilities
        )

    def _python_grep(
        self,
        pattern: str,
        target: Path,
        glob_pattern: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Run the Python fallback over the same file set ripgrep would see."""
        flags = re.IGNORECASE if smart_case_ignore(pattern) else 0
        try:
            re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc

        files = [target] if target.is_file() else list(self._walk_files(target))
        if glob_pattern:
            files = [
                file_path
                for file_path in files
                if self._matches_glob(file_path, target, glob_pattern)
            ]
        if not files:
            return {"status": "success", "matches": [], "truncated": False}
        if not self._can_spawn_argv():
            # A backend without argv support still gets line-accurate results;
            # it just cannot isolate the regex in a child process.
            return inline_python_grep(
                pattern=pattern,
                files=files,
                limit=limit,
                max_bytes=self.max_file_bytes,
            )
        return run_python_grep(
            pattern=pattern,
            files=files,
            limit=limit,
            max_bytes=self.max_file_bytes,
            sandbox=self.sandbox,
            cwd=self.cwd,
        )

    @staticmethod
    def _matches_glob(file_path: Path, root: Path, glob_pattern: str) -> bool:
        """Match a ripgrep-style glob against the file name and relative path."""
        if fnmatch.fnmatch(file_path.name, glob_pattern):
            return True
        try:
            relative = file_path.relative_to(root).as_posix()
        except ValueError:
            relative = file_path.as_posix()
        if fnmatch.fnmatch(relative, glob_pattern):
            return True
        # `**/` is optional in ripgrep globs, required by fnmatch.
        if glob_pattern.startswith("**/"):
            return fnmatch.fnmatch(relative, glob_pattern[3:])
        return False

    def find(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Find files by glob without descending into ignored directories."""
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("pattern must be a non-empty string")
        target = self._path(path, operation="read")
        if not target.is_dir():
            raise ToolError(f"directory not found: {target}")
        limit = positive_limit(max_results, self.max_results, 5000, "max_results")

        rg = shutil.which("rg")
        if rg and self._can_spawn_argv():
            command = [
                rg,
                "--files",
                "--hidden",
                "--no-ignore",
                "--no-follow",
                "--glob",
                pattern,
            ]
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            for sandbox_glob in self._sandbox_globs():
                command.extend(["--glob", sandbox_glob])
            command.append(str(target))
            returncode, paths, truncated, stderr = search_module.run_bounded_command(
                command, cwd=self.cwd, limit=limit, sandbox=self.sandbox
            )
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"find exited {returncode}")
            return {
                "status": "success",
                "files": paths[:limit],
                "truncated": truncated,
            }

        paths: list[str] = []
        for file_path in self._walk_files(target):
            if self._matches_glob(file_path, target, pattern):
                paths.append(str(file_path))
                if len(paths) >= limit:
                    return {"status": "success", "files": paths, "truncated": True}
        return {"status": "success", "files": paths, "truncated": False}

    def _walk_files(self, root: Path) -> Iterator[Path]:
        """Yield readable files under ``root`` while respecting the sandbox."""
        ignored = set(IGNORED_DIRECTORIES)
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in ignored]
            for filename in filenames:
                candidate = Path(directory) / filename
                # os.walk does not descend into symlinked directories, but a
                # symlinked file or a configured extra root can still leave the
                # sandbox, so every candidate is re-checked.
                if not self.sandbox.is_allowed(
                    candidate, operation="read", allow_outside=self.allow_outside
                ):
                    continue
                yield candidate
