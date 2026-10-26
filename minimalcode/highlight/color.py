"""Terminal color capability detection."""

from __future__ import annotations

import os
from typing import TextIO


def _supports_color(stream: TextIO) -> bool:
    """Return True when ANSI styling is safe for ``stream``.

    ``NO_COLOR`` and ``TERM=dumb`` win over TTY detection, matching common CLI
    conventions. Windows console mode is enabled as a side effect.
    """
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
    """Enable virtual-terminal processing on a Windows console handle."""
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
        # Styling is cosmetic; a console API failure must not break output.
        pass
