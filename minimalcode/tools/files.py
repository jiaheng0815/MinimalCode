"""Workspace-bound file operations: read, write, and exact-text edit."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..errors import ToolError
from .search import _MAX_READ_LINE_CHARS, drain_physical_line


class FileToolsMixin:
    """File methods mixed into :class:`~minimalcode.tools.ToolRuntime`.

    All paths are resolved by the runtime's sandbox-aware ``_path`` helper
    before any read or write occurs.
    """

    def read(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Read a UTF-8 text file by 1-based line range with line numbers."""
        target = self._path(path, operation="read")
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if isinstance(start_line, bool) or not isinstance(start_line, int):
            raise ToolError("start_line must be an integer")
        if end_line is not None and (
            isinstance(end_line, bool) or not isinstance(end_line, int)
        ):
            raise ToolError("end_line must be an integer or null")
        start = max(1, start_line)
        stop = end_line
        if stop is not None and stop < start:
            raise ToolError("end_line must be greater than or equal to start_line")

        # Reject binary input before decoding so a NUL-containing file is not
        # rendered as a wall of replacement characters. The sample catches the
        # common case early; the per-line check below catches a NUL that only
        # appears after the sample.
        with target.open("rb") as binary:
            sample = binary.read(65536)
        if b"\x00" in sample:
            raise ToolError("binary files are not supported by read")

        chunks: list[str] = []
        used = 0
        actual_start = None
        actual_end = 0
        truncated = False
        last_line = 0
        pending = ""
        line_limit = min(self.max_file_chars + 1, _MAX_READ_LINE_CHARS)
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            number = 0
            while True:
                line = pending + handle.readline(line_limit)
                pending = ""
                if not line:
                    break
                if line.endswith("\r") and len(line) >= line_limit:
                    # A limit that lands between CR and LF must not turn the
                    # LF into a separate empty line. Peek one character and
                    # keep it for the next iteration when it is real content.
                    following = handle.read(1)
                    if following == "\n":
                        line += following
                    else:
                        pending = following
                number += 1
                last_line = number
                if "\x00" in line:
                    raise ToolError("binary files are not supported by read")
                line_cut = len(line) >= line_limit and not line.endswith(("\n", "\r"))
                if number < start or (stop is not None and number > stop):
                    if line_cut:
                        # Skip the rest of an over-long physical line so the
                        # next readline starts a new line and the reported
                        # numbers keep matching the file.
                        drain_physical_line(handle, line_limit)
                    if stop is not None and number > stop:
                        break
                    continue
                if line_cut:
                    truncated = True
                rendered = f"{number:>6}\t{line.rstrip(chr(13) + chr(10))}\n"
                remaining = self.max_file_chars - used
                if len(rendered) > remaining:
                    truncated = True
                    if actual_start is None and remaining > 0:
                        # A first line larger than the budget must not look like
                        # an empty file; return its prefix so the caller sees
                        # content.
                        chunks.append(rendered[:remaining])
                        actual_start = number
                        actual_end = number
                    break
                if actual_start is None:
                    actual_start = number
                chunks.append(rendered)
                used += len(rendered)
                actual_end = number
                if line_cut:
                    break

        if actual_start is None:
            if start > max(1, last_line):
                raise ToolError(f"start_line {start} is beyond the end of the file")
            return {
                "status": "success",
                "path": str(target),
                "content": "",
                "start_line": start,
                "end_line": 0,
                "truncated": truncated,
                "eof": True,
            }
        return {
            "status": "success",
            "path": str(target),
            "start_line": actual_start,
            "end_line": actual_end,
            "content": "".join(chunks),
            "truncated": truncated,
        }

    @staticmethod
    def _write_atomic(target: Path, text: str, *, mode: int | None = None) -> None:
        """Write a sibling temp file, fsync it, then replace the target.

        The rename keeps readers from observing a half-written file, and the
        original mode is restored for executable scripts. A new file uses the
        process umask instead of mkstemp's 0600 so other users/groups can read
        source files created by the agent.
        """
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is None:
                current_umask = os.umask(0)
                os.umask(current_umask)
                mode = 0o666 & ~current_umask
            try:
                os.chmod(temp_path, mode)
            except OSError:
                pass
            os.replace(temp_path, target)
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
            except OSError:
                directory_fd = -1
            if directory_fd >= 0:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
                finally:
                    os.close(directory_fd)
        finally:
            # A failed write or replace must not leave a hidden temp file.
            temp_path.unlink(missing_ok=True)

    def write(self, path: str, content: str) -> dict[str, Any]:
        """Create or fully replace a UTF-8 text file."""
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"content is not valid UTF-8 text: {exc}") from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolError(
                f"content is too large for write: {len(encoded)} bytes exceeds "
                f"tools.max_file_bytes ({self.max_file_bytes})"
            )
        target = self._path(path, operation="write")
        if target.exists() and target.is_dir():
            raise ToolError(f"path is a directory: {target}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(f"cannot create parent directory: {exc}") from exc
        existed = target.exists()
        mode = target.stat().st_mode & 0o7777 if existed else None
        self._write_atomic(target, content, mode=mode)
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(encoded),
            "created": not existed,
        }

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Replace exact text while preserving each region's line endings."""
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string")
        if not isinstance(replace_all, bool):
            raise ToolError("replace_all must be a boolean")
        target = self._path(path, operation="write")
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if target.stat().st_size > self.max_file_bytes:
            raise ToolError("file is too large for edit")

        with target.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            original = handle.read()
        if "\r\n" in original:
            default_newline = "\r\n"
        elif "\r" in original and "\n" not in original:
            default_newline = "\r"
        else:
            default_newline = "\n"

        # Normalize the search text's newlines, then match any physical ending
        # in the file. This lets an LF snippet edit a CRLF file.
        needle = old_text.replace("\r\n", "\n").replace("\r", "\n")
        pattern = re.compile(
            r"(?:\r\n|\r|\n)".join(re.escape(part) for part in needle.split("\n"))
        )
        # Do not materialize every match: a one-character needle in a
        # multi-megabyte file would allocate millions of match objects even
        # when only the first occurrence is replaced.
        first = pattern.search(original)
        if first is None:
            raise ToolError("old_text was not found")
        second = pattern.search(original, first.end())
        if second is not None and not replace_all:
            raise ToolError(
                "old_text occurs more than once; make it unique or set replace_all"
            )

        # Replace only the matched spans so untouched lines keep their own endings.
        normalized_new = new_text.replace("\r\n", "\n").replace("\r", "\n")

        def replacement_for(span: str) -> str:
            if "\r\n" in span:
                span_newline = "\r\n"
            elif "\r" in span:
                span_newline = "\r"
            elif "\n" in span:
                span_newline = "\n"
            else:
                span_newline = default_newline
            return normalized_new.replace("\n", span_newline)

        if replace_all:
            pieces: list[str] = []
            last = 0
            count = 0
            for count, match in enumerate(pattern.finditer(original), start=1):
                pieces.append(original[last : match.start()])
                pieces.append(replacement_for(match.group(0)))
                last = match.end()
            pieces.append(original[last:])
            updated = "".join(pieces)
        else:
            updated = (
                original[: first.start()]
                + replacement_for(first.group(0))
                + original[first.end() :]
            )
            count = 1
        try:
            encoded = updated.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"edited content is not valid UTF-8 text: {exc}") from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolError(
                f"edited file is too large: {len(encoded)} bytes exceeds "
                f"tools.max_file_bytes ({self.max_file_bytes})"
            )
        self._write_atomic(target, updated, mode=target.stat().st_mode & 0o7777)
        return {
            "status": "success",
            "path": str(target),
            "replacements": count if replace_all else 1,
        }
