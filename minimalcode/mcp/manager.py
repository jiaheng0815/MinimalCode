"""Own every configured MCP server for one tool runtime.

Starting a server is I/O, not configuration: a server that is missing, crashes,
or hangs must not stop the agent from answering, so failures are recorded per
server and surfaced through ``/status`` and ``/mcp`` instead of raising. Tool
names are namespaced as ``mcp__<server>__<tool>`` so a server tool can never
shadow a built-in one.
"""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..errors import ToolError
from .client import MCPClient, MCPServerConfig, MCPTool
from .protocol import TOOL_PREFIX, MCPError, MCPTimeout

_START_PARALLEL = 4


class MCPManager:
    """The set of MCP servers attached to one :class:`ToolRuntime`."""

    def __init__(
        self,
        *,
        configs: Sequence[MCPServerConfig] = (),
        workspace: Path,
        max_output_chars: int = 60_000,
        max_tools: int = 200,
        enabled: bool = True,
        cwd_resolver: Callable[[MCPServerConfig], Path] | None = None,
    ) -> None:
        self.workspace = workspace
        self.enabled = enabled
        self.max_output_chars = max(1, int(max_output_chars))
        self.max_tools = max(1, int(max_tools))
        self._configs = list(configs)
        self._cwd_resolver = cwd_resolver
        self._clients: list[MCPClient] = []
        self._tools: dict[str, tuple[MCPClient, MCPTool]] = {}
        self._notes: list[str] = []
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        workspace: Path,
        *,
        sandbox: Any = None,
    ) -> MCPManager:
        """Read ``mcp`` settings into a manager that has not started yet."""
        block = settings.get("mcp")
        if not isinstance(block, dict):
            block = {}
        enabled = bool(block.get("enabled", True))
        servers = block.get("servers")
        configs: list[MCPServerConfig] = []
        if enabled and isinstance(servers, dict):
            for name, data in servers.items():
                if not isinstance(data, dict):
                    continue
                config = MCPServerConfig.from_settings(name, data, block)
                if config.enabled:
                    configs.append(config)
        allow_outside = bool(settings.get("tools.allow_outside_workspace", False))

        def resolve_cwd(config: MCPServerConfig) -> Path:
            if config.cwd is None:
                return workspace
            if sandbox is None:
                path = Path(config.cwd).expanduser()
                if not path.is_absolute():
                    path = workspace / path
                return path.resolve()
            return sandbox.resolve_path(
                config.cwd,
                cwd=workspace,
                operation="read",
                allow_outside=allow_outside,
            )

        return cls(
            configs=configs,
            workspace=workspace,
            max_output_chars=int(block.get("max_output_chars", 60_000)),
            max_tools=int(block.get("max_tools", 200)),
            enabled=enabled,
            cwd_resolver=resolve_cwd,
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> MCPManager:
        """Start every enabled server, recording failures per server."""
        if self._started or self._closed:
            return self
        self._started = True
        clients: list[MCPClient] = []
        for config in self._configs:
            clients.append(
                MCPClient(
                    config,
                    workspace=self.workspace,
                    cwd=self._client_cwd(config),
                    max_output_chars=self.max_output_chars,
                )
            )
        self._clients = clients
        if not clients:
            return self
        workers = min(_START_PARALLEL, len(clients))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._start_one, client) for client in clients]
            for future in futures:
                future.result()
        self._reindex()
        return self

    def _start_one(self, client: MCPClient) -> None:
        try:
            client.start()
        except Exception as exc:  # noqa: BLE001 - one server must not stop the run
            client.error = str(client.error or f"{type(exc).__name__}: {exc}")
            client.state = "error"

    def _client_cwd(self, config: MCPServerConfig) -> Path:
        if self._cwd_resolver is None:
            return self.workspace
        try:
            return self._cwd_resolver(config)
        except Exception as exc:  # noqa: BLE001 - reported through the summary
            self._notes.append(f"{config.name}: cwd rejected: {exc}")
            return self.workspace

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients)
        for client in clients:
            client.close()

    # -- tools -------------------------------------------------------------

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Return provider declarations, capped by ``mcp.max_tools``."""
        with self._lock:
            entries = list(self._tools.values())
        schemas: list[dict[str, Any]] = []
        for _, tool in entries:
            if len(schemas) >= self.max_tools:
                note = (
                    f"only the first {self.max_tools} MCP tools are advertised; "
                    "raise mcp.max_tools or narrow include_tools"
                )
                if note not in self._notes:
                    self._notes.append(note)
                break
            schemas.append(tool.schema)
        return schemas

    def is_read_only(self, name: str) -> bool:
        entry = self._tools.get(name)
        return bool(entry and entry[1].read_only)

    def owns(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        """Run one ``mcp__`` tool, reporting failures as tool errors."""
        entry = self._tools.get(name)
        if entry is None:
            raise ToolError(f"unknown MCP tool: {name}")
        client, _ = entry
        try:
            return client.call(name, arguments)
        except MCPTimeout as exc:
            raise ToolError(f"MCP server {client.name!r} timed out: {exc}") from exc
        except MCPError as exc:
            raise ToolError(f"MCP server {client.name!r}: {exc}") from exc
        except OSError as exc:
            raise ToolError(f"MCP server {client.name!r}: {exc}") from exc

    def refresh(self) -> list[str]:
        """Re-list ready servers and retry failed ones; return new tool names."""
        added: list[str] = []
        with self._lock:
            clients = list(self._clients)
            for client in clients:
                before = {tool.full_name for tool in client.tools}
                if client.state == "ready":
                    self._refresh_client(client)
                elif client.state == "error":
                    self._retry_start(client)
                added.extend(sorted({tool.full_name for tool in client.tools} - before))
            self._reindex()
        return added

    @staticmethod
    def _refresh_client(client: MCPClient) -> None:
        try:
            client.refresh()
        except (MCPError, OSError) as exc:
            # Keep the tools that were already discovered: a transient failure
            # should not empty the tool list for the rest of the session.
            client.notes.append(f"refresh failed: {exc}")

    @staticmethod
    def _retry_start(client: MCPClient) -> None:
        try:
            client.start()
        except Exception as exc:  # noqa: BLE001 - reported in the summary
            client.error = str(client.error or f"{type(exc).__name__}: {exc}")
            client.state = "error"

    def summary(self) -> dict[str, Any]:
        with self._lock:
            clients = list(self._clients)
            tools = len(self._tools)
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "configured": len(self._configs),
            "tools": tools,
            "servers": [client.summary() for client in clients],
        }
        if self._notes:
            payload["notes"] = list(self._notes[-5:])
        return payload

    def _reindex(self) -> None:
        index: dict[str, tuple[MCPClient, MCPTool]] = {}
        for client in self._clients:
            for tool in client.tools:
                if tool.full_name.startswith(TOOL_PREFIX):
                    index.setdefault(tool.full_name, (client, tool))
        self._tools = index


def mcp_tool_server(name: str) -> str | None:
    """Return the server segment of a namespaced tool name, if any."""
    if not name.startswith(TOOL_PREFIX):
        return None
    parts = name.split("__", 2)
    return parts[1] if len(parts) > 1 and parts[1] else None
