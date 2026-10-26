"""Shell and subagent execution tools."""

from __future__ import annotations

import math
from typing import Any

from ..errors import ToolError
from ..shell import run_shell
from ..subagent import PREAMBLE as SUBAGENT_PREAMBLE
from ..subagent import build_command as build_subagent_command
from ..subagent import child_env as subagent_env


class ProcessToolsMixin:
    """Run commands and nested agents inside the active sandbox policy."""

    def shell(
        self, command: str, timeout: float | None = None, cwd: str = "."
    ) -> dict[str, Any]:
        """Run one command in the configured shell and workspace."""
        if not isinstance(command, str):
            raise ToolError("command must be a string")
        workdir = self._path(cwd, operation="execute")
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ToolError("timeout must be a number")
        requested_timeout = float(
            timeout if timeout is not None else self.shell_timeout
        )
        if math.isnan(requested_timeout):
            raise ToolError("timeout must be a number")
        # The configured value is both the default and the hard ceiling.
        effective_timeout = max(1.0, min(requested_timeout, self.shell_timeout))
        return run_shell(
            command,
            shell=self.shell_spec,
            cwd=workdir,
            timeout=effective_timeout,
            max_output_chars=self.max_output_chars,
            sandbox=self.sandbox,
        )

    def subagent(
        self,
        task: str,
        cwd: str = ".",
        timeout: float | None = None,
        max_turns: int | None = None,
    ) -> dict[str, Any]:
        """Run a nested ``minimalcode -p`` process and return its answer."""
        if not self.subagent_enabled:
            raise ToolError(
                "subagent is disabled here: set subagent.enabled or raise "
                f"subagent.max_depth (currently {self.subagent_max_depth})"
            )
        if not isinstance(task, str) or not task.strip():
            raise ToolError("task must be a non-empty string")
        workdir = self._path(cwd, operation="execute")
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ToolError("timeout must be a number")
        requested_timeout = float(
            timeout if timeout is not None else self.subagent_timeout
        )
        if math.isnan(requested_timeout):
            raise ToolError("timeout must be a number")
        limits = [requested_timeout, self.subagent_timeout]
        if self.sandbox.policy.enabled:
            # Only an active sandbox caps the child's runtime.
            limits.append(self.sandbox.policy.timeout_seconds)
        effective_timeout = max(1.0, min(limits))
        turns = self.subagent_max_turns if max_turns is None else max_turns
        if isinstance(turns, bool) or not isinstance(turns, int):
            raise ToolError("max_turns must be an integer")
        turns = max(1, min(turns, self.subagent_max_turns))

        command = build_subagent_command(
            self.shell_spec,
            cwd=workdir,
            home=self.settings.home,
            provider=self.provider_name,
            model=self.model,
            thinking=self.thinking,
            max_turns=turns,
        )
        result = run_shell(
            command,
            shell=self.shell_spec,
            cwd=workdir,
            timeout=effective_timeout,
            max_output_chars=self.max_output_chars,
            env=subagent_env(self.subagent_depth + 1),
            stdin_text=SUBAGENT_PREAMBLE + task.strip(),
            sandbox=self.sandbox,
        )
        return {**result, "command": command, "timeout_seconds": effective_timeout}
