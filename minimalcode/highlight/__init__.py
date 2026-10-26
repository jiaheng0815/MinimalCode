"""Streamed Markdown rendering and terminal color helpers.

The renderer turns assistant deltas into styled terminal output as they arrive,
so text appears while the model is still producing it, and it strips terminal
control sequences from untrusted text before that text reaches a real terminal.
``__all__`` is the complete public surface: the renderer, the sanitizer and its
one-shot wrapper, the palette constants, and the helpers that color an activity
tag or remove SGR codes. Deciding what to print, drawing progress indicators, and
reading theme settings belong to ``cli``/``agent``, and the parser covers the
Markdown this project emits rather than all of CommonMark. Invariants: every byte
of untrusted input passes through ``TerminalSanitizer`` first, and with color
disabled the renderer writes the original Markdown unchanged so redirected and
JSON output stay parseable; the private helpers that lived on the pre-0.7 single
``highlight`` module are imported from the submodule that defines them.
"""

from __future__ import annotations

import re
from typing import TextIO

from . import color
from .markdown import MarkdownHighlighter
from .palette import ACTIVITY_TAG, RESET, strip_ansi
from .sanitize import TerminalSanitizer, sanitize_terminal_text

__all__ = [
    "ACTIVITY_TAG",
    "RESET",
    "MarkdownHighlighter",
    "TerminalSanitizer",
    "sanitize_terminal_text",
    "strip_ansi",
    "style_activity_tag",
]


def style_activity_tag(text: str, stream: TextIO) -> str:
    """Color a leading ``[tool]`` activity tag when the stream supports ANSI."""
    text = sanitize_terminal_text(text)
    match = re.match(r"^(\[[^\]\n]+\])", text)
    if match is None or not color._supports_color(stream):
        return text
    tag = match.group(1)
    return f"{ACTIVITY_TAG}{tag}{RESET}{text[len(tag) :]}"
