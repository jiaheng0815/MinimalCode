from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ShellError
from .sandbox import SandboxRunner, truncate_text

__all__ = [
    "ShellSpec",
    "detect_shell",
    "probe_shell",
    "run_shell",
    "select_shell",
    "truncate_text",
]

# A shell that exists on disk is not necessarily a shell that can start: a
# pwsh build without libicu, or one whose .NET runtime cannot reserve memory,
# aborts before reading the command. Detection verifies each candidate once so
# a broken binary cannot make every shell command fail.
_PROBE_TIMEOUT_SECONDS = 10
_PROBE_CACHE: dict[tuple[str, str], str | None] = {}


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
    """Prefer pwsh, then the native fallback for the current platform.

    Candidates are verified with a trivial command before they are accepted, so
    an installed-but-unusable shell (missing ICU, no memory, wrong architecture)
    falls through to the next one instead of failing every command later.
    """
    if preferred:
        shell = _resolve_override(preferred)
        reason = probe_shell(shell)
        if reason is not None:
            raise ShellError(
                f"configured shell failed to start: {shell.executable}: {reason}"
            )
        return shell
    return select_shell(os.name == "nt", shutil.which, probe=probe_shell)


def probe_shell(shell: ShellSpec) -> str | None:
    """Return None when ``shell`` can start, otherwise a short failure reason.

    The result is cached per executable for the lifetime of the process: the
    probe costs one shell startup (~0.2 s for pwsh) and is therefore paid once.
    """
    key = (shell.executable, shell.kind)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    reason = _probe_once(shell)
    _PROBE_CACHE[key] = reason
    return reason


def _probe_once(shell: ShellSpec) -> str | None:
    try:
        completed = subprocess.run(
            prefix_command(shell, "exit 0"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"did not answer within {_PROBE_TIMEOUT_SECONDS:g} seconds"
    except (OSError, ValueError) as exc:
        return str(exc)
    if completed.returncode == 0:
        return None
    detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
    if not detail:
        detail = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
    first_line = detail.splitlines()[0] if detail else ""
    if not first_line:
        return f"exited with code {completed.returncode}"
    return first_line[:200]


def select_shell(
    is_windows: bool,
    finder: Callable[[str], str | None],
    *,
    probe: Callable[[ShellSpec], str | None] | None = None,
) -> ShellSpec:
    """Resolve the shell order without depending on the host platform."""
    candidates = (
        ("pwsh", "powershell", "bash") if is_windows else ("pwsh", "bash", "zsh", "sh")
    )
    unusable: list[str] = []
    for name in candidates:
        executable = finder(name)
        if executable:
            kind, label = _kind_for_executable(executable)
            shell = ShellSpec(
                kind=kind,
                executable=executable,
                label=label,
                is_windows=is_windows,
            )
            if probe is not None:
                reason = probe(shell)
                if reason is not None:
                    unusable.append(f"{name}: {reason}")
                    continue
            return shell
    message = "no supported shell found (tried pwsh, powershell/bash/sh)"
    if unusable:
        message = f"{message}: {'; '.join(unusable)}"
    raise ShellError(message)


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
