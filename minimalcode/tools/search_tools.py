"""Grep and find implementations exposed as ToolRuntime mixins."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from functools import lru_cache
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

_GREP_RESULT_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):")


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
                "--no-config",
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
                command,
                cwd=self.cwd,
                # Read one line past the requested count so a filtered sensitive
                # path cannot make an incomplete result look complete.
                limit=limit + 1,
                sandbox=self.sandbox,
            )
            # Exit 1 means "no matches" and is not an error.
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"grep exited {returncode}")
            matches = [line for line in lines if self._grep_line_allowed(line)]
            return {
                "status": "success",
                "matches": matches[:limit],
                "truncated": truncated or len(matches) > limit,
            }
        return self._python_grep(pattern, target, glob, limit)

    def _explicit_ignored_target(self, target: Path) -> bool:
        """Return True when the caller named a normally ignored directory."""
        try:
            relative = target.relative_to(self.cwd)
        except ValueError:
            relative = target
        parts = relative.parts[:-1] if target.is_file() else relative.parts
        return any(part in IGNORED_DIRECTORIES for part in parts)

    def _ignore_globs(self, target: Path) -> tuple[str, ...]:
        # An explicitly requested ignored path must still be searchable;
        # otherwise `grep(..., path="build")` silently returns nothing.
        return () if self._explicit_ignored_target(target) else IGNORED_GLOBS

    def _sandbox_globs(self) -> tuple[str, ...]:
        # The sensitive-file filter is a path policy, not an execution setting,
        # so it also applies when `sandbox.enabled` is false.
        if self.sandbox.policy.allow_sensitive_files:
            return ()
        return SENSITIVE_SEARCH_GLOBS

    def _allowed_result_path(self, raw: str) -> bool:
        """Return True when one search result may be shown for this workspace."""
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.cwd / candidate
            candidate = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False
        return self.sandbox.is_allowed(
            candidate, operation="read", allow_outside=self.allow_outside
        )

    def _grep_line_allowed(self, line: str) -> bool:
        """Keep one ripgrep match only when its path is allowed."""
        match = _GREP_RESULT_RE.match(line)
        if match is None:
            # A head/tail truncation marker or a line fragment is not a match.
            return False
        return self._allowed_result_path(match.group("path"))

    def _find_result_allowed(self, raw: str) -> bool:
        """Keep one `rg --files` path only when it names an allowed file."""
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.cwd / candidate
            if not candidate.is_file():
                return False
        except (OSError, RuntimeError, ValueError):
            return False
        return self._allowed_result_path(str(candidate))

    def _can_spawn_argv(self) -> bool:
        """Return True when the active backend can start a child process.

        Capabilities describe what a backend *could* do; a selected backend
        that is not installed must still let file tools fall back to the
        in-process scanner instead of failing the whole search.
        """
        if not self.sandbox.policy.enabled:
            return True
        return (
            "argv" in self.sandbox.backend.capabilities
            and self.sandbox.backend.available()
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

        ignored = () if self._explicit_ignored_target(target) else IGNORED_DIRECTORIES
        files = (
            [target]
            if target.is_file()
            else list(self._walk_files(target, ignored=ignored))
        )
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
    @lru_cache(maxsize=256)
    def _glob_regex(glob_pattern: str) -> re.Pattern[str]:
        """Translate a ripgrep-style glob to a compiled, case-sensitive regex."""
        parts: list[str] = ["^"]
        index = 0
        while index < len(glob_pattern):
            char = glob_pattern[index]
            if char == "*":
                if glob_pattern.startswith("**/", index):
                    # `**/` means zero or more directories, as in ripgrep.
                    parts.append("(?:.*/)?")
                    index += 3
                elif glob_pattern.startswith("**", index):
                    parts.append(".*")
                    index += 2
                else:
                    parts.append("[^/]*")
                    index += 1
                continue
            if char == "?":
                parts.append("[^/]")
                index += 1
                continue
            if char == "[":
                end = index + 1
                if end < len(glob_pattern) and glob_pattern[end] in "!^":
                    end += 1
                if end < len(glob_pattern) and glob_pattern[end] == "]":
                    end += 1
                while end < len(glob_pattern) and glob_pattern[end] != "]":
                    end += 1
                if end >= len(glob_pattern):
                    parts.append(r"\[")
                    index += 1
                    continue
                content = glob_pattern[index + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                if content in {"", "^"}:
                    parts.append(r"\[\]")
                else:
                    parts.append(f"[{content}]")
                index = end + 1
                continue
            parts.append(re.escape(char))
            index += 1
        parts.append("$")
        expression = "".join(parts)
        try:
            return re.compile(expression)
        except re.error:
            # Keep a malformed character class from turning a search into an
            # uncaught exception; treat the whole pattern literally instead.
            return re.compile("^" + re.escape(glob_pattern) + "$")

    @classmethod
    def _matches_glob(cls, file_path: Path, root: Path, glob_pattern: str) -> bool:
        """Match a ripgrep-style glob against the file name and relative path."""
        try:
            relative = file_path.relative_to(root).as_posix()
        except ValueError:
            relative = file_path.as_posix()
        # A glob containing a separator is matched against the relative path;
        # `*.py` keeps matching any file name, but `*` no longer crosses `/`.
        candidate = relative if "/" in glob_pattern else file_path.name
        return cls._glob_regex(glob_pattern).fullmatch(candidate) is not None

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
                "--no-config",
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
                command,
                cwd=self.cwd,
                limit=limit + 1,
                sandbox=self.sandbox,
            )
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"find exited {returncode}")
            files = [path for path in paths if self._find_result_allowed(path)]
            return {
                "status": "success",
                "files": files[:limit],
                "truncated": truncated or len(files) > limit,
            }

        paths: list[str] = []
        ignored = () if self._explicit_ignored_target(target) else IGNORED_DIRECTORIES
        for file_path in self._walk_files(target, ignored=ignored):
            if self._matches_glob(file_path, target, pattern):
                paths.append(str(file_path))
                if len(paths) > limit:
                    return {
                        "status": "success",
                        "files": paths[:limit],
                        "truncated": True,
                    }
        return {"status": "success", "files": paths[:limit], "truncated": False}

    def _walk_files(
        self,
        root: Path,
        *,
        ignored: tuple[str, ...] = IGNORED_DIRECTORIES,
    ) -> Iterator[Path]:
        """Yield readable files under ``root`` while respecting the sandbox."""
        ignored_names = set(ignored)
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in ignored_names]
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
