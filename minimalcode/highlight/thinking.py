"""Render streamed reasoning text in the Markdown six-level heading style."""

from __future__ import annotations

from typing import TextIO

from . import color
from .palette import _PALETTES, _styled
from .sanitize import TerminalSanitizer


def _detect_color(stream: TextIO) -> bool:
    """Delegate to the color module so a single patch target stays effective."""
    return color._supports_color(stream)


class ThinkingRenderer:
    """Write reasoning deltas the way a ``######`` heading is styled.

    Reasoning text is not Markdown, so it does not go through the parser: each
    delta is sanitized and wrapped in the palette's ``h6`` sequence as one block.
    Nothing is written when the stream cannot take ANSI, which keeps redirected
    output and ``--json`` equal to the answer alone.
    """

    def __init__(
        self,
        stream: TextIO,
        *,
        theme: str = "dark",
        color: bool | None = None,
    ) -> None:
        self.stream = stream
        self.style = _PALETTES.get(theme, _PALETTES["dark"])["h6"]
        self.enabled = _detect_color(stream) if color is None else color
        self._sanitizer = TerminalSanitizer()
        self._last_char = ""
        self._open = False

    def feed(self, delta: str) -> None:
        """Write one reasoning delta in the heading style."""
        if not self.enabled:
            return
        text = self._sanitizer.feed(delta)
        if not text:
            return
        self.stream.write(_styled(text, self.style))
        self.stream.flush()
        self._last_char = text[-1]
        self._open = True

    def finish(self) -> None:
        """Close the reasoning block so the answer starts on its own line."""
        if not self.enabled or not self._open:
            return
        self._sanitizer.finish()
        if self._last_char and self._last_char != "\n":
            self.stream.write("\n")
            self.stream.flush()
        self._sanitizer = TerminalSanitizer()
        self._last_char = ""
        self._open = False
