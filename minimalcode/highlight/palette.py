"""ANSI palettes and low-level color helpers."""

from __future__ import annotations

import re

RESET = "\x1b[0m"
ACTIVITY_TAG = "\x1b[38;5;114m"

# Both palettes expose the same keys so renderers never branch on the theme.
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


def strip_ansi(text: str) -> str:
    """Return text without SGR color sequences."""
    return _ANSI_RE.sub("", text)


def _styled(text: str, style: str) -> str:
    """Wrap ``text`` in an SGR style and the reset sequence."""
    return f"{style}{text}{RESET}"
