"""One MCP server session: handshake, tool discovery, and tool calls.

A client owns exactly one transport and serializes requests over it, so two
tool calls never interleave inside one JSON-RPC session. Server-initiated
requests are answered from the same loop: ``ping`` and ``roots/list`` are
handled, everything else (sampling, elicitation) is refused with the standard
"method not found" error instead of being left unanswered.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from . import protocol
from .transport import create_transport

_MAX_TOOL_PAGES = 50


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """The effective configuration of one MCP server."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = True
    read_only: bool = False
    timeout_seconds: float = 60.0
    startup_timeout_seconds: float = 20.0
    include_tools: tuple[str, ...] = ()
    exclude_tools: tuple[str, ...] = ()

    @classmethod
    def from_settings(
        cls,
        name: str,
        data: Mapping[str, Any] | None,
        defaults: Mapping[str, Any] | None = None,
    ) -> MCPServerConfig:
        """Build a config from ``mcp.servers.<name>`` plus the shared block."""
        server = dict(data or {})
        shared = dict(defaults or {})

        def pick(key: str, fallback: Any) -> Any:
            value = server.get(key)
            if value is None:
                value = shared.get(key, fallback)
            return value

        def strings(value: Any) -> tuple[str, ...]:
            if not isinstance(value, (list, tuple)):
                return ()
            return tuple(str(item) for item in value)

        return cls(
            name=name,
            transport=str(pick("transport", "stdio")),
            command=str(server.get("command") or ""),
            args=strings(server.get("args")),
            env={str(k): str(v) for k, v in (server.get("env") or {}).items()},
            cwd=server.get("cwd") if isinstance(server.get("cwd"), str) else None,
            url=str(server.get("url") or ""),
            headers={str(k): str(v) for k, v in (server.get("headers") or {}).items()},
            enabled=bool(pick("enabled", True)),
            read_only=bool(server.get("read_only", False)),
            timeout_seconds=float(pick("timeout_seconds", 60.0)),
            startup_timeout_seconds=float(pick("startup_timeout_seconds", 20.0)),
            include_tools=strings(server.get("include_tools")),
            exclude_tools=strings(server.get("exclude_tools")),
        )


@dataclass(frozen=True, slots=True)
class MCPTool:
    """One discovered tool, in both provider-facing and server-side naming."""

    full_name: str
    tool_name: str
    schema: dict[str, Any]
    read_only: bool


