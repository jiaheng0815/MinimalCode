"""Sandbox configuration, validation, and path/network policy checks."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from .network import host_allowed, network_signals

SANDBOX_BACKENDS: tuple[str, ...] = (
    "auto",
    "local",
    "bwrap",
    "firejail",
    "sandbox-exec",
    "docker",
)
NETWORK_POLICIES: tuple[str, ...] = ("deny", "allowlist", "allow")


def _is_within(path: Path, root: Path) -> bool:
    """Case-insensitive containment check that works with resolved paths."""
    child = os.path.normcase(os.path.abspath(str(path)))
    parent = os.path.normcase(os.path.abspath(str(root))).rstrip(os.sep)
    if child == parent:
        return True
    return child.startswith(parent + os.sep)


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    """Validate a configuration value that must be a list of non-empty strings."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{label} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{label} must contain non-empty strings")
        result.append(item.strip())
    return tuple(result)


def _number(value: Any, label: str) -> float:
    """Validate a finite numeric setting, rejecting bool as an int subclass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigError(f"{label} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ConfigError(f"{label} must be a finite number")
    return numeric


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Immutable policy shared by path checks, backends, and the runner."""

    enabled: bool
    backend: str
    allowed_roots: tuple[Path, ...]
    network_policy: str
    allowed_hosts: tuple[str, ...]
    timeout_seconds: float
    max_output_chars: int
    allow_sensitive_files: bool
    max_memory_mb: int
    max_processes: int
    docker_image: str
    workspace: Path

    @classmethod
    def disabled(cls, workspace: str | Path) -> SandboxPolicy:
        """Return a permissive execution policy with file checks still active."""
        root = Path(workspace).resolve(strict=False)
        return cls(
            enabled=False,
            backend="local",
            allowed_roots=(root,),
            network_policy="allow",
            allowed_hosts=(),
            timeout_seconds=3600.0,
            max_output_chars=10_000_000,
            allow_sensitive_files=True,
            max_memory_mb=0,
            max_processes=0,
            docker_image="",
            workspace=root,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        workspace: str | Path,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> SandboxPolicy:
        """Build a policy from settings, treating malformed values as fatal.

        Configuration validation normally catches these cases, but the same
        builder is used by tests and embedders, so it must not silently widen
        the policy when a value is missing or wrong.
        """
        root = Path(workspace).resolve()
        raw = settings.get("sandbox", {}) if hasattr(settings, "get") else {}
        data: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        if overrides:
            data.update(
                {key: value for key, value in overrides.items() if value is not None}
            )

        enabled = data.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError("sandbox.enabled must be a boolean")
        backend = data.get("backend", "local")
        if not isinstance(backend, str) or backend not in SANDBOX_BACKENDS:
            raise ConfigError(
                f"sandbox.backend must be one of {', '.join(SANDBOX_BACKENDS)}"
            )
        network_policy = data.get("network_policy", "deny")
        if network_policy not in NETWORK_POLICIES:
            raise ConfigError(
                f"sandbox.network_policy must be one of {', '.join(NETWORK_POLICIES)}"
            )
        allowed_hosts = _string_tuple(
            data.get("allowed_hosts", ()), "sandbox.allowed_hosts"
        )
        if network_policy == "allowlist" and not allowed_hosts:
            raise ConfigError(
                "sandbox.network_policy is 'allowlist' but sandbox.allowed_hosts is empty"
            )

        # The workspace is always the first root; extras are resolved relative
        # to it when not absolute.
        roots: list[Path] = [root]
        for entry in _string_tuple(
            data.get("allowed_roots", ()), "sandbox.allowed_roots"
        ):
            try:
                candidate = Path(entry).expanduser()
                if not candidate.is_absolute():
                    candidate = root / candidate
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ConfigError(
                    f"invalid sandbox.allowed_roots entry: {entry!r}"
                ) from exc
            if resolved not in roots:
                roots.append(resolved)

        timeout = _number(data.get("timeout_seconds", 120), "sandbox.timeout_seconds")
        if not 1 <= timeout <= 3600:
            raise ConfigError("sandbox.timeout_seconds must be from 1 to 3600")
        max_output = data.get("max_output_chars", 60_000)
        if (
            isinstance(max_output, bool)
            or not isinstance(max_output, int)
            or max_output < 1
        ):
            raise ConfigError("sandbox.max_output_chars must be a positive integer")
        allow_sensitive = data.get("allow_sensitive_files", False)
        if not isinstance(allow_sensitive, bool):
            raise ConfigError("sandbox.allow_sensitive_files must be a boolean")
        max_memory = data.get("max_memory_mb", 0)
        if (
            isinstance(max_memory, bool)
            or not isinstance(max_memory, int)
            or max_memory < 0
        ):
            raise ConfigError("sandbox.max_memory_mb must be a non-negative integer")
        max_processes = data.get("max_processes", 0)
        if (
            isinstance(max_processes, bool)
            or not isinstance(max_processes, int)
            or max_processes < 0
        ):
            raise ConfigError("sandbox.max_processes must be a non-negative integer")
        docker_image = data.get("docker_image", "alpine:3")
        if not isinstance(docker_image, str):
            raise ConfigError("sandbox.docker_image must be a string")

        return cls(
            enabled=enabled,
            backend=backend,
            allowed_roots=tuple(roots),
            network_policy=network_policy,
            allowed_hosts=allowed_hosts,
            timeout_seconds=timeout,
            max_output_chars=max_output,
            allow_sensitive_files=allow_sensitive,
            max_memory_mb=max_memory,
            max_processes=max_processes,
            docker_image=docker_image.strip(),
            workspace=root,
        )

    def contains(self, path: Path) -> bool:
        """Return True when ``path`` is inside any allowed root."""
        return any(_is_within(path, root) for root in self.allowed_roots)

    def write_roots(self, cwd: Path) -> tuple[Path, ...]:
        """Return roots a filesystem-capable backend must mount read-write."""
        roots = list(self.allowed_roots)
        resolved_cwd = Path(cwd).resolve(strict=False)
        if not any(_is_within(resolved_cwd, root) for root in roots):
            # An explicit legacy opt-out still needs the working directory
            # mounted by filesystem-capable backends.
            roots.append(resolved_cwd)
        return tuple(roots)

    def network_denial(self, command: str) -> str | None:
        """Return a denial reason, or None when the command may run."""
        if not self.enabled or self.network_policy == "allow":
            return None
        detected, hosts = network_signals(command)
        if not detected:
            return None
        if self.network_policy == "deny":
            return (
                "network access is disabled by sandbox.network_policy=deny "
                "(rejected command resembles a network client)"
            )
        if not hosts:
            return (
                "sandbox.network_policy=allowlist cannot verify the target host "
                "for this command"
            )
        for host in hosts:
            if not host_allowed(host, self.allowed_hosts):
                return f"host {host!r} is not in sandbox.allowed_hosts"
        return None
