"""Incremental Markdown renderer for streamed assistant text."""

from __future__ import annotations

import re
from typing import TextIO

from . import color
from .code import CodeHighlightMixin
from .palette import _PALETTES, _styled
from .sanitize import TerminalSanitizer

_RULE_RE = re.compile(r"([*_-])(?:[ \t]*\1){2,}")
_TABLE_SEPARATOR_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")
_TABLE_SEPARATOR_PREFIX_RE = re.compile(r"[ \t|:-]+")


def _detect_color(stream: TextIO) -> bool:
    """Delegate to the color module so a single patch target stays effective."""
    return color._supports_color(stream)


class MarkdownHighlighter(CodeHighlightMixin):
    """Render streamed Markdown with linear cursor-based parsing."""

    def __init__(
        self,
        stream: TextIO,
        *,
        theme: str = "dark",
        color: bool | None = None,
    ) -> None:
        self.stream = stream
        self.palette = _PALETTES.get(theme, _PALETTES["dark"])
        self.enabled = _detect_color(stream) if color is None else color
        self._pending = ""
        self._cursor = 0
        self._previous = ""
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode: str | None = None
        self._line_indent = 0
        self._in_fence = False
        self._fence_char = ""
        self._fence_length = 0
        self._code_comment = False
        self._code_quote: str | None = None
        self._code_escape = False
        self._code_word: list[str] = []
        self._code_last = ""
        self._sanitizer = TerminalSanitizer()

    def feed(self, delta: str) -> None:
        """Consume one streamed delta and write the now-stable prefix."""
        delta = self._sanitizer.feed(delta)
        if not delta:
            return
        if not self.enabled:
            # Redirected and JSON output stays raw Markdown.
            self._write(delta)
            return

        self._pending += delta
        segments: list[tuple[str | None, list[str]]] = []
        self._consume(segments, final=False)
        self._pending = self._pending[self._cursor :]
        self._cursor = 0
        self._write(self._render_segments(segments))

    def finish(self) -> None:
        """Flush held markers and reset state for the next stream."""
        self._sanitizer.finish()
        if self.enabled:
            segments: list[tuple[str | None, list[str]]] = []
            self._consume(segments, final=True)
            self._flush_code_word(segments)
            self._write(self._render_segments(segments))
        self._reset_stream_state()

    def _consume(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> None:
        """Advance the cursor until input is exhausted or more is needed."""
        while self._cursor < len(self._pending):
            if self._in_fence and self._at_line_start:
                if not self._consume_code_line_start(final=final):
                    break
            elif self._at_line_start:
                if not self._consume_line_start(segments, final=final):
                    break
            elif self._line_kind == "code":
                self._consume_code(segments)
            else:
                remaining = self._cursor
                self._consume_markdown(segments, final=final)
                if self._cursor == remaining:
                    # A held marker made no progress; wait for the next delta.
                    break

    def _consume_line_start(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        """Classify indentation and the block marker at the start of a line."""
        text = self._pending
        start = self._cursor
        body_start = start
        while body_start < len(text) and text[body_start] == " ":
            body_start += 1
        if body_start > start:
            # Hold the indentation until the line kind is known. A filtered
            # `| --- |` row has to take its own indentation with it, and the
            # cursor still moves forward so later deltas never rescan it.
            self._line_indent += body_start - start
            self._advance(body_start - start)
        if body_start >= len(text):
            if not final:
                return False
            self._flush_indent(segments)
            self._at_line_start = False
            return True

        if text.startswith(("\r\n", "\n"), body_start):
            ending = 2 if text.startswith("\r\n", body_start) else 1
            self._flush_indent(segments)
            self._emit(segments, body_start + ending, None)
            self._end_line()
            return True

        first = text[body_start]
        if first == "#":
            level_end = body_start
            while level_end < len(text) and text[level_end] == "#":
                level_end += 1
            level = level_end - body_start
            if level_end == len(text) and not final:
                # The run of '#' may still be growing. Committing now would fix
                # `##` and `###` headings to the h1 style whenever the marker is
                # split across deltas, so wait for the next non-'#' character.
                return False
            if level <= 6 and (level_end == len(text) or text[level_end].isspace()):
                self._flush_indent(segments)
                if level_end == len(text):
                    # The stream ended inside the marker; print it rather than
                    # swallowing the only characters the model produced.
                    self._emit(segments, level_end, self.palette[f"h{level}"])
                    self._line_kind = "heading"
                    self._heading_level = level
                    self._at_line_start = False
                    return True
                # Consume the marker (and its separating space) instead of
                # printing it, so `## Title` renders as styled heading text the
                # same way `**bold**` drops its delimiters.
                body_end = level_end + 1 if text[level_end] == " " else level_end
                self._advance(body_end - self._cursor)
                self._line_kind = "heading"
                self._heading_level = level
                self._at_line_start = False
                return True

        if first == ">":
            self._flush_indent(segments)
            self._emit(segments, body_start + 1, self.palette["quote"])
            self._line_kind = "quote"
            self._at_line_start = False
            return True

        if first in "-*_":
            line_end = text.find("\n", body_start)
            line = text[body_start:] if line_end < 0 else text[body_start:line_end]
            if _RULE_RE.fullmatch(line.strip()):
                if line_end < 0 and not final:
                    return False
                consumed = len(text) if line_end < 0 else line_end + 1
                self._flush_indent(segments)
                self._emit(segments, consumed, self.palette["rule"])
                self._end_line()
                return True
            if line_end < 0 and not final and not line.strip(" \t*_-"):
                # The line may still become a `---` / `***` rule; wait for the
                # rest of it instead of styling a partial marker as emphasis.
                return False

        if first in "-+*":
            if body_start + 1 >= len(text):
                if not final:
                    return False
            elif text[body_start + 1].isspace():
                self._flush_indent(segments)
                self._emit(segments, body_start + 1, self.palette["bullet"])
                self._emit(segments, body_start + 2, None)
                self._line_kind = "list"
                self._at_line_start = False
                return True

        number_end = body_start
        while number_end < len(text) and text[number_end].isdigit():
            number_end += 1
        number_width = number_end - body_start
        if (
            1 <= number_width <= 9
            and number_end < len(text)
            and text[number_end] in ".)"
        ):
            marker_end = number_end + 1
        else:
            marker_end = 0
        if marker_end and marker_end < len(text) and text[marker_end].isspace():
            self._flush_indent(segments)
            self._emit(segments, marker_end, self.palette["bullet"])
            self._line_kind = "list"
            self._at_line_start = False
            return True
        if (
            first.isdigit()
            and number_width <= 9
            and (number_end == len(text) or text[number_end] in ".)")
            and not final
        ):
            return False

        if first in "|-:":
            line_end = text.find("\n", body_start)
            line = text[body_start:] if line_end < 0 else text[body_start:line_end]
            if line_end < 0 and not final:
                if "|" in line and _TABLE_SEPARATOR_PREFIX_RE.fullmatch(line):
                    # Still only separator characters, so this row may yet turn
                    # into `| --- |`; wait for the rest of it before deciding.
                    return False
            elif "|" in line and _TABLE_SEPARATOR_RE.fullmatch(line.strip()):
                # Drop the `| --- |` row wholesale, indentation included, instead
                # of printing a wall of dashes and pipes between header and body.
                self._line_indent = 0
                self._advance(line_end + 1 - self._cursor)
                self._end_line()
                return True

        if first == "|":
            self._flush_indent(segments)
            self._emit(segments, body_start + 1, self.palette["rule"])
            self._line_kind = "table"
            self._at_line_start = False
            return True

        if first in "`~":
            marker_end = body_start
            while marker_end < len(text) and text[marker_end] == first:
                marker_end += 1
            marker_length = marker_end - body_start
            if marker_length >= 3:
                line_end = text.find("\n", marker_end)
                if line_end < 0 and not final:
                    return False
                consumed = len(text) if line_end < 0 else line_end + 1
                # Hide the opening fence (```python) so only the code body is
                # rendered, the same way `**bold**` drops its delimiters.
                self._line_indent = 0
                self._advance(consumed - self._cursor)
                self._in_fence = True
                self._fence_char = first
                self._fence_length = marker_length
                self._end_line()
                return True
            if marker_length == len(text) - body_start and not final:
                return False

        self._flush_indent(segments)
        self._line_kind = "normal"
        self._at_line_start = False
        return True

    def _consume_markdown(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> None:
        """Consume inline Markdown inside an ordinary block."""
        text = self._pending
        while self._cursor < len(text):
            if not final and self._should_hold():
                return
            start = self._cursor
            char = text[start]
            style = self._inline_style()

            if text.startswith("\r\n", start):
                self._emit(segments, start + 2, None)
                self._end_line()
                return
            if char == "\n":
                self._emit(segments, start + 1, None)
                self._end_line()
                return

            if self._inline_mode is None and self._line_kind == "list" and char == "[":
                if text.startswith(("[x]", "[X]"), start):
                    self._append(segments, "☑", style)
                    self._advance(3)
                    continue
                if text.startswith("[ ]", start):
                    self._append(segments, "☐", style)
                    self._advance(3)
                    continue

            if self._inline_mode == "url":
                if char == ")":
                    self._append(segments, ")", self.palette["url"])
                    self._inline_mode = None
                else:
                    self._append(segments, char, self.palette["url"])
                self._advance(1)
                continue

            if self._inline_mode is None and self._line_kind == "table" and char == "|":
                self._append(segments, char, self.palette["rule"])
                self._advance(1)
                continue

            if char == "\\":
                if start + 1 >= len(text):
                    if not final:
                        return
                    # A lone trailing backslash is still text; dropping it would
                    # silently lose the final character of the answer.
                    self._append(segments, char, style)
                    self._advance(1)
                    continue
                self._append(segments, text[start : start + 2], style)
                self._advance(2)
                continue

            if self._inline_mode is None and char == "[":
                link_result = self._consume_link_candidate(segments, final=final)
                if link_result:
                    if self._cursor == start:
                        return
                    continue

            if self._inline_mode is None:
                if self._consume_opening_marker(segments, final=final):
                    if self._cursor == start:
                        return
                    continue
            elif self._consume_closing_marker(segments, final=final):
                if self._cursor == start:
                    return
                continue

            self._append(segments, char, style)
            self._advance(1)

    def _consume_link_candidate(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        """Try to parse ``[label](url)`` without waiting on a partial label."""
        text = self._pending
        start = self._cursor
        search_end = min(len(text), start + 513)
        newline = text.find("\n", start + 1, search_end)
        if newline >= 0:
            search_end = newline
        close = text.find("]", start + 1, search_end)
        if close < 0:
            if not final and newline < 0 and search_end == len(text):
                return True
            self._append(segments, "[", None)
            self._advance(1)
            return True

        if close + 1 >= len(text) and not final:
            return True
        if close + 1 < len(text) and text[close + 1] == "(":
            self._append(segments, text[start + 1 : close], self.palette["link"])
            self._advance(close + 2 - start)
            self._append(segments, " (", self.palette["url"])
            self._inline_mode = "url"
            return True

        self._append(segments, text[start : close + 1], None)
        self._advance(close + 1 - start)
        return True

    def _consume_opening_marker(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        """Enter bold/italic/strike/code mode when a valid opener is complete."""
        text = self._pending
        start = self._cursor
        char = text[start]
        if text.startswith("**", start):
            if start + 2 >= len(text):
                if not final:
                    return True
                self._append(segments, "**", None)
                self._advance(2)
            elif text[start + 2].isspace():
                self._append(segments, "**", None)
                self._advance(2)
            else:
                self._inline_mode = "bold"
                self._advance(2)
            return True
        if text.startswith("__", start):
            if (
                start + 2 >= len(text)
                or text[start + 2].isspace()
                or self._previous.isalnum()
            ):
                if start + 2 < len(text) or final:
                    self._append(segments, "__", None)
                    self._advance(2)
                return True
            self._inline_mode = "bold"
            self._advance(2)
            return True
        if text.startswith("~~", start):
            if start + 2 >= len(text):
                if not final:
                    return True
                self._append(segments, "~~", None)
                self._advance(2)
            elif text[start + 2].isspace():
                self._append(segments, "~~", None)
                self._advance(2)
            else:
                self._inline_mode = "strike"
                self._advance(2)
            return True
        if char in "*_":
            if (
                start + 1 >= len(text)
                or text[start + 1].isspace()
                or (char == "_" and self._previous.isalnum())
            ):
                if start + 1 >= len(text) and not final:
                    return True
                self._append(segments, char, None)
                self._advance(1)
            else:
                self._inline_mode = "italic"
                self._advance(1)
            return True
        if char == "`":
            if start + 1 >= len(text):
                if not final:
                    return True
                self._append(segments, "`", None)
                self._advance(1)
            else:
                self._inline_mode = "code"
                self._advance(1)
            return True
        return False

    def _consume_closing_marker(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        """Leave the active inline mode when its closing marker is valid."""
        text = self._pending
        start = self._cursor
        char = text[start]
        mode = self._inline_mode
        if mode == "bold":
            marker = "**" if text.startswith("**", start) else "__"
            if text.startswith(marker, start):
                if start + len(marker) < len(text) or final:
                    if self._previous.isspace():
                        self._append(segments, marker, self.palette["bold"])
                    else:
                        self._inline_mode = None
                    self._advance(len(marker))
                    return True
                return True
        elif mode == "strike":
            if text.startswith("~~", start):
                if self._previous.isspace():
                    self._append(segments, "~~", self.palette["strike"])
                else:
                    self._inline_mode = None
                self._advance(2)
                return True
        elif mode == "italic" and char in "*_":
            if not self._previous.isspace():
                self._inline_mode = None
            else:
                self._append(segments, char, self.palette["italic"])
            self._advance(1)
            return True
        elif mode == "code" and char == "`":
            self._inline_mode = None
            self._advance(1)
            return True
        return False

    def _should_hold(self) -> bool:
        """Hold a single trailing marker character that may start a pair."""
        text = self._pending
        if len(text) - self._cursor == 1:
            return text[self._cursor] in "*_~`"
        return False

    def _inline_style(self) -> str | None:
        """Return the style for ordinary text in the current block/mode."""
        if self._inline_mode == "bold":
            return self.palette["bold"]
        if self._inline_mode == "italic":
            return self.palette["italic"]
        if self._inline_mode == "strike":
            return self.palette["strike"]
        if self._inline_mode == "code":
            return self.palette["inline_code"]
        if self._line_kind == "heading":
            return self.palette[f"h{self._heading_level}"]
        if self._line_kind == "quote":
            return self.palette["italic"]
        return None

    def _advance(self, count: int) -> None:
        """Move the cursor and remember the last consumed character."""
        if count <= 0:
            return
        self._cursor += count
        self._previous = self._pending[self._cursor - 1]

    def _flush_indent(
        self,
        segments: list[tuple[str | None, list[str]]],
    ) -> None:
        """Emit indentation that was held until the line kind was known."""
        indent = self._line_indent
        self._line_indent = 0
        if indent:
            self._append(segments, " " * indent, None)

    def _emit(
        self,
        segments: list[tuple[str | None, list[str]]],
        end: int,
        style: str | None,
    ) -> None:
        """Emit pending text up to ``end`` and advance past it."""
        start = self._cursor
        if end > start:
            self._append(segments, self._pending[start:end], style)
            self._advance(end - start)

    def _end_line(self) -> None:
        """Reset all per-line inline and code state."""
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode = None
        self._line_indent = 0
        self._reset_code_line()

    def _reset_stream_state(self) -> None:
        """Reset parsing state so the renderer can be reused."""
        self._pending = ""
        self._cursor = 0
        self._previous = ""
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode = None
        self._line_indent = 0
        self._in_fence = False
        self._fence_char = ""
        self._fence_length = 0
        self._reset_code_line()
        self._sanitizer = TerminalSanitizer()

    @staticmethod
    def _append(
        segments: list[tuple[str | None, list[str]]],
        text: str,
        style: str | None,
    ) -> None:
        """Append text, merging runs that share a style."""
        if not text:
            return
        if segments and segments[-1][0] == style:
            segments[-1][1].append(text)
        else:
            segments.append((style, [text]))

    @staticmethod
    def _render_segments(
        segments: list[tuple[str | None, list[str]]],
    ) -> str:
        """Render styled runs into ANSI text."""
        return "".join(
            _styled("".join(chunks), style) if style is not None else "".join(chunks)
            for style, chunks in segments
        )

    def _write(self, text: str) -> None:
        """Write and flush a rendered fragment."""
        if not text:
            return
        self.stream.write(text)
        flush = getattr(self.stream, "flush", None)
        if flush is not None:
            flush()
