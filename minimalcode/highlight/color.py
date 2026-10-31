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
        # Presence, not value, follows the NO_COLOR convention: `NO_COLOR=` with an
        # empty value still means "no color".
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        # Streams that cannot report a terminal (closed pipe, detached buffer)
        # are treated as unstyled rather than failing the write.
        return False
    if os.name == "nt":
        # A console that refuses virtual-terminal mode would print the SGR
        # bytes literally; report unstyled output instead.
        return _enable_windows_ansi(stream)
    return True


def _enable_windows_ansi(stream: TextIO) -> bool:
    """Enable virtual-terminal processing on a Windows console handle.

    Returns False only when a Windows console refused the mode change; callers
    that write raw escape sequences use that to avoid printing them literally.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        # 0x0004 is ENABLE_VIRTUAL_TERMINAL_PROCESSING. The current mode is read
        # first and the bit is OR-ed in so other console flags (line input, echo
        # state) survive; SetConsoleMode alone would clear them.
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        # Styling is cosmetic; a console API failure must not break output.
        return False
