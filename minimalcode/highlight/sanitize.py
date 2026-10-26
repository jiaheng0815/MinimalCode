"""Terminal control-sequence sanitization for untrusted streamed text."""

from __future__ import annotations

_CONTROL_PENDING_LIMIT = 64_000


def _csi_length(data: str, start: int) -> int | None:
    """Return the length of a CSI sequence beginning at ``start``."""
    index = start + 2
    while index < len(data):
        code = ord(data[index])
        if 0x40 <= code <= 0x7E:
            return index + 1 - start
        if not 0x20 <= code <= 0x3F:
            # Malformed CSI: consume only the ESC and '['.
            return 2
        index += 1
    return None


def _string_sequence_length(data: str, start: int, sequence_start: int) -> int | None:
    """Return the length of an OSC/DCS-style string sequence."""
    index = start
    while index < len(data):
        if data[index] == "\x07":
            return index + 1 - sequence_start
        if data[index] == "\x1b":
            if index + 1 >= len(data):
                return None
            if data[index + 1] == "\\":
                return index + 2 - sequence_start
        index += 1
    return None


class TerminalSanitizer:
    """Strip terminal control sequences before writing untrusted text.

    Model output and shell output are untrusted, but they still reach a real
    terminal. Keep newlines and tabs, drop every other C0/C1 control character,
    and remove complete or partially streamed escape sequences. Splitting the
    state across ``feed`` calls also covers sequences cut between stream deltas.
    """

    def __init__(self) -> None:
        self._pending = ""

    @staticmethod
    def _escape_length(data: str, start: int) -> int | None:
        if start + 1 >= len(data):
            return None
        marker = data[start + 1]
        if marker == "[":
            return _csi_length(data, start)
        if marker == "]":
            return _string_sequence_length(data, start + 2, start)
        if marker in "PX^_":
            return _string_sequence_length(data, start + 2, start)
        return 2

    def feed(self, text: str) -> str:
        """Consume a delta and return the safe text that is complete so far."""
        if not text:
            return ""
        data = self._pending + text
        self._pending = ""
        rendered: list[str] = []
        index = 0
        while index < len(data):
            char = data[index]
            if char == "\x1b":
                length = self._escape_length(data, index)
                if length is None:
                    # Hold an incomplete escape until the next delta.
                    self._pending = data[index:]
                    break
                index += length
                continue
            if char == "\r":
                if index + 1 >= len(data):
                    self._pending = "\r"
                    break
                if data[index + 1] == "\n":
                    rendered.append("\r\n")
                    index += 2
                else:
                    # A lone CR would move the cursor; drop it.
                    index += 1
                continue
            code = ord(char)
            if char in "\n\t":
                rendered.append(char)
            elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
                pass
            else:
                rendered.append(char)
            index += 1
        if len(self._pending) > _CONTROL_PENDING_LIMIT:
            # A hostile stream must not grow the pending buffer without bound.
            self._pending = ""
        return "".join(rendered)

    def finish(self) -> str:
        """Discard a trailing lone CR or unterminated escape sequence."""
        self._pending = ""
        return ""


def sanitize_terminal_text(text: str) -> str:
    """Return ``text`` with all terminal escape/control sequences removed."""
    sanitizer = TerminalSanitizer()
    return sanitizer.feed(text) + sanitizer.finish()
