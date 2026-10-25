from __future__ import annotations

import math
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ShellError
from .sandbox import SandboxRunner, truncate_text

__all__ = [
    "ShellSpec",
    "detect_shell",
    "run_shell",
    "select_shell",
    "truncate_text",
]


@dataclass(frozen=True, slots=True)
class ShellSpec:
    kind: str
    executable: str
    label: str
    is_windows: bool


def _kind_for_executable(executable: str) -> tuple[str, str]:
    name = Path(executable).stem.lower()
    if name == "pwsh":
        return "pwsh7", "PowerShell 7"
    if name == "powershell":
        return "windows_powershell", "Windows PowerShell 5.1"
    if name == "bash":
        return "bash", "bash"
    if name == "zsh":
        return "zsh", "zsh"
    return "sh", "sh"


def _resolve_override(preferred: str) -> ShellSpec:
    try:
        expanded = str(Path(preferred).expanduser())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ShellError(f"invalid configured shell path: {preferred}") from exc
    resolved = shutil.which(expanded)
    if resolved is None:
        candidate = Path(expanded)
        if candidate.is_file():
            resolved = str(candidate)
    if resolved is None:
        raise ShellError(f"configured shell not found: {preferred}")
    kind, label = _kind_for_executable(resolved)
    return ShellSpec(
        kind=kind,
        executable=resolved,
        label=label,
        is_windows=os.name == "nt",
    )


def detect_shell(preferred: str | None = None) -> ShellSpec:
    """Prefer pwsh, then the native fallback for the current platform."""
    if preferred:
        return _resolve_override(preferred)
    return select_shell(os.name == "nt", shutil.which)


def select_shell(
    is_windows: bool,
    finder: Callable[[str], str | None],
) -> ShellSpec:
    """Resolve the shell order without depending on the host platform."""
    candidates = ("pwsh", "powershell") if is_windows else ("pwsh", "bash", "sh")
    for name in candidates:
        executable = finder(name)
        if executable:
            kind, label = _kind_for_executable(executable)
            return ShellSpec(
                kind=kind,
                executable=executable,
                label=label,
                is_windows=is_windows,
            )
    raise ShellError("no supported shell found (tried pwsh, powershell/bash/sh)")


def prefix_command(shell: ShellSpec, command: str) -> list[str]:
    """Build the shell invocation for ``command``."""
    if shell.kind in {"pwsh7", "windows_powershell"}:
        bootstrap = (
            "$ProgressPreference='SilentlyContinue';"
            "$ErrorActionPreference='Stop';"
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
            "$OutputEncoding=[Console]::OutputEncoding;"
        )
        return [
            shell.executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            bootstrap + command,
        ]
    if shell.kind == "bash":
        return [shell.executable, "--noprofile", "--norc", "-c", command]
    if shell.kind == "zsh":
        return [shell.executable, "-f", "-c", command]
    return [shell.executable, "-c", command]


# Kept for callers that imported the private name before the sandbox layer.
_prefix_command = prefix_command


def run_shell(
    command: str,
    *,
    shell: ShellSpec,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    sandbox: SandboxRunner | None = None,
) -> dict[str, object]:
    """Run ``command`` through the unified sandbox execution entry point."""
    if not isinstance(command, str):
        return {"status": "error", "message": "command must be a string"}
    if not command.strip():
        return {"status": "error", "message": "command is empty"}
    try:
        requested_timeout = float(timeout)
    except (TypeError, ValueError):
        return {"status": "error", "message": "timeout must be a number"}
    if math.isnan(requested_timeout):
        return {"status": "error", "message": "timeout must be a number"}
    timeout = max(1.0, min(requested_timeout, 3600.0))

    runner = sandbox if sandbox is not None else SandboxRunner.disabled(cwd)
    return runner.run_argv(
        prefix_command(shell, command),
        command=command,
        shell_kind=shell.kind,
        shell_label=shell.label,
        cwd=cwd,
        timeout=timeout,
        max_output_chars=max_output_chars,
        env=env,
        stdin_text=stdin_text,
    )
