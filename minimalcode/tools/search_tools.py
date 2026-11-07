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
    inline_guard_available,
    inline_python_grep,
    run_python_grep,
    smart_case_ignore,
)
from .validation import positive_limit

_GREP_RESULT_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):")
# Sensitive-path filtering happens after ripgrep exits, so a run that returns
# only filtered paths must be retried with a wider window. The cap keeps one
# pathological search from scanning an unbounded number of lines.
_MAX_GREP_SCAN_LINES = 20_000
# `{a,b}{c,d}...` expands to 2**n patterns. A cap keeps a pathological glob
# from turning `find`/`grep` into an hour-long loop.
_MAX_GLOB_VARIANTS = 256


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
                # A single-file target otherwise prints `LINE:TEXT` with no
                # path, which the result-path filter rejects as a stray line.
                "--with-filename",
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
                command.extend(["--glob", self._anchor_glob(glob, target)])
            # Exclusions must follow the positive glob: ripgrep uses the last
            # matching --glob, so this order stays exclusion-only.
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            for sandbox_glob in self._sandbox_globs():
                command.extend(["--glob", sandbox_glob])
            command.extend(["--", pattern, str(target)])
            requested = limit + 1
            while True:
                returncode, lines, truncated, stderr = (
                    search_module.run_bounded_command(
                        command,
                        cwd=self.cwd,
                        # One line past the requested count proves truncation.
                        limit=requested,
                        sandbox=self.sandbox,
                    )
                )
                # Exit 1 means "no matches" and is not an error.
                if not truncated and returncode not in (0, 1):
                    raise ToolError(stderr or f"grep exited {returncode}")
                matches = [line for line in lines if self._grep_line_allowed(line)]
                if not truncated or len(matches) > limit or len(lines) < requested:
                    break
                # Filtered paths consumed the whole window. Read a wider one
                # so an allowed match later in the output is not lost.
                grown = min(requested * 4, _MAX_GREP_SCAN_LINES)
                if grown <= requested:
                    break
                requested = grown
            return {
                "status": "success",
                "matches": matches[:limit],
                "truncated": truncated or len(matches) > limit,
            }
        return self._python_grep(pattern, target, glob, limit)

    def _explicit_ignored_target(self, target: Path) -> bool:
        """Return True when the caller named a normally ignored directory."""
        relative = None
        for root in (self.cwd, *self.sandbox.policy.allowed_roots):
            try:
                relative = target.relative_to(root)
                break
            except ValueError:
                continue
        if relative is None:
            relative = target
        parts = list(relative.parts)
        if target.is_file():
            parts = parts[:-1]
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

    def _anchor_glob(self, glob_pattern: str, target: Path) -> str:
        """Anchor a path-bearing glob the way ripgrep matches ``--glob``.

        ripgrep compares a glob against the candidate path relative to its
        working directory (absolute only outside it), so a glob from the
        caller — which is relative to the *search root* — never matched when
        the two differed. `src/*.py` searched from `.` silently returned no
        files while the Python fallback found them. Globs without a separator
        keep matching the file name at any depth.
        """
        if "/" not in glob_pattern:
            return glob_pattern
        negated = glob_pattern.startswith("!")
        body = glob_pattern[1:] if negated else glob_pattern
        if body.startswith("/") or (len(body) > 1 and body[1] == ":"):
            return glob_pattern
        try:
            prefix = target.relative_to(self.cwd).as_posix()
        except ValueError:
            # A root outside the workspace has no cwd-relative form.
            prefix = target.as_posix()
        anchored = body if prefix in {"", "."} else f"{prefix.rstrip('/')}/{body}"
        return f"!{anchored}" if negated else anchored

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
        path = self._grep_line_path(line)
        if path is None:
            # A head/tail truncation marker or a line fragment is not a match.
            return False
        return self._allowed_result_path(path)

    def _grep_line_path(self, line: str) -> str | None:
        """Extract the file path from one `path:line:text` match line.

        A file name may itself contain ``:N:``, so the shortest prefix is not
        always the real path. The longest prefix that exists on disk wins; the
        first candidate is the fallback for a path that vanished mid-search.
        """
        match = _GREP_RESULT_RE.match(line)
        if match is None:
            return None
        fallback = match.group("path")
        chosen = fallback
        for boundary in re.finditer(r":\d+:", line):
            candidate = line[: boundary.start()]
            try:
                path = Path(candidate)
                if not path.is_absolute():
                    path = self.cwd / path
                if path.exists():
                    chosen = candidate
            except (OSError, RuntimeError, ValueError):
                continue
        return chosen

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
            # A backend without argv support still gets line-accurate results.
            # The in-process engine cannot interrupt a catastrophic regex from
            # a worker thread or on Windows, so in that case the trusted child
            # interpreter is used instead of risking a hung agent process.
            if inline_guard_available():
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
                sandbox=None,
                cwd=self.cwd,
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
    def _glob_tokens(glob_pattern: str) -> tuple[tuple[str, Any], ...]:
        """Translate a ripgrep-style glob into path-aware match tokens.

        A regex translation of repeated ``**`` runs (``**a**a**a...``) is
        exponential on a non-matching path, so matching is a small linear scan
        instead. ``*`` never crosses ``/``, ``**`` does, and ``**/`` matches
        zero or more complete directories.
        """
        tokens: list[tuple[str, Any]] = []
        index = 0
        length = len(glob_pattern)
        while index < length:
            char = glob_pattern[index]
            if char == "*":
                if glob_pattern.startswith("**/", index):
                    tokens.append(("dirs", None))
                    index += 3
                elif glob_pattern.startswith("**", index):
                    tokens.append(("any", None))
                    index += 2
                else:
                    tokens.append(("star", None))
                    index += 1
                continue
            if char == "?":
                tokens.append(("one", None))
                index += 1
                continue
            if char == "[":
                end = index + 1
                if end < length and glob_pattern[end] in "!^":
                    end += 1
                if end < length and glob_pattern[end] == "]":
                    end += 1
                while end < length and glob_pattern[end] != "]":
                    end += 1
                if end >= length:
                    tokens.append(("lit", "["))
                    index += 1
                    continue
                content = glob_pattern[index + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                if content in {"", "^"}:
                    tokens.append(("lit", "[]"))
                else:
                    try:
                        tokens.append(("class", re.compile(f"[{content}]")))
                    except re.error:
                        tokens.append(("lit", glob_pattern[index : end + 1]))
                index = end + 1
                continue
            end = index
            while end < length and glob_pattern[end] not in "*?[":
                end += 1
            tokens.append(("lit", glob_pattern[index:end]))
            index = end
        return tuple(tokens)

    @classmethod
    @lru_cache(maxsize=256)
    def _expand_braces(cls, glob_pattern: str) -> tuple[str, ...]:
        """Expand the simple ``{py,txt}`` alternation ripgrep accepts."""
        if cls._brace_variants(glob_pattern) > _MAX_GLOB_VARIANTS:
            # Matching the pattern literally beats an exponential expansion.
            return (glob_pattern,)
        return cls._expand_braces_uncapped(glob_pattern)

    @staticmethod
    def _brace_variants(glob_pattern: str) -> int:
        """Count the alternatives the brace groups would expand to."""
        count = 1
        index = 0
        while True:
            start = glob_pattern.find("{", index)
            if start < 0:
                return count
            end = glob_pattern.find("}", start + 1)
            if end < 0:
                return count
            choices = len(glob_pattern[start + 1 : end].split(","))
            if choices > 1:
                count *= choices
                if count > _MAX_GLOB_VARIANTS:
                    return count
            index = end + 1

    @classmethod
    def _expand_braces_uncapped(cls, glob_pattern: str) -> tuple[str, ...]:
        start = glob_pattern.find("{")
        if start < 0:
            return (glob_pattern,)
        end = glob_pattern.find("}", start + 1)
        if end < 0:
            return (glob_pattern,)
        choices = glob_pattern[start + 1 : end].split(",")
        if len(choices) < 2 or any(not choice for choice in choices):
            return (glob_pattern,)
        prefix = glob_pattern[:start]
        suffix = glob_pattern[end + 1 :]
        # A pattern can hold more than one group (`{a,b}.{py,txt}`); leaving a
        # later group in place matched nothing at all.
        return tuple(
            expanded
            for choice in choices
            for expanded in cls._expand_braces(prefix + choice + suffix)
        )

    @classmethod
    def _matches_glob(cls, file_path: Path, root: Path, glob_pattern: str) -> bool:
        """Match a ripgrep-style glob against the file name and relative path."""
        if glob_pattern.startswith("!"):
            # ripgrep treats a leading `!` as an exclusion.
            return not cls._matches_glob(file_path, root, glob_pattern[1:])
        return any(
            cls._matches_single_glob(file_path, root, expanded)
            for expanded in cls._expand_braces(glob_pattern)
        )

    @classmethod
    def _matches_single_glob(
        cls, file_path: Path, root: Path, glob_pattern: str
    ) -> bool:
        try:
            relative = file_path.relative_to(root).as_posix()
        except ValueError:
            relative = file_path.as_posix()
        # A glob containing a separator is matched against the relative path;
        # `*.py` keeps matching any file name, but `*` no longer crosses `/`.
        candidate = relative if "/" in glob_pattern else file_path.name
        return cls._match_glob_tokens(cls._glob_tokens(glob_pattern), candidate)

    @staticmethod
    def _match_glob_tokens(tokens: tuple[tuple[str, Any], ...], text: str) -> bool:
        """Return True when ``text`` matches the token sequence."""
        length = len(text)
        positions: set[int] = {0}
        for kind, value in tokens:
            next_positions: set[int] = set()
            if kind == "lit":
                for position in positions:
                    if text.startswith(value, position):
                        next_positions.add(position + len(value))
            elif kind == "one":
                for position in positions:
                    if position < length and text[position] != "/":
                        next_positions.add(position + 1)
            elif kind == "class":
                for position in positions:
                    if (
                        position < length
                        and text[position] != "/"
                        and value.match(text[position])
                    ):
                        next_positions.add(position + 1)
            elif kind == "star":
                for position in positions:
                    next_positions.add(position)
                    end = position
                    while end < length and text[end] != "/":
                        end += 1
                        next_positions.add(end)
            elif kind == "any":
                for position in positions:
                    next_positions.update(range(position, length + 1))
            elif kind == "dirs":
                for position in positions:
                    next_positions.add(position)
                    end = position
                    while True:
                        slash = text.find("/", end)
                        if slash < 0:
                            break
                        next_positions.add(slash + 1)
                        end = slash + 1
            positions = next_positions
            if not positions:
                return False
        return length in positions

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
                self._anchor_glob(pattern, target),
            ]
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            for sandbox_glob in self._sandbox_globs():
                command.extend(["--glob", sandbox_glob])
            command.append(str(target))
            requested = limit + 1
            while True:
                returncode, paths, truncated, stderr = (
                    search_module.run_bounded_command(
                        command,
                        cwd=self.cwd,
                        limit=requested,
                        sandbox=self.sandbox,
                    )
                )
                if not truncated and returncode not in (0, 1):
                    raise ToolError(stderr or f"find exited {returncode}")
                files = [path for path in paths if self._find_result_allowed(path)]
                if not truncated or len(files) > limit or len(paths) < requested:
                    break
                grown = min(requested * 4, _MAX_GREP_SCAN_LINES)
                if grown <= requested:
                    break
                requested = grown
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
