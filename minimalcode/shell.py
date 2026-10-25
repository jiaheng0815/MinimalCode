from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .errors import ShellError


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


def _prefix_command(shell: ShellSpec, command: str) -> list[str]:
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


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return ("", bool(text)) if text else ("", False)
    if len(text) <= limit:
        return text, False
    marker = f"\n... <truncated {len(text) - limit} chars> ...\n"
    if limit <= len(marker):
        return text[:limit], True
    available = limit - len(marker)
    head = int(available * 0.4)
    tail = available - head
    return text[:head] + marker + text[-tail:], True


def run_shell(
    command: str,
    *,
    shell: ShellSpec,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
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
    process_env = os.environ.copy()
    process_env.update(
        {
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "PYTHONIOENCODING": "utf-8",
            "LANG": process_env.get("LANG", "C.UTF-8"),
        }
    )
    if env:
        process_env.update({str(key): str(value) for key, value in env.items()})

    # Output is collected through temporary files instead of pipes. With pipes a
    # timed-out command that left a background child holding the write end made
    # the post-kill drain block forever; file handles let the call return with
    # whatever was written so far.
    try:
        with (
            tempfile.TemporaryFile() as out_file,
            tempfile.TemporaryFile() as err_file,
        ):
            try:
                completed = subprocess.run(
                    _prefix_command(shell, command),
                    cwd=cwd,
                    env=process_env,
                    stdin=subprocess.DEVNULL,
                    stdout=out_file,
                    stderr=err_file,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                stdout = _read_capture(out_file)
                stderr = _read_capture(err_file)
                stdout, stdout_truncated = truncate_text(stdout, max_output_chars)
                stderr, stderr_truncated = truncate_text(stderr, max_output_chars)
                return {
                    "status": "timeout",
                    "shell": shell.label,
                    "timeout_seconds": timeout,
                    "stdout": stdout.rstrip(),
                    "stderr": stderr.rstrip(),
                    "truncated": stdout_truncated or stderr_truncated,
                }
            stdout = _read_capture(out_file)
            stderr = _read_capture(err_file)
    except (OSError, ValueError) as exc:
        return {
            "status": "error",
            "shell": shell.label,
            "message": str(exc),
        }

    stdout, stdout_truncated = truncate_text(stdout, max_output_chars)
    stderr, stderr_truncated = truncate_text(stderr, max_output_chars)
    return {
        "status": "success" if completed.returncode == 0 else "error",
        "shell": shell.label,
        "exit_code": completed.returncode,
        "stdout": stdout.rstrip(),
        "stderr": stderr.rstrip(),
        "truncated": stdout_truncated or stderr_truncated,
    }


def _read_capture(handle: IO[bytes]) -> str:
    handle.seek(0)
    return _decode_output(handle.read())


def _decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")
