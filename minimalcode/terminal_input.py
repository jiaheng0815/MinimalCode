"""A small multiline terminal editor for the interactive prompt.

Python's built-in ``input()`` edits one physical line at a time. Once a long
line wraps, Backspace at the visual line start cannot reach the preceding row.
This editor keeps the whole message in one buffer, renders hard wraps itself,
and therefore joins visual or logical lines with one Backspace.

It deliberately stays small: no history, syntax highlighting, or TUI
framework. The non-interactive path in ``cli._read_message`` remains unchanged.
"""

from __future__ import annotations

import codecs
import os
import sys
import unicodedata
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_PASTE_START = "[200~"
_PASTE_END = "[201~"
_MAX_PASTE = 1_000_000
_CONTROL_PREFIXES = frozenset({"\x00", "\xe0"})


def _windows_modifier_down(virtual_key: int) -> bool:
    """Return True while a Windows modifier key is physically held."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
    except (AttributeError, OSError):
        return False


@dataclass(slots=True)
class InputBuffer:
    """Editable message text and its cursor as a character offset."""

    text: str = ""
    cursor: int = 0

    def insert(self, value: str) -> None:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)

    def backspace(self) -> bool:
        if self.cursor <= 0:
            return False
        self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
        self.cursor -= 1
        return True

    def move_left(self) -> bool:
        if self.cursor <= 0:
            return False
        self.cursor -= 1
        return True

    def move_right(self) -> bool:
        if self.cursor >= len(self.text):
            return False
        self.cursor += 1
        return True

    def move_home(self) -> None:
        self.cursor = self.text.rfind("\n", 0, self.cursor) + 1

    def move_end(self) -> None:
        end = self.text.find("\n", self.cursor)
        self.cursor = len(self.text) if end < 0 else end


def _char_width(char: str) -> int:
    """Return the terminal cell width of one printable character."""
    if char == "\t":
        return 4
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    if ord(char) < 32 or ord(char) == 127:
        return 0
    return 1


def _text_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _visible(char: str) -> str:
    """Keep terminal controls out of the redraw buffer."""
    if char == "\t":
        return "    "
    if ord(char) < 32 or ord(char) == 127:
        return ""
    return char


@dataclass(slots=True)
class _Layout:
    rows: list[str]
    cursor_row: int
    cursor_column: int


def layout(
    buffer: InputBuffer,
    columns: int,
    *,
    prompt: str,
    continuation: str,
) -> _Layout:
    """Lay out hard-wrapped terminal rows and map the cursor into them."""
    width = max(1, columns - 1)
    rows: list[str] = []
    cursor_row = 0
    cursor_column = 0
    source_position = 0
    newline_count = buffer.text.count("\n")

    for line_index, line in enumerate(buffer.text.split("\n")):
        prefix = prompt if line_index == 0 else continuation
        rows.append(prefix)
        row_index = len(rows) - 1
        column = _text_width(prefix)
        if source_position == buffer.cursor:
            cursor_row, cursor_column = row_index, column

        for char in line:
            cell_width = _char_width(char)
            if cell_width and column + cell_width > width:
                rows.append("")
                row_index += 1
                column = 0
                if source_position == buffer.cursor:
                    cursor_row, cursor_column = row_index, column
            shown = _visible(char)
            rows[row_index] += shown
            column += cell_width
            source_position += 1
            if source_position == buffer.cursor:
                cursor_row, cursor_column = row_index, column

        if line_index < newline_count:
            source_position += 1

    if not rows:
        rows = [prompt]
    return _Layout(rows, cursor_row, cursor_column)


class _Output:
    def __init__(self, height: int) -> None:
        self.height = max(1, height)
        self.rows = 0
        self.cursor_row = 0
        self.started = False

    @staticmethod
    def _write(text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    def render(self, value: _Layout) -> None:
        start = max(0, len(value.rows) - self.height)
        start = min(start, value.cursor_row)
        visible = value.rows[start : start + self.height]
        cursor_row = value.cursor_row - start
        if self.started:
            self._write("\r")
            if self.cursor_row:
                self._write(f"\x1b[{self.cursor_row}A")
            self._write("\x1b[J")
        self._write("\n".join(visible))

        bottom = len(visible) - 1
        if bottom > cursor_row:
            self._write(f"\x1b[{bottom - cursor_row}A")
        self._write("\r")
        if value.cursor_column:
            self._write(f"\x1b[{value.cursor_column}C")
        self.rows = len(visible)
        self.cursor_row = cursor_row
        self.started = True

    def finish(self) -> None:
        if not self.started:
            return
        bottom = self.rows - 1
        if bottom > self.cursor_row:
            self._write(f"\x1b[{bottom - self.cursor_row}B")
        self._write("\r\n")
        self.started = False

    def clear(self) -> None:
        if not self.started:
            return
        self._write("\r")
        if self.cursor_row:
            self._write(f"\x1b[{self.cursor_row}A")
        self._write("\x1b[J")
        self.started = False


class _Keys:
    """Read semantic key events from a raw terminal."""

    def __init__(self) -> None:
        self._pending: deque[str] = deque()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def push(self, value: str) -> None:
        self._pending.extendleft(reversed(value))

    def input_ready(self, timeout: float = 0.02) -> bool:
        """Return True when the terminal has more characters queued."""
        if self._pending:
            return True
        if os.name == "nt":
            try:
                import msvcrt
                import time
            except ImportError:
                return False
            deadline = time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.002)
        try:
            import select
        except ImportError:
            return False
        try:
            readable, _, _ = select.select([sys.stdin], [], [], timeout)
        except (OSError, ValueError):
            return False
        return bool(readable)

    def _read_char(self) -> str:
        if self._pending:
            return self._pending.popleft()
        if os.name == "nt":
            import msvcrt

            return msvcrt.getwch()
        raw = os.read(sys.stdin.fileno(), 1)
        while raw:
            decoded = self._decoder.decode(raw)
            if decoded:
                return decoded
            raw = os.read(sys.stdin.fileno(), 1)
        raise EOFError

    def read(self) -> tuple[str, str]:
        """Return ``(kind, value)`` for one key, paste, interrupt, or EOF."""
        char = self._read_char()
        if os.name == "nt" and char in _CONTROL_PREFIXES:
            return "key", {
                "H": "up",
                "P": "down",
                "M": "right",
                "K": "left",
                "G": "home",
                "O": "end",
            }.get(self._read_char(), "ignore")
        if char == "\x03":
            return "interrupt", ""
        if char == "\x04":
            return "eof", ""
        if char == "\r":
            if os.name == "nt" and (
                _windows_modifier_down(0x11) or _windows_modifier_down(0x10)
            ):
                return "newline", ""
            if self.input_ready():
                following = self._read_char()
                if following != "\n":
                    self.push(following)
                return "newline", ""
            return "enter", ""
        if char == "\n":
            return "newline", ""
        if char in {"\x08", "\x7f"}:
            return "backspace", ""
        if char != "\x1b":
            return "text", char

        sequence = ""
        for _ in range(16):
            nxt = self._read_char()
            sequence += nxt
            if nxt.isalpha() or nxt == "~":
                break
        if sequence == _PASTE_START:
            return "paste", self._read_paste()
        if sequence in {
            "[13;2u",
            "[13;5u",
            "[27;2;13~",
            "[27;5;13~",
        }:
            return "newline", ""
        arrows = {
            "[A": "up",
            "[B": "down",
            "[C": "right",
            "[D": "left",
            "[H": "home",
            "[F": "end",
            "OH": "home",
            "OF": "end",
            "[1~": "home",
            "[4~": "end",
        }
        return "key", arrows.get(sequence, "ignore")

    def _read_paste(self) -> str:
        chars: list[str] = []
        tail = ""
        while len(chars) < _MAX_PASTE:
            char = self._read_char()
            tail = (tail + char)[-len(_PASTE_END) :]
            if tail == _PASTE_END:
                return "".join(chars[: -len(_PASTE_END)])
            chars.append(char)
        return "".join(chars)


@contextmanager
def _raw_terminal() -> Iterator[None]:
    if os.name == "nt":
        yield
        return
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    tty.setraw(descriptor)
    try:
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def editable_terminal() -> bool:
    """Return True when the prompt can use the multiline editor."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _automatically_continues(buffer: InputBuffer) -> bool:
    """Consume one continuation Backslash at the cursor's logical line end."""
    line_start = buffer.text.rfind("\n", 0, buffer.cursor) + 1
    line = buffer.text[line_start : buffer.cursor]
    if not line.endswith("\\") or line.endswith("\\\\"):
        return False
    buffer.cursor -= 1
    buffer.text = buffer.text[: buffer.cursor] + buffer.text[buffer.cursor + 1 :]
    buffer.insert("\n")
    return True


