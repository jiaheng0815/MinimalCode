"""Streamed Markdown rendering and terminal color helpers.

The renderer is split into palette, sanitizer, code-token, and Markdown state
modules. The names in ``__all__`` are the stable surface. Pre-split module-level
names are served by a temporary alias table that warns on use; see
``docs/compatibility.md`` for replacement paths and the removal target.
"""

from __future__ import annotations

import re
from typing import Any, TextIO

from .._compat import AliasTable, deprecated_attribute
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

# Removed in 0.7.0. Patching ``minimalcode.highlight._supports_color`` no longer
# affects rendering; patch ``minimalcode.highlight.color._supports_color``.
_DEPRECATED_ATTRIBUTES: AliasTable = {
    "_PALETTES": ("minimalcode.highlight.palette", "_PALETTES"),
    "_enable_windows_ansi": ("minimalcode.highlight.color", "_enable_windows_ansi"),
    "_supports_color": ("minimalcode.highlight.color", "_supports_color"),
}


def __getattr__(name: str) -> Any:
    """Serve pre-split names with a deprecation warning."""
    return deprecated_attribute(__name__, name, _DEPRECATED_ATTRIBUTES)


def style_activity_tag(text: str, stream: TextIO) -> str:
    """Color a leading ``[tool]`` activity tag when the stream supports ANSI."""
    text = sanitize_terminal_text(text)
    match = re.match(r"^(\[[^\]\n]+\])", text)
    if match is None or not color._supports_color(stream):
        return text
    tag = match.group(1)
    return f"{ACTIVITY_TAG}{tag}{RESET}{text[len(tag) :]}"
