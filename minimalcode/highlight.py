from __future__ import annotations

import os
import re
from typing import TextIO

RESET = "\x1b[0m"
ACTIVITY_TAG = "\x1b[38;5;114m"

_PALETTES = {
    "dark": {
        "h1": "\x1b[1;38;5;51m",
        "h2": "\x1b[1;38;5;45m",
        "h3": "\x1b[1;38;5;48m",
        "h4": "\x1b[1;38;5;111m",
        "h5": "\x1b[1;38;5;249m",
        "h6": "\x1b[2;38;5;245m",
        "rule": "\x1b[38;5;238m",
        "bold": "\x1b[1;38;5;229m",
        "italic": "\x1b[3;38;5;250m",
        "strike": "\x1b[9;38;5;245m",
        "inline_code": "\x1b[38;5;222;48;5;236m",
        "fence": "\x1b[38;5;108m",
        "code_comment": "\x1b[3;38;5;244m",
        "code_string": "\x1b[38;5;186m",
        "code_number": "\x1b[38;5;141m",
        "code_keyword": "\x1b[38;5;197m",
        "code_name": "\x1b[38;5;81m",
        "code_variable": "\x1b[38;5;114m",
        "bullet": "\x1b[38;5;214m",
        "quote": "\x1b[38;5;244m",
        "link": "\x1b[4;38;5;75m",
        "url": "\x1b[38;5;242m",
    },
    "light": {
        "h1": "\x1b[1;38;5;25m",
        "h2": "\x1b[1;38;5;24m",
        "h3": "\x1b[1;38;5;29m",
        "h4": "\x1b[1;38;5;55m",
        "h5": "\x1b[1;38;5;238m",
        "h6": "\x1b[2;38;5;240m",
        "rule": "\x1b[38;5;250m",
        "bold": "\x1b[1;38;5;130m",
        "italic": "\x1b[3;38;5;238m",
        "strike": "\x1b[9;38;5;240m",
        "inline_code": "\x1b[38;5;166;48;5;254m",
        "fence": "\x1b[38;5;66m",
        "code_comment": "\x1b[3;38;5;244m",
        "code_string": "\x1b[38;5;130m",
        "code_number": "\x1b[38;5;91m",
        "code_keyword": "\x1b[38;5;161m",
        "code_name": "\x1b[38;5;25m",
        "code_variable": "\x1b[38;5;28m",
        "bullet": "\x1b[38;5;166m",
        "quote": "\x1b[38;5;243m",
        "link": "\x1b[4;38;5;25m",
        "url": "\x1b[38;5;242m",
    },
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_NUMBER_RE = re.compile(
    r"(?<![\w.])-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?!\w)"
)
_VARIABLE_RE = re.compile(r"\$(?:\w+:)?\w+|\b[A-Za-z_]\w*-\w+\b")
_KEYWORDS = {
    "and",
    "as",
    "async",
    "await",
    "begin",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "def",
    "do",
    "else",
    "elseif",
    "end",
    "enum",
    "except",
    "export",
    "False",
    "false",
    "finally",
    "for",
    "foreach",
    "from",
    "function",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "let",
    "match",
    "new",
    "None",
    "not",
    "null",
    "or",
    "param",
    "pass",
    "process",
    "raise",
    "return",
    "switch",
    "throw",
    "True",
    "true",
    "try",
    "undefined",
    "using",
    "var",
    "while",
    "with",
    "yield",
}
_KEYWORD_RE = re.compile(r"\b(?:" + "|".join(sorted(_KEYWORDS)) + r")\b")


def strip_ansi(text: str) -> str:
    """Return text without SGR color sequences."""
    return _ANSI_RE.sub("", text)


def style_activity_tag(text: str, stream: TextIO) -> str:
    """Color a leading ``[tool]`` activity tag when the stream supports ANSI."""
    match = re.match(r"^(\[[^\]\n]+\])", text)
    if match is None or not _supports_color(stream):
        return text
    tag = match.group(1)
    return f"{ACTIVITY_TAG}{tag}{RESET}{text[len(tag) :]}"


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False
    _enable_windows_ansi(stream)
    return True


def _enable_windows_ansi(stream: TextIO) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError, ValueError):
        pass