def read_message(
    prompt: str = "> ",
    *,
    continuation: str = "... ",
) -> str:
    """Read one message with cross-row Backspace and bracketed paste."""
    try:
        terminal_size = os.get_terminal_size(sys.stdout.fileno())
        columns = max(2, terminal_size.columns)
        lines = max(1, terminal_size.lines)
    except (AttributeError, OSError, ValueError):
        columns = 80
        lines = 24
    buffer = InputBuffer()
    output = _Output(lines)
    keys = _Keys()

    def redraw() -> None:
        output.render(
            layout(
                buffer,
                columns,
                prompt=prompt,
                continuation=continuation,
            )
        )

    output._write("\x1b[?7l")
    try:
        redraw()
        with _raw_terminal():
            while True:
                kind, value = keys.read()
                if kind == "interrupt":
                    output.clear()
                    raise KeyboardInterrupt
                if kind == "eof":
                    if not buffer.text:
                        output.clear()
                        raise EOFError
                    continue
                if kind == "enter":
                    if _automatically_continues(buffer):
                        redraw()
                        continue
                    line_start = buffer.text.rfind("\n", 0, buffer.cursor) + 1
                    if buffer.text[line_start : buffer.cursor].endswith("\\\\"):
                        buffer.backspace()
                    output.finish()
                    return buffer.text
                if kind == "newline":
                    buffer.insert("\n")
                    redraw()
                    continue
                if kind in {"text", "paste"}:
                    buffer.insert(value)
                    redraw()
                    continue
                if kind == "backspace":
                    if buffer.backspace():
                        redraw()
                    continue
                if kind == "key":
                    if value == "left":
                        buffer.move_left()
                    elif value == "right":
                        buffer.move_right()
                    elif value == "home":
                        buffer.move_home()
                    elif value == "end":
                        buffer.move_end()
                    else:
                        continue
                    redraw()
    finally:
        output._write("\x1b[?7h")