class MCPClient:
    """A started-or-failed session with one MCP server."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        workspace: Path,
        cwd: Path | None = None,
        max_output_chars: int = 60_000,
    ) -> None:
        self.config = config
        self.name = config.name
        self.workspace = workspace
        self.cwd = cwd or workspace
        self.max_output_chars = max_output_chars
        self.tools: list[MCPTool] = []
        self.state = "new"
        self.error: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.notes: list[str] = []
        self.tools_changed = False
        self._transport: Any = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._tool_index: dict[str, MCPTool] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Connect, handshake, and discover tools.

        A partially started session is closed before the error propagates, so a
        failed server never leaves a child process behind.
        """
        try:
            self._transport = create_transport(
                transport=self.config.transport,
                command=self.config.command,
                args=self.config.args,
                cwd=self.cwd,
                env=self.config.env,
                url=self.config.url,
                headers=self.config.headers,
                timeout=self.config.timeout_seconds,
                startup_timeout=self.config.startup_timeout_seconds,
                label=self.name,
            )
            self._transport.start()
            result = self._request(
                "initialize",
                {
                    "protocolVersion": protocol.PROTOCOL_VERSION,
                    "capabilities": {"roots": {"listChanged": False}},
                    "clientInfo": {
                        "name": "minimalcode",
                        "version": __version__,
                    },
                },
                timeout=self.config.startup_timeout_seconds,
            )
            self._apply_handshake(result)
            self.refresh()
            self.state = "ready"
        except Exception as exc:
            self.error = (
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            )
            self.state = "error"
            self.close()
            raise

    def close(self) -> None:
        transport = self._transport
        self._transport = None
        if transport is not None:
            transport.close()
        if self.state != "error":
            self.state = "closed"

    # -- tools -------------------------------------------------------------

    def refresh(self) -> list[str]:
        """Re-list tools and return the provider-facing names."""
        tools: list[MCPTool] = []
        used: set[str] = set()
        cursor: str | None = None
        for _ in range(_MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise protocol.MCPError("tools/list response is missing a tools array")
            for item in page:
                if not isinstance(item, dict):
                    continue
                tool = self._parse_tool(item, used)
                if tool is not None:
                    tools.append(tool)
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        self.tools = tools
        self._tool_index = {tool.full_name: tool for tool in tools}
        self.tools_changed = False
        return [tool.full_name for tool in tools]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.schema for tool in self.tools]

    def call(
        self, full_name: str, arguments: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Run one discovered tool and convert its content into a tool result."""
        tool = self._tool_index.get(full_name)
        if tool is None:
            raise protocol.MCPError(
                f"tool {full_name!r} is not available on server {self.name!r}"
            )
        result = self._request(
            "tools/call",
            {"name": tool.tool_name, "arguments": dict(arguments or {})},
        )
        text, clipped = protocol.content_text(
            result,
            limit=self.max_output_chars,
        )
        payload: dict[str, Any] = {
            "status": "error" if result.get("isError") else "success",
            "server": self.name,
            "tool": tool.tool_name,
        }
        if text:
            payload["content"] = text
        if clipped:
            payload["truncated"] = True
        return payload

    def summary(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "transport": self.config.transport,
            "state": self.state,
            "read_only": self.config.read_only,
            "tools": [tool.tool_name for tool in self.tools],
        }
        if self.protocol_version:
            payload["protocol_version"] = self.protocol_version
        server_name = self.server_info.get("name")
        if isinstance(server_name, str) and server_name:
            payload["server"] = server_name
        server_version = self.server_info.get("version")
        if isinstance(server_version, str) and server_version:
            payload["server_version"] = server_version
        if self.config.transport == "stdio":
            payload["command"] = " ".join((self.config.command, *self.config.args))
        elif self.config.url:
            payload["url"] = self.config.url
        if self.error:
            payload["error"] = self.error
        if self.notes:
            payload["notes"] = self.notes[-3:]
        return payload

    # -- protocol ----------------------------------------------------------

    def _apply_handshake(self, result: Mapping[str, Any]) -> None:
        version = result.get("protocolVersion")
        if not isinstance(version, str) or not version.strip():
            raise protocol.MCPError("initialize response is missing protocolVersion")
        self.protocol_version = version.strip()
        if self.protocol_version not in protocol.SUPPORTED_PROTOCOL_VERSIONS:
            # The revision is newer than this client knows. The fields it
            # relies on are optional additions in every revision so far, so the
            # session continues with a note instead of dropping the server.
            self.notes.append(
                f"server protocol {self.protocol_version} is newer than "
                f"{protocol.PROTOCOL_VERSION}"
            )
        update = getattr(self._transport, "set_protocol_version", None)
        if callable(update):
            update(self.protocol_version)
        server_info = result.get("serverInfo")
        if isinstance(server_info, dict):
            self.server_info = dict(server_info)
        self._notify("notifications/initialized")
        self._notify("notifications/initialized")

    def _request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        transport = self._transport
        if transport is None:
            raise protocol.MCPError(f"MCP server {self.name!r} is not connected")
        budget = self.config.timeout_seconds if timeout is None else timeout
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            transport.send(protocol.request_message(request_id, method, params))
            deadline = time.monotonic() + budget
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise protocol.MCPTimeout(f"{method} timed out after {budget:g}s")
                message = transport.receive(remaining)
                if message is None:
                    continue
                if "method" in message:
                    self._handle_server_message(message)
                    continue
                if message.get("id") != request_id:
                    # A reply to a request that already timed out; dropping it
                    # keeps the next reply aligned with its own request.
                    continue
                if "error" in message:
                    raise protocol.error_from(message)
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        transport = self._transport
        if transport is not None:
            transport.send(protocol.notification_message(method, params))

    def _handle_server_message(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        if "id" in message:
            self._answer_server_request(message, method)
            return
        if method == "notifications/tools/list_changed":
            self.tools_changed = True

    def _answer_server_request(
        self,
        message: Mapping[str, Any],
        method: Any,
    ) -> None:
        request_id = message.get("id")
        if method == "ping":
            reply = protocol.result_message(request_id, {})
        elif method == "roots/list":
            name = self.workspace.name or "workspace"
            reply = protocol.result_message(
                request_id,
                {"roots": [{"uri": self.workspace.as_uri(), "name": name}]},
            )
        else:
            # Sampling and elicitation need a UI and a second model call; a
            # silent refusal is the protocol-defined way to say "not here".
            reply = protocol.error_message(
                request_id,
                -32601,
                f"{method} is not supported by this client",
            )
        try:
            assert self._transport is not None
            self._transport.send(reply)
        except protocol.MCPError as exc:
            # The server may have closed while asking; the in-flight request
            # still has its own deadline and error path.
            self.notes.append(str(exc))

    def _parse_tool(
        self,
        item: Mapping[str, Any],
        used: set[str],
    ) -> MCPTool | None:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        if self.config.include_tools and name not in self.config.include_tools:
            return None
        if name in self.config.exclude_tools:
            return None
        schema = protocol.tool_schema(self.name, item)
        if schema is None:
            return None
        full_name = str(schema["function"]["name"])
        if full_name in used:
            digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
            full_name = f"{full_name[: protocol.MAX_TOOL_NAME - 7]}_{digest}"
        used.add(full_name)
        schema["function"]["name"] = full_name
        return MCPTool(
            full_name=full_name,
            tool_name=name,
            schema=schema,
            read_only=self.config.read_only or protocol.tool_read_only(item),
        )
