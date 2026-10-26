"""The unified path and command sandbox runner."""

from __future__ import annotations

import math
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import ToolError
from .audit import SandboxAudit
from .backends import (
    CommandRequest,
    LocalSandboxBackend,
    SandboxBackend,
    SandboxUnavailable,
    backend_for,
)
from .execution import _child_env, _execute, _resource_limiter
from .policy import SandboxPolicy
from .sensitive import is_sensitive_path


class SandboxRunner:
    """Single entry point for sandboxed paths and child processes."""

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        backend: SandboxBackend | None = None,
        audit: SandboxAudit | None = None,
    ) -> None:
        self.policy = policy
        self.backend = backend if backend is not None else backend_for(policy.backend)
        self.audit = audit or SandboxAudit()
        self.last_temp_dir: Path | None = None
        self.last_temp_cleaned: bool | None = None

    @classmethod
    def disabled(cls, workspace: str | Path) -> SandboxRunner:
        """Create a runner that keeps file checks but skips command isolation."""
        return cls(
            SandboxPolicy.disabled(workspace),
            backend=LocalSandboxBackend(),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        workspace: str | Path,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> SandboxRunner:
        """Build a runner and policy from configuration."""
        return cls(
            SandboxPolicy.from_settings(settings, workspace, overrides=overrides)
        )

    def resolve_path(
        self,
        raw: str | Path,
        *,
        cwd: Path,
        operation: str = "read",
        allow_outside: bool = False,
    ) -> Path:
        """Resolve a tool path and enforce roots and sensitive-file policy.

        ``sandbox.enabled`` only governs how *commands* are executed. The
        filesystem boundary and the sensitive-file filter stay in force even
        when the execution layer is disabled, so ``--no-sandbox`` cannot
        silently turn every file tool into an unrestricted one.
        """
        if not isinstance(raw, (str, Path)) or (
            isinstance(raw, str) and not raw.strip()
        ):
            raise ToolError("path must be a non-empty string")
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(cwd) / path
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolError(f"invalid path: {raw}") from exc
        if not allow_outside and not self.policy.contains(resolved):
            reason = "path escapes the sandbox allowed roots"
            self.audit.deny(operation, str(resolved), reason)
            raise ToolError(
                f"path is outside workspace: {resolved} (sandbox denied: {reason})"
            )
        if not self.policy.allow_sensitive_files and is_sensitive_path(resolved):
            reason = "sensitive file or directory"
            self.audit.deny(operation, str(resolved), reason)
            raise ToolError(
                f"sensitive path is blocked by sandbox policy: {resolved} "
                "(set sandbox.allow_sensitive_files=true to allow)"
            )
        return resolved

    def is_allowed(
        self,
        path: Path,
        *,
        operation: str = "read",
        allow_outside: bool = False,
    ) -> bool:
        """Non-raising variant used to filter bulk search results."""
        try:
            resolved = Path(path).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False
        if not allow_outside and not self.policy.contains(resolved):
            return False
        return self.policy.allow_sensitive_files or not is_sensitive_path(resolved)

    def summary(self) -> dict[str, Any]:
        """Return the sandbox status payload shown by the CLI."""
        return {
            "enabled": self.policy.enabled,
            "backend": self.backend.name,
            "backend_requested": self.policy.backend,
            "backend_available": self.backend.available(),
            "network_policy": self.policy.network_policy,
            "allowed_hosts": list(self.policy.allowed_hosts),
            "allowed_roots": [str(root) for root in self.policy.allowed_roots],
            "allow_sensitive_files": self.policy.allow_sensitive_files,
            "timeout_seconds": self.policy.timeout_seconds,
            "max_output_chars": self.policy.max_output_chars,
            "max_memory_mb": self.policy.max_memory_mb,
            "max_processes": self.policy.max_processes,
            "total_denials": self.audit.total_denials,
            "recent_denials": [
                denial.as_dict() for denial in self.audit.denials()[-5:]
            ],
            "notes": self.audit.notes()[-5:],
            "last_temp_cleaned": self.last_temp_cleaned,
        }

    def _sandbox_metadata(self, cleaned: bool | None) -> dict[str, Any]:
        """Describe the execution environment attached to command results."""
        return {
            "backend": self.backend.name,
            "network_policy": self.policy.network_policy,
            "temp_dir_cleaned": cleaned,
        }

    def _denied_result(
        self,
        *,
        shell_label: str,
        message: str,
        cleaned: bool | None = None,
    ) -> dict[str, Any]:
        """Shape a refusal exactly like a command result for the caller."""
        return {
            "status": "error",
            "shell": shell_label,
            "message": message,
            "sandbox": self._sandbox_metadata(cleaned),
        }

    def run_argv(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command: str | None = None,
        shell_kind: str = "",
        shell_label: str = "",
        cwd: Path,
        timeout: float,
        max_output_chars: int,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> dict[str, Any]:
        """Execute argv through the configured backend and policy."""
        if not argv:
            return {
                "status": "error",
                "shell": shell_label,
                "message": "command is empty",
            }
        try:
            requested_timeout = float(timeout)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "shell": shell_label,
                "message": "timeout must be a number",
            }
        if math.isnan(requested_timeout):
            return {
                "status": "error",
                "shell": shell_label,
                "message": "timeout must be a number",
            }
        try:
            requested_output = int(max_output_chars)
        except (TypeError, ValueError):
            requested_output = 1
        requested_output = max(1, requested_output)

        # With execution isolation disabled, still run through the same bounded
        # executor so timeouts and output caps behave consistently.
        if not self.policy.enabled:
            return _execute(
                list(argv),
                cwd=cwd,
                timeout=max(1.0, min(requested_timeout, 3600.0)),
                max_output_chars=requested_output,
                env=_child_env(env, None),
                stdin_text=stdin_text,
                shell_label=shell_label,
            )

        effective_timeout = max(
            1.0, min(requested_timeout, self.policy.timeout_seconds)
        )
        # Output policy applies to shell strings; internal argv searches have
        # their own byte/line budgets.
        effective_output = (
            min(requested_output, self.policy.max_output_chars)
            if command is not None
            else requested_output
        )

        if command is not None:
            denial = self.policy.network_denial(command)
            if denial is not None:
                self.audit.deny("shell", command, denial)
                return self._denied_result(
                    shell_label=shell_label,
                    message=f"network access denied by sandbox policy: {denial}",
                )

        if not self.backend.available():
            reason = (
                f"sandbox backend {self.backend.name!r} is unavailable; "
                "refusing to execute (fail-closed)"
            )
            self.audit.deny("shell", command or " ".join(argv[:3]), reason)
            return self._denied_result(shell_label=shell_label, message=reason)

        temp_dir: Path | None = None
        cleaned: bool | None = None
        failure: str | None = None
        try:
            try:
                temp_dir = Path(tempfile.mkdtemp(prefix="minimalcode-sandbox-"))
            except OSError as exc:
                reason = f"cannot create sandbox temporary directory: {exc}"
                self.audit.deny("shell", command or " ".join(argv[:3]), reason)
                return self._denied_result(shell_label=shell_label, message=reason)
            self.last_temp_dir = temp_dir
            request = CommandRequest(
                argv=tuple(argv),
                command=command,
                shell_kind=shell_kind,
                shell_label=shell_label,
                cwd=Path(cwd),
                temp_dir=temp_dir,
                policy=self.policy,
            )
            try:
                prepared = self.backend.wrap(request)
            except SandboxUnavailable as exc:
                failure = str(exc)
                self.audit.deny("shell", command or " ".join(argv[:3]), failure)
                result: dict[str, Any] = {}
            if failure is None:
                result = _execute(
                    prepared,
                    cwd=Path(cwd),
                    timeout=effective_timeout,
                    max_output_chars=effective_output,
                    env=_child_env(env, self.backend.temp_path(request)),
                    stdin_text=stdin_text,
                    shell_label=shell_label,
                    preexec=_resource_limiter(self.policy, effective_timeout),
                )
        finally:
            # Per-command temp directories are always removed, including when
            # backend wrapping or process startup raises.
            cleaned = True
            if temp_dir is not None:
                try:
                    shutil.rmtree(temp_dir)
                except OSError as exc:
                    cleaned = False
                    self.audit.note(
                        f"temporary directory cleanup failed: {temp_dir}: {exc}"
                    )
            self.last_temp_cleaned = cleaned

        if failure is not None:
            return self._denied_result(
                shell_label=shell_label, message=failure, cleaned=cleaned
            )
        if result.get("status") == "timeout":
            self.audit.note(
                f"command timed out after {effective_timeout:g}s; process tree killed"
            )
        if result.get("truncated"):
            self.audit.note(
                f"command output truncated at {effective_output} characters"
            )
        result["sandbox"] = self._sandbox_metadata(cleaned)
        return result
