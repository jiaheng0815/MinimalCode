"""The ToolRuntime facade: configuration, path policy, and dispatch."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ToolError
from ..sandbox import SandboxRunner
from ..shell import ShellSpec
from ..subagent import depth_from_env as subagent_depth_from_env
from ..vision import ProviderVisionClient
from ..web import ProviderWebClient
from .files import FileToolsMixin
from .process import ProcessToolsMixin
from .remote import RemoteToolsMixin
from .search_tools import SearchToolsMixin
from .validation import check_argument_types, clip


class ToolRuntime(
    FileToolsMixin,
    SearchToolsMixin,
    ProcessToolsMixin,
    RemoteToolsMixin,
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
        provider_name: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
    ):
        self.settings = settings
        self.cwd = cwd.resolve()
        self.shell_spec = shell
        self.web_client = web_client
        self.web_enabled = bool(web_client and web_client.enabled)
        self.vision_client = vision_client
        self.vision_enabled = bool(vision_client and vision_client.enabled)
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

        self.allow_outside = bool(settings.get("tools.allow_outside_workspace", False))
        self.max_file_chars = int(settings.get("tools.max_file_chars", 160_000))
        self.max_file_bytes = int(settings.get("tools.max_file_bytes", 5_000_000))
        self.max_results = int(settings.get("tools.max_results", 200))
        self.shell_timeout = float(settings.get("shell.timeout_seconds", 120))
        self.max_output_chars = int(settings.get("shell.max_output_chars", 60_000))
        self.sandbox = SandboxRunner.from_settings(settings, self.cwd)
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
            "write": self.write,
            "edit": self.edit,
            "shell": self.shell,
            "subagent": self.subagent,
            "grep": self.grep,
            "find": self.find,
            "read_image": self.read_image,
            "web_search": self.web_search,
            "web_fetch": self.web_fetch,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolError("tool arguments must be an object")
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
        elif name in {"read", "write", "edit", "read_image"}:
            detail = clip(str(arguments.get("path", "")))
        elif name == "grep":
            detail = f"{clip(str(arguments.get('pattern', '')))} in {arguments.get('path', '.')}"
        elif name == "find":
            detail = f"{arguments.get('pattern', '*')} in {arguments.get('path', '.')}"
        elif name == "web_search":
            detail = clip(str(arguments.get("query", "")))
        elif name == "web_fetch":
            detail = clip(str(arguments.get("url", "")))
        else:
            detail = ""
        return f"[{name}] {detail}".rstrip()
