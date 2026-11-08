"""Shell and subagent execution tools."""

from __future__ import annotations

import math
from typing import Any

from ..errors import ToolError
from ..provider.anthropic_env import ANTHROPIC_ENV_PROVIDER
from ..shell import MAX_TIMEOUT_SECONDS, powershell_heredoc_delimiter, run_shell
from ..subagent import PREAMBLE as SUBAGENT_PREAMBLE
from ..subagent import build_command as build_subagent_command
from ..subagent import child_env as subagent_env

_POWERSHELL_KINDS = frozenset({"pwsh7", "windows_powershell"})


def _clamp_timeout(value: float) -> float:
    """Apply the ceiling `run_shell` enforces, so the reported value is real."""
    return max(1.0, min(value, MAX_TIMEOUT_SECONDS))


class ProcessToolsMixin:
    """Run commands and nested agents inside the active sandbox policy."""

    def _subagent_sandbox_options(self) -> tuple[list[str], list[tuple[str, str]]]:
        """Mirror the current in-memory sandbox policy onto the child command.

        CLI flags are applied to ``settings.data`` in memory only; without this
        the child reloads ``settings.json`` and silently drops ``--sandbox``,
        ``--sandbox-network``, and the other per-run overrides.
        """
        policy = self.sandbox.policy
        if not policy.enabled:
            return ["--no-sandbox"], []
        flags = ["--sandbox"]
        options: list[tuple[str, str]] = [
            ("--sandbox-backend", policy.backend),
            ("--sandbox-network", policy.network_policy),
            ("--sandbox-timeout", f"{policy.timeout_seconds:g}"),
            ("--sandbox-max-output", str(policy.max_output_chars)),
        ]
        for root in policy.allowed_roots:
            options.append(("--sandbox-root", str(root)))
        for host in policy.allowed_hosts:
            options.append(("--sandbox-allow-host", host))
        flags.append(
            "--sandbox-allow-sensitive"
            if policy.allow_sensitive_files
            else "--no-sandbox-allow-sensitive"
        )
        return flags, options

    def shell(
        self, command: str, timeout: float | None = None, cwd: str = "."
    ) -> dict[str, Any]:
        """Run one command in the configured shell and workspace."""
        if not isinstance(command, str):
            raise ToolError("command must be a string")
        if self.shell_spec.kind in _POWERSHELL_KINDS:
            delimiter = powershell_heredoc_delimiter(command)
            if delimiter is not None:
                raise ToolError(
                    f"{self.shell_spec.label} does not support bash heredocs "
                    f"(<<{delimiter}). Write the payload with write/append_file "
                    "and run the file, use a PowerShell here-string (@'...'@), "
                    "or pass stdin; the same command will fail again."
                )
        workdir = self._path(cwd, operation="execute")
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ToolError("timeout must be a number")
        try:
            requested_timeout = float(
                timeout if timeout is not None else self.shell_timeout
            )
        except (OverflowError, ValueError) as exc:
            raise ToolError("timeout must be a number") from exc
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
        try:
            requested_timeout = float(
                timeout if timeout is not None else self.subagent_timeout
            )
        except (OverflowError, ValueError) as exc:
            raise ToolError("timeout must be a number") from exc
        if math.isnan(requested_timeout):
            raise ToolError("timeout must be a number")
        limits = [requested_timeout, self.subagent_timeout]
        if self.sandbox.policy.enabled:
            # Only an active sandbox caps the child's runtime.
            limits.append(self.sandbox.policy.timeout_seconds)
        effective_timeout = _clamp_timeout(min(limits))
        turns = self.subagent_max_turns if max_turns is None else max_turns
        if isinstance(turns, bool) or not isinstance(turns, int):
            raise ToolError("max_turns must be an integer")
        turns = max(1, min(turns, self.subagent_max_turns))

        sandbox_flags, sandbox_options = self._subagent_sandbox_options()
        extra_flags = list(sandbox_flags)
        provider = self.provider_name
        if provider == ANTHROPIC_ENV_PROVIDER:
            # The env-derived provider exists only in this process; the child
            # must rebuild it from the inherited ANTHROPIC_* environment.
            extra_flags.append("--anthropic-env")
            provider = None
        command = build_subagent_command(
            self.shell_spec,
            cwd=workdir,
            home=self.settings.home,
            provider=provider,
            model=self.model,
            thinking=self.thinking,
            max_turns=turns,
            extra_flags=extra_flags,
            extra_options=sandbox_options,
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
