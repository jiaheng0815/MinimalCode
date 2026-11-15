"""MCP client support: expose configured servers as extra agent tools.

The public surface is deliberately small: :class:`MCPManager` is what the tool
runtime owns, and the client/config types let an embedder build one without
reading a settings file.
"""

from __future__ import annotations

from .client import MCPClient, MCPServerConfig, MCPTool
from .manager import MCPManager
from .protocol import TOOL_PREFIX, MCPError, MCPTimeout, sanitize_tool_name

__all__ = [
    "TOOL_PREFIX",
    "MCPClient",
    "MCPError",
    "MCPManager",
    "MCPServerConfig",
    "MCPTimeout",
    "MCPTool",
    "sanitize_tool_name",
]
