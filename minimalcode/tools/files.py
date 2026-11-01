"""Workspace-bound file operations: read, write, and exact-text edit."""

from __future__ import annotations

import difflib
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ToolError
from .search import _MAX_READ_LINE_CHARS, drain_physical_line

# ``os.umask`` is process-global; serialize the read-and-restore window so a
# concurrently created directory cannot inherit a zero umask.
_UMASK_LOCK = threading.Lock()

# Failure feedback has to stay useful without turning a retry into another
# large prompt: a closest-match diff is capped at this many lines.
_MAX_DIAGNOSTIC_DIFF_LINES = 40
# Scoring every possible window is quadratic on a large file. Exact line hits
# are always scored; the remaining evenly spaced windows keep a typo-only match
# bounded on multi-megabyte inputs.
_MAX_SIMILARITY_WINDOWS = 4000

# Reported only when the requested range really is not valid UTF-8, so a plain
# read stays silent about encoding.
_NOT_UTF8_WARNING = (
    "this range is not valid UTF-8; undecodable bytes were replaced with U+FFFD "
    "and edit refuses this file"
)


@dataclass(slots=True)
class _TextScan:
    """One pass over the requested line range of a text file."""

    chunks: list[str]
    start: int | None
    end: int
    last_line: int
    truncated: bool


def _detect_newline(text: str) -> str:
    """Return the file's dominant physical line ending."""
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text and "\n" not in text:
        return "\r"
    return "\n"


def _line_ending_name(newline: str) -> str:
    return {"\r\n": "CRLF", "\r": "CR", "\n": "LF"}.get(newline, "LF")


