"""Streamed Markdown rendering and terminal color helpers.

The renderer is split into palette, sanitizer, code-token, Markdown state, and
terminal capability modules. ``__all__`` is the complete public surface; the
private helpers that lived on the pre-0.7 single ``highlight`` module are
imported from the submodule that defines them.
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
