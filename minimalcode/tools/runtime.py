"""The ToolRuntime facade: configuration, path policy, and dispatch."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ConfigError, ToolError
from ..mcp import MCPManager
from ..mcp.protocol import TOOL_PREFIX
from ..sandbox import SandboxRunner
from ..shell import ShellSpec
from ..subagent import depth_from_env as subagent_depth_from_env
from ..vision import ProviderVisionClient
from ..web import ProviderWebClient
from .files import FileToolsMixin
from .media_tools import MediaToolsMixin
from .process import ProcessToolsMixin
from .remote import RemoteToolsMixin
from .schemas import READ_ONLY_TOOLS, TOOL_NAMES
from .search_tools import SearchToolsMixin
from .validation import check_argument_types, clip


def is_mcp_tool(name: str) -> bool:
    """Return True when ``name`` is namespaced to an MCP server."""
    return name.startswith(TOOL_PREFIX)


def start_mcp_servers(
    settings: Settings,
    cwd: Path,
    sandbox: SandboxRunner,
) -> MCPManager:
    """Build and start the configured MCP servers for one workspace."""
    manager = MCPManager.from_settings(settings, cwd, sandbox=sandbox)
    return manager.start()


class ToolRuntime(
    FileToolsMixin,
    SearchToolsMixin,
    ProcessToolsMixin,
    RemoteToolsMixin,
    MediaToolsMixin,
):
    """Execute model tool calls with workspace and sandbox enforcement.

    The methods are grouped into mixins by concern; this class owns the shared
    settings, path resolver, and dispatcher so every tool sees one policy.
    """

    def __init__(
        self,
        settings: Settings,
        cwd: Path,
        shell: ShellSpec,
        *,
        web_client: ProviderWebClient | None = None,
        vision_client: ProviderVisionClient | None = None,
        media_client: Any = None,
        provider_name: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
        read_only: bool = False,
        mcp_manager: MCPManager | None = None,
    ):
        self.settings = settings
        self.cwd = cwd.resolve()
        self.shell_spec = shell
        self.web_client = web_client
        self.web_enabled = bool(web_client and web_client.enabled)
        self.vision_client = vision_client
        self.vision_enabled = bool(vision_client and vision_client.enabled)
        # Duck-typed on purpose: the media client owns OCR, speech, and speech
        # synthesis endpoints, and this runtime only asks which are enabled.
        self.media_client = media_client
        self.provider_name = provider_name
        self.model = model
        self.thinking = thinking

        # Subagent depth is inherited from the child environment so a nested
        # process cannot recurse without bound.
        self.subagent_depth = subagent_depth_from_env()
        self.subagent_max_depth = int(settings.get("subagent.max_depth", 2))
        self.subagent_enabled = (
            bool(settings.get("subagent.enabled", True))
            and self.subagent_depth < self.subagent_max_depth
        )
        self.subagent_timeout = float(settings.get("subagent.timeout_seconds", 900))
        self.subagent_max_turns = int(settings.get("subagent.max_turns", 40))
        self.subagent_max_parallel = max(
            1, int(settings.get("subagent.max_parallel", 4))
        )

        self.allow_outside = bool(settings.get("tools.allow_outside_workspace", False))
        self.max_file_chars = int(settings.get("tools.max_file_chars", 160_000))
        self.max_file_bytes = int(settings.get("tools.max_file_bytes", 5_000_000))
        self.max_results = int(settings.get("tools.max_results", 200))
        self.max_parallel = max(1, int(settings.get("tools.max_parallel", 4)))
        self.shell_timeout = float(settings.get("shell.timeout_seconds", 120))
        self.max_output_chars = int(settings.get("shell.max_output_chars", 60_000))
        self.sandbox = SandboxRunner.from_settings(settings, self.cwd)
        # A runtime created for `/new` or a provider switch adopts the session's
        # existing manager instead of starting a second copy of every server.
        owns_mcp = mcp_manager is None
        if mcp_manager is not None:
            self.mcp = mcp_manager
        else:
            self.mcp = start_mcp_servers(settings, self.cwd, self.sandbox)
        # ``tools.allow`` and ``--read-only`` narrow the same set, so the two
        # compose instead of the flag silently widening what the file allows.
        self.read_only = bool(read_only)
        try:
            self.allowed_tools = self._allowed_tool_names(
                settings, read_only=self.read_only
            )
        except ConfigError:
            # A rejected `tools.allow` must not leave the servers this runtime
            # just started running after its constructor has already failed.
            if owns_mcp:
                self.mcp.close()
            raise
        if self.sandbox.policy.enabled:
            cap = self.sandbox.policy.timeout_seconds
            if self.subagent_timeout > cap:
                # A subagent is one sandboxed command, so the policy cap always
                # wins. Clamp here so the value the tool reports is the value
                # that actually applies, instead of silently killing the child
                # at the sandbox timeout.
                self.subagent_timeout = cap
                self.sandbox.audit.note(
                    "subagent.timeout_seconds capped by sandbox.timeout_seconds "
                    f"({cap:g}s)"
                )

    def _allowed_tool_names(
        self,
        settings: Settings,
        *,
        read_only: bool,
    ) -> frozenset[str]:
        """Resolve ``tools.allow`` and ``--read-only`` into one name set.

        A typo in ``tools.allow`` would otherwise intersect down to an empty
        set, silently leaving an agent that cannot do anything, so unknown
        names are a configuration error rather than a no-op. ``mcp__<server>``
        and ``mcp__<server>__*`` select every tool a server exposes, which is
        the only way to name tools that do not exist until the server starts.
        """
        configured = settings.get("tools.allow")
        mcp_names = self.mcp.tool_names
        available = set(TOOL_NAMES) | set(mcp_names)
        allowed = set(available)
        if configured is not None:
            if not isinstance(configured, list) or not configured:
                raise ConfigError("tools.allow must be a non-empty list of tool names")
            selected: set[str] = set()
            unknown: list[str] = []
            for raw in configured:
                if not isinstance(raw, str) or not raw.strip():
                    unknown.append(str(raw))
                    continue
                name = raw.strip()
                if name in available:
                    selected.add(name)
                    continue
                prefix = self._mcp_server_prefix(name)
                matches = {
                    candidate
                    for candidate in mcp_names
                    if prefix is not None and candidate.startswith(prefix)
                }
                if matches:
                    selected |= matches
                else:
                    unknown.append(name)
            if unknown:
                hint = ""
                if mcp_names:
                    hint = (
                        " (MCP tools discovered: "
                        + ", ".join(sorted(mcp_names)[:8])
                        + ")"
                    )
                elif self.mcp.enabled and self._mcp_failures():
                    hint = f" ({self._mcp_failures()})"
                raise ConfigError(
                    "tools.allow contains unknown tools: " + ", ".join(unknown) + hint
                )
            allowed &= selected
        if read_only:
            allowed = {
                name
                for name in allowed
                if name in READ_ONLY_TOOLS or self.mcp.is_read_only(name)
            }
        if not allowed:
            raise ConfigError(
                "no tools are enabled; widen tools.allow or drop --read-only"
            )
        return frozenset(allowed)

    @staticmethod
    def _mcp_server_prefix(name: str) -> str | None:
        """Return the ``mcp__server__`` prefix named by a server-level entry."""
        if not name.startswith(TOOL_PREFIX):
            return None
        body = name[len(TOOL_PREFIX) :].removesuffix("*").rstrip("_")
        if not body or "__" in body:
            return None
        return f"{TOOL_PREFIX}{body}__"

    def _mcp_failures(self) -> str:
        """Summarize failed MCP servers for a configuration error message."""
        failures = [
            f"{server['name']}: {server['error']}"
            for server in self.mcp.summary().get("servers", [])
            if server.get("error")
        ]
        return "; ".join(failures)

    def _path(self, raw: str, *, operation: str = "read") -> Path:
        """Resolve a tool path through the sandbox's shared policy."""
        return self.sandbox.resolve_path(
            raw,
            cwd=self.cwd,
            operation=operation,
            allow_outside=self.allow_outside,
        )

    def sandbox_summary(self) -> dict[str, Any]:
        """Return the serializable sandbox status used by the CLI."""
        return self.sandbox.summary()

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and run one named tool call.

        Lookup is explicit rather than ``getattr`` so a model cannot reach
        helper methods or private attributes by name.
        """
        handlers = {
            "read": self.read,
            "read_range": self.read_range,
            "write": self.write,
            "append_file": self.append_file,
            "edit": self.edit,
            "shell": self.shell,
            "subagent": self.subagent,
            "grep": self.grep,
            "find": self.find,
            "read_image": self.read_image,
            "web_search": self.web_search,
            "web_fetch": self.web_fetch,
            "ocr": self.ocr,
            "transcribe": self.transcribe,
            "speech": self.speech,
        }
        handler = handlers.get(name)
        if handler is None and not (is_mcp_tool(name) and self.mcp.owns(name)):
            raise ToolError(f"unknown tool: {name}")
        if name not in self.allowed_tools:
            # The model can only ask for what was advertised, but a replayed
            # session or a provider that ignores the schema list must still be
            # stopped here: this is the enforcement point, not the schema.
            read_only = name in READ_ONLY_TOOLS or self.mcp.is_read_only(name)
            reason = (
                "--read-only" if self.read_only and not read_only else "tools.allow"
            )
            raise ToolError(f"tool {name!r} is disabled by {reason}")
        if not isinstance(arguments, dict):
            raise ToolError("tool arguments must be an object")
        if handler is None:
            # MCP tools take a free-form argument object: the server validates
            # its own schema, so only the envelope is checked here.
            return self.mcp.call(name, arguments)
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc
        check_argument_types(name, handler, arguments)
        return handler(**arguments)

    @staticmethod
    def activity(name: str, arguments: dict[str, Any]) -> str:
        """Return a short one-line preview for the terminal activity stream."""
        if name == "shell":
            detail = clip(str(arguments.get("command", "")))
        elif name == "subagent":
            detail = clip(str(arguments.get("task", "")))
        elif name in {
            "read",
            "read_range",
            "write",
            "append_file",
            "edit",
            "read_image",
            "ocr",
            "transcribe",
            "speech",
        }:
            detail = clip(str(arguments.get("path", "")))
        elif name == "grep":
            detail = (
                f"{clip(str(arguments.get('pattern', '')))} in "
                f"{clip(str(arguments.get('path', '.')))}"
            )
        elif name == "find":
            detail = (
                f"{clip(str(arguments.get('pattern', '*')))} in "
                f"{clip(str(arguments.get('path', '.')))}"
            )
        elif name == "web_search":
            detail = clip(str(arguments.get("query", "")))
        elif name == "web_fetch":
            detail = clip(str(arguments.get("url", "")))
        elif is_mcp_tool(name):
            # The activity line already carries the full tool name; the
            # argument names are what a human needs to see move.
            names = [str(key) for key in arguments]
            detail = clip(", ".join(names))
        else:
            detail = ""
        return f"[{name}] {detail}".rstrip()