def _normalize_newlines(text: str, newline: str) -> str:
    """Rewrite every physical line ending to ``newline``."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", newline)


def _line_number_at(text: str, offset: int) -> int:
    return len(re.findall(r"\r\n|\r|\n", text[:offset])) + 1


def _clip_diagnostic(lines: list[str]) -> str:
    """Join a diff without letting a wrong-file match flood the next request."""
    if len(lines) > _MAX_DIAGNOSTIC_DIFF_LINES:
        head = _MAX_DIAGNOSTIC_DIFF_LINES - 4
        tail = 3
        omitted = len(lines) - head - tail
        lines = [
            *lines[:head],
            f"... ({omitted} diff lines omitted) ...",
            *lines[-tail:],
        ]
    return "\n".join(lines)


def _closest_fragment(original: str, needle: str) -> tuple[int, int, float, str] | None:
    """Locate the region most similar to ``needle`` and render its diff.

    Returns ``(start_line, end_line, similarity, diff)`` with 1-based inclusive
    line numbers. ``difflib`` is only used after an exact match already failed,
    and the candidate count is bounded so this stays a repair aid rather than a
    second full-file scan.
    """
    needle_lines = _normalize_newlines(needle, "\n").splitlines()
    if not needle_lines:
        return None
    file_lines = _normalize_newlines(original, "\n").splitlines()
    if not file_lines:
        return None
    count = len(needle_lines)
    last_start = max(0, len(file_lines) - count)
    starts: set[int] = set()
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(file_lines):
        positions.setdefault(line, []).append(index)
    for offset, line in enumerate(needle_lines):
        for index in positions.get(line, ())[:50]:
            starts.add(min(max(0, index - offset), last_start))
    step = max(1, len(file_lines) // _MAX_SIMILARITY_WINDOWS)
    for index in range(0, last_start + 1, step):
        starts.add(index)
    best_start = 0
    best_ratio = -1.0
    matcher = difflib.SequenceMatcher(autojunk=False)
    for start in sorted(starts):
        window = file_lines[start : start + count]
        if not window:
            continue
        matcher.set_seqs(needle_lines, window)
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = start
    context_start = max(0, best_start - 1)
    context_end = min(len(file_lines), best_start + count + 1)
    actual = file_lines[context_start:context_end]
    diff = list(
        difflib.unified_diff(
            needle_lines,
            actual,
            fromfile="old_text",
            tofile=f"file lines {context_start + 1}-{context_end}",
            n=0,
            lineterm="",
        )
    )
    if not diff:
        # Identical line content can still reach here when the caller included
        # a trailing empty line that splitlines() dropped. Show real context so
        # the retry is not blind.
        diff = [f"{context_start + 1}: {line}" for line in actual]
    return (
        best_start + 1,
        min(len(file_lines), best_start + count),
        max(0.0, best_ratio),
        _clip_diagnostic(diff),
    )


def _not_found_message(target: Path, original: str, old_text: str) -> str:
    message = f"old_text was not found in {target}."
    fragment = _closest_fragment(original, old_text)
    if fragment is None:
        return message + " The file has no text to compare against."
    start, end, ratio, diff = fragment
    return (
        f"{message}\nMost similar region: lines {start}-{end} "
        f"({ratio:.0%} similar):\n{diff}"
    )


def _range_hint(lines: list[str], start: int, end: int) -> str:
    """Describe the valid line range nearest to an out-of-bounds request."""
    total = len(lines)
    if total == 0:
        return "the file is empty; write or append content instead"
    nearest_start = min(max(1, start), total)
    nearest_end = min(max(nearest_start, end), total)
    preview = "".join(
        f"{number:>6}\t{lines[number - 1]}"
        for number in range(nearest_start, nearest_end + 1)
    )
    if preview and not preview.endswith(("\n", "\r")):
        preview += "\n"
    return f"closest available range is lines {nearest_start}-{nearest_end}:\n{preview}"


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

        line_limit = min(self.max_file_chars + 1, _MAX_READ_LINE_CHARS)
        warnings: list[str] = []
        try:
            scan = self._scan_lines(
                target,
                start=start,
                stop=stop,
                line_limit=line_limit,
                errors="strict",
            )
        except UnicodeDecodeError:
            # Decoding with ``replace`` used to hide this: the caller saw a
            # clean read made of U+FFFD characters, and only ``edit`` later
            # refused the same file. Read it again lossily so the content is
            # still visible, and say that the bytes were not UTF-8 only when
            # the returned range actually contains a replacement character.
            scan = self._scan_lines(
                target,
                start=start,
                stop=stop,
                line_limit=line_limit,
                errors="replace",
            )
            if "\ufffd" in "".join(scan.chunks):
                warnings.append(_NOT_UTF8_WARNING)

        if scan.start is None:
            if start > max(1, scan.last_line):
                raise ToolError(f"start_line {start} is beyond the end of the file")
            result: dict[str, Any] = {
                "status": "success",
                "path": str(target),
                "content": "",
                "start_line": start,
                "end_line": 0,
                "truncated": scan.truncated,
                "eof": True,
            }
        else:
            result = {
                "status": "success",
                "path": str(target),
                "start_line": scan.start,
                "end_line": scan.end,
                "content": "".join(scan.chunks),
                "truncated": scan.truncated,
            }
        if warnings:
            result["warnings"] = warnings
        return result

    def _scan_lines(
        self,
        target: Path,
        *,
        start: int,
        stop: int | None,
        line_limit: int,
        errors: str,
    ) -> _TextScan:
        """Collect one line range, decoding with the caller's error policy.

        A strict pass is what makes a non-UTF-8 file visible: with
        ``errors="replace"`` the decode never fails, so the caller could not
        tell a clean file from one whose bytes were silently substituted.
        ``utf-8-sig`` also drops a leading BOM instead of showing it as part of
        line 1.
        """
        chunks: list[str] = []
        used = 0
        actual_start: int | None = None
        actual_end = 0
        truncated = False
        last_line = 0
        pending = ""
        # Two extra characters make an exactly-at-the-limit physical line,
        # including its terminator, readable as a complete line instead of
        # being flagged as a cut and dropping every line after it.
        read_limit = line_limit + 2
        with target.open(
            "r", encoding="utf-8-sig", errors=errors, newline=""
        ) as handle:
            number = 0
            while True:
                line = pending + handle.readline(read_limit)
                pending = ""
                if not line:
                    break
                if line.endswith("\r") and len(line) >= read_limit:
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
                line_cut = len(line) >= read_limit and not line.endswith(("\n", "\r"))
                if line_cut:
                    # The limit may still be an exact fit for a complete line
                    # that had not delivered its terminator within the limit.
                    position = handle.tell()
                    following = handle.read(1)
                    if not following:
                        line_cut = False
                    elif following == "\n":
                        line += following
                        line_cut = False
                    elif following == "\r":
                        line += following
                        peek = handle.read(1)
                        if peek == "\n":
                            line += peek
                        elif peek:
                            handle.seek(position + 1)
                        line_cut = False
                    else:
                        handle.seek(position)
                if number < start or (stop is not None and number > stop):
                    if line_cut:
                        # Skip the rest of an over-long physical line so the
                        # next readline starts a new line and the reported
                        # numbers keep matching the file.
                        drain_physical_line(handle, read_limit)
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
        return _TextScan(
            chunks=chunks,
            start=actual_start,
            end=actual_end,
            last_line=last_line,
            truncated=truncated,
        )

    @staticmethod
    def _write_atomic(target: Path, text: str, *, mode: int | None = None) -> None:
        """Write a sibling temp file, fsync it, then replace the target.

        The rename keeps readers from observing a half-written file, and the
        original mode is restored for executable scripts. A new file uses the
        process umask instead of mkstemp's 0600 so other users/groups can read
        source files created by the agent.
        """
        fd: int
        temp_name: str
        try:
            fd, temp_name = tempfile.mkstemp(
                # A temp name longer than the filesystem's limit fails with an
                # opaque EINVAL; the random suffix is what matters, not the stem.
                prefix=f".{target.name[:40]}.",
                dir=target.parent,
            )
        except OSError as exc:
            raise ToolError(f"cannot write {target}: {exc}") from exc
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is None:
                with _UMASK_LOCK:
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
        if b"\x00" in encoded:
            # `read` rejects NUL bytes as binary, so accepting them here would
            # create a file the agent can write but never read back.
            raise ToolError("binary files are not supported by write")
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
        # Never carry setuid/setgid bits onto rewritten content: the kernel
        # clears them on a normal write, and restoring them would hand the new
        # bytes the old privilege.
        mode = target.stat().st_mode & 0o777 if existed else None
        self._write_atomic(target, content, mode=mode)
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(encoded),
            "created": not existed,
        }

    def _read_for_edit(self, target: Path) -> tuple[str, str]:
        """Read an editable UTF-8 file and its dominant line ending."""
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if target.stat().st_size > self.max_file_bytes:
            raise ToolError("file is too large for edit")
        with target.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            try:
                original = handle.read()
            except UnicodeDecodeError as exc:
                raise ToolError(
                    f"file is not valid UTF-8 text: {target}: {exc}"
                ) from exc
        if "\x00" in original:
            raise ToolError("binary files are not supported by edit")
        return original, _detect_newline(original)

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Replace a line range with a patch block, or fall back to exact text.

        Line mode (``start_line``/``end_line`` plus ``new_text``) is the primary
        interface: it cannot silently match a second occurrence elsewhere in
        the file. Exact ``old_text`` remains available for files whose line
        numbers are not meaningful, and its failure now carries the closest
        region and a diff instead of a bare ``not found``.
        """
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string")
        if not isinstance(replace_all, bool):
            raise ToolError("replace_all must be a boolean")
        for name, value in (("start_line", start_line), ("end_line", end_line)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ToolError(f"{name} must be an integer or null")
        target = self._path(path, operation="write")
        original, default_newline = self._read_for_edit(target)

        line_mode = start_line is not None or end_line is not None
        if line_mode:
            if old_text is not None:
                raise ToolError(
                    "provide either start_line/end_line or old_text, not both"
                )
            if replace_all:
                raise ToolError("replace_all only applies to exact-text edits")
            if start_line is None:
                raise ToolError("start_line is required when end_line is set")
            if end_line is None:
                raise ToolError("end_line is required when start_line is set")
            lines = original.splitlines(keepends=True)
            # ``end_line == start_line - 1`` is an empty range: insert before
            # start_line. This gives the model a safe way to add a line without
            # guessing adjacent text.
            if end_line < start_line - 1:
                raise ToolError(
                    f"end_line {end_line} must be at least start_line - 1 "
                    f"({start_line - 1})"
                )
            if start_line < 1:
                raise ToolError("start_line must be at least 1")
            if start_line > len(lines) + 1:
                raise ToolError(
                    f"start_line {start_line} is beyond EOF: file has "
                    f"{len(lines)} lines; {_range_hint(lines, start_line, end_line)}"
                )
            if end_line > len(lines):
                raise ToolError(
                    f"end_line {end_line} is beyond EOF: file has {len(lines)} "
                    f"lines; {_range_hint(lines, start_line, end_line)}"
                )
            start_index = start_line - 1
            end_index = end_line
            span = "".join(lines[start_index:end_index])
            span_newline = _detect_newline(span) if span else default_newline
            replacement = _normalize_newlines(new_text, span_newline)
            if replacement:
                region_has_terminator = span.endswith(("\r", "\n"))
                following_exists = end_index < len(lines)
                if (
                    region_has_terminator or following_exists
                ) and not replacement.endswith(("\r", "\n")):
                    # A patch block is line-oriented. Without this, replacing
                    # line 7 of a 20-line file would glue line 8 onto it.
                    replacement += span_newline
            updated = (
                "".join(lines[:start_index]) + replacement + "".join(lines[end_index:])
            )
            lines_removed = end_index - start_index
            lines_added = len(replacement.splitlines())
        else:
            if not isinstance(old_text, str) or not old_text:
                raise ToolError(
                    "old_text must be a non-empty string when start_line/end_line "
                    "are omitted"
                )
            # Normalize the search text's newlines, then match any physical
            # ending in the file. This lets an LF snippet edit a CRLF file.
            needle = old_text.replace("\r\n", "\n").replace("\r", "\n")
            pattern = re.compile(
                r"(?:\r\n|\r|\n)".join(re.escape(part) for part in needle.split("\n"))
            )
            # Do not materialize every match: a one-character needle in a
            # multi-megabyte file would allocate millions of match objects even
            # when only the first occurrence is replaced.
            first = pattern.search(original)
            if first is None:
                raise ToolError(_not_found_message(target, original, old_text))
            second = pattern.search(original, first.end())
            if second is not None and not replace_all:
                locations = [
                    _line_number_at(original, match.start())
                    for match in (first, second, pattern.search(original, second.end()))
                    if match is not None
                ]
                raise ToolError(
                    f"old_text occurs more than once (first at lines "
                    f"{', '.join(str(line) for line in locations)}); make it "
                    "unique, use start_line/end_line, or set replace_all"
                )

            # Replace only the matched spans so untouched lines keep their own
            # endings.
            normalized_new = _normalize_newlines(new_text, "\n")

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
            lines_removed = len(first.group(0).splitlines())
            lines_added = len(new_text.splitlines())
        try:
            encoded = updated.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"edited content is not valid UTF-8 text: {exc}") from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolError(
                f"edited file is too large: {len(encoded)} bytes exceeds "
                f"tools.max_file_bytes ({self.max_file_bytes})"
            )
        self._write_atomic(target, updated, mode=target.stat().st_mode & 0o777)
        result: dict[str, Any] = {
            "status": "success",
            "path": str(target),
            "mode": "lines" if line_mode else "exact",
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "bytes": len(encoded),
            "line_ending": _line_ending_name(default_newline),
        }
        if line_mode:
            result["start_line"] = start_line
            result["end_line"] = end_line
        else:
            result["replacements"] = count if replace_all else 1
        return result