def _styled(text: str, style: str) -> str:
    return f"{style}{text}{RESET}"


class MarkdownHighlighter:
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
        self.enabled = _supports_color(stream) if color is None else color
        self._pending = ""
        self._cursor = 0
        self._previous = ""
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode: str | None = None
        self._in_fence = False
        self._fence_char = ""
        self._fence_length = 0
        self._code_comment = False
        self._code_quote: str | None = None
        self._code_escape = False
        self._code_word: list[str] = []
        self._code_last = ""

    def feed(self, delta: str) -> None:
        if not delta:
            return
        if not self.enabled:
            self._write(delta)
            return

        self._pending += delta
        segments: list[tuple[str | None, list[str]]] = []
        self._consume(segments, final=False)
        self._pending = self._pending[self._cursor :]
        self._cursor = 0
        self._write(self._render_segments(segments))

    def finish(self) -> None:
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
        while self._cursor < len(self._pending):
            if self._in_fence and self._at_line_start:
                if not self._consume_code_line_start(segments, final=final):
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
                    break

    def _consume_line_start(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        text = self._pending
        start = self._cursor
        body_start = start
        while body_start < len(text) and text[body_start] == " ":
            body_start += 1
        leading = body_start - start
        if body_start >= len(text):
            if not final:
                # Emit the scanned indentation now so the next delta resumes at the
                # first non-space byte instead of rescanning the whole prefix.
                self._emit(segments, body_start, None)
                return False
            self._emit(segments, body_start, None)
            self._at_line_start = False
            return True

        if text.startswith(("\r\n", "\n"), body_start):
            ending = 2 if text.startswith("\r\n", body_start) else 1
            self._emit(segments, body_start + ending, None)
            self._end_line()
            return True

        first = text[body_start]
        if first == "#":
            level_end = body_start
            while level_end < len(text) and text[level_end] == "#":
                level_end += 1
            level = level_end - body_start
            if level <= 6 and (level_end == len(text) or text[level_end].isspace()):
                style = self.palette[f"h{level}"]
                self._emit(segments, level_end, style)
                self._line_kind = "heading"
                self._heading_level = level
                self._at_line_start = False
                return True

        if first == ">":
            self._emit(segments, body_start + 1, self.palette["quote"])
            self._line_kind = "quote"
            self._at_line_start = False
            return True

        if first in "-+*":
            if body_start + 1 >= len(text):
                if not final:
                    return False
            elif text[body_start + 1].isspace():
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

        if first == "|":
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
                self._emit(segments, consumed, self.palette["fence"])
                self._in_fence = True
                self._fence_char = first
                self._fence_length = marker_length
                self._end_line()
                return True
            if marker_length == len(text) - body_start and not final:
                return False

        if leading:
            self._emit(segments, body_start, None)
        self._line_kind = "normal"
        self._at_line_start = False
        return True

    def _consume_markdown(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> None:
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
                    return
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
        text = self._pending
        if len(text) - self._cursor == 1:
            return text[self._cursor] in "*_~`"
        return False

    def _consume_code_line_start(
        self,
        segments: list[tuple[str | None, list[str]]],
        *,
        final: bool,
    ) -> bool:
        text = self._pending
        start = self._cursor
        line_end = text.find("\n", start)
        line = text[start:] if line_end < 0 else text[start:line_end]
        marker = re.match(rf"^ {{0,3}}({re.escape(self._fence_char)}+)(.*)$", line)
        if marker:
            marker_text = marker.group(1)
            remainder = marker.group(2).strip()
            if (
                len(marker_text) >= self._fence_length
                and not remainder
                and (line_end >= 0 or final)
            ):
                consumed = len(text) if line_end < 0 else line_end + 1
                self._emit(segments, consumed, self.palette["fence"])
                self._in_fence = False
                self._end_line()
                return True
            if line_end < 0 and not remainder and not final:
                return False

        self._line_kind = "code"
        self._at_line_start = False
        self._reset_code_line()
        return True

    def _consume_code(
        self,
        segments: list[tuple[str | None, list[str]]],
    ) -> None:
        text = self._pending
        start = self._cursor
        char = text[start]
        if text.startswith("\r\n", start):
            self._flush_code_word(segments)
            self._emit(segments, start + 2, None)
            self._end_line()
            return
        if char == "\n":
            self._flush_code_word(segments)
            self._emit(segments, start + 1, None)
            self._end_line()
            return

        if self._code_comment:
            self._append(segments, char, self.palette["code_comment"])
            self._advance(1)
            self._code_last = char
            return

        if self._code_quote is not None:
            self._append(segments, char, self.palette["code_string"])
            self._advance(1)
            if self._code_escape:
                self._code_escape = False
            elif char == "\\":
                self._code_escape = True
            elif char == self._code_quote:
                self._code_quote = None
            self._code_last = char
            return

        comment_start = (
            char == "#" and (not self._code_last or self._code_last.isspace())
        ) or text.startswith("//", start)
        if comment_start:
            self._flush_code_word(segments)
            token = "#" if char == "#" else "//"
            self._append(segments, token, self.palette["code_comment"])
            self._advance(len(token))
            self._code_comment = True
            self._code_last = token[-1]
            return

        if char in "\"'`":
            self._flush_code_word(segments)
            self._append(segments, char, self.palette["code_string"])
            self._advance(1)
            self._code_quote = char
            self._code_escape = False
            self._code_last = char
            return

        if char.isalnum() or char in "_$:.-":
            self._code_word.append(char)
            self._advance(1)
            self._code_last = char
            return

        self._flush_code_word(segments)
        self._append(segments, char, None)
        self._advance(1)
        self._code_last = char

    def _flush_code_word(
        self,
        segments: list[tuple[str | None, list[str]]],
    ) -> None:
        word = "".join(self._code_word)
        self._code_word.clear()
        if not word:
            return
        if _KEYWORD_RE.fullmatch(word):
            style = self.palette["code_keyword"]
        elif _NUMBER_RE.fullmatch(word):
            style = self.palette["code_number"]
        elif _VARIABLE_RE.fullmatch(word):
            style = self.palette["code_variable"]
        else:
            style = self.palette["code_name"]
        self._append(segments, word, style)

    def _inline_style(self) -> str | None:
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
        if count <= 0:
            return
        self._cursor += count
        self._previous = self._pending[self._cursor - 1]

    def _emit(
        self,
        segments: list[tuple[str | None, list[str]]],
        end: int,
        style: str | None,
    ) -> None:
        start = self._cursor
        if end > start:
            self._append(segments, self._pending[start:end], style)
            self._advance(end - start)

    def _end_line(self) -> None:
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode = None
        self._reset_code_line()

    def _reset_code_line(self) -> None:
        self._code_comment = False
        self._code_quote = None
        self._code_escape = False
        self._code_last = ""
        self._code_word.clear()

    def _reset_stream_state(self) -> None:
        self._pending = ""
        self._cursor = 0
        self._previous = ""
        self._at_line_start = True
        self._line_kind = "normal"
        self._heading_level = 0
        self._inline_mode = None
        self._in_fence = False
        self._fence_char = ""
        self._fence_length = 0
        self._reset_code_line()

    @staticmethod
    def _append(
        segments: list[tuple[str | None, list[str]]],
        text: str,
        style: str | None,
    ) -> None:
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
        return "".join(
            _styled("".join(chunks), style) if style is not None else "".join(chunks)
            for style, chunks in segments
        )

    def _write(self, text: str) -> None:
        if not text:
            return
        self.stream.write(text)
        flush = getattr(self.stream, "flush", None)
        if flush is not None:
            flush()
