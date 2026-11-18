"""Local chat web UI served over loopback HTTP with the standard library.

There is no build step and no JavaScript dependency: the page is one HTML file,
one stylesheet, and one script, all shipped inside the wheel.
"""

from __future__ import annotations

from .server import create_server, serve

__all__ = ["create_server", "serve"]
