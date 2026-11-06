from __future__ import annotations

import math
import os
import re
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
    "powershell_heredoc_delimiter",
    "probe_shell",
    "run_shell",
    "select_shell",
    "truncate_text",
]

# Bash heredoc starts with ``<<WORD``, ``<<-WORD``, or ``<<\WORD``. A PowerShell
# bit shift such as ``$x << 2`` cannot match because the delimiter is a word.
_HEREDOC_RE = re.compile(
    r"(?<!<)<<-?\s*\\?(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?P=quote)"
)
# A PowerShell here-string is a multi-line literal. Its body may legitimately
# contain ``<<EOF``, so it must be removed before looking for bash heredocs.
_HERE_STRING_RE = re.compile(
    r"(?ms)(?<!\\)@(?P<quote>['\"])[ \t]*\r?\n.*?^[ \t]*(?P=quote)@[ \t]*$"
)

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


def _is_wsl_bash_stub(executable: str) -> bool:
    """Return True for the WSL launcher that lives in System32.

    ``shutil.which("bash")`` finds ``C:\\Windows\\System32\\bash.exe`` on many
    Windows machines even when no Git Bash is installed. Running workspace
    commands through it silently switches to a Linux namespace with different
    paths and interpreters, so it must not be selected as the native shell.
    """
    if os.name != "nt":
        return False
    try:
        parts = [part.lower() for part in Path(executable).resolve().parts]
    except (OSError, RuntimeError, ValueError):
        return False
    if len(parts) < 2 or parts[-1] != "bash.exe":
        return False
    # System32 holds the launcher, and the Store app alias under WindowsApps is
    # a zero-byte reparse point that behaves the same way.
    return (parts[-2] == "system32" and "windows" in parts) or "windowsapps" in parts


def _kind_for_executable(executable: str) -> tuple[str, str]:
    name = Path(executable).stem.lower()
    if name == "pwsh" or name.startswith("pwsh-"):
        return "pwsh7", "PowerShell 7"
    if name == "powershell" or name.startswith("powershell-"):
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
    if Path(resolved).stem.lower() in {"cmd", "command"}:
        # cmd.exe does not accept `-c`, so it would print its banner, exit 0 and
        # never run the command: refuse instead of failing silently.
        raise ShellError(
            f"configured shell is not supported: {resolved}; "
            "use pwsh, powershell, bash, or zsh"
        )
    kind, label = _kind_for_executable(resolved)
    if kind == "bash" and _is_wsl_bash_stub(resolved):
        raise ShellError(
            f"configured shell is the WSL bash launcher, not a native shell: {resolved}"
        )
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
            if kind == "bash" and _is_wsl_bash_stub(executable):
                unusable.append(f"{name}: WSL bash launcher is not a native shell")
                continue
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
    message = f"no supported shell found (tried {'/'.join(candidates)})"
    if unusable:
        message = f"{message}: {'; '.join(unusable)}"
    raise ShellError(message)


def _unquoted_heredoc_marks(
    line: str, quote: str | None = None
) -> tuple[list[int], str | None]:
    """Return ``<<`` offsets outside string literals, and the open quote.

    ``quote`` carries an unterminated literal in from the previous line, because
    a PowerShell string may span lines: scanning line by line reported a
    ``<<WORD`` inside a multi-line string as a bash heredoc and refused the
    command. ``#`` starts a comment and ends the scan of its line.
    """
    positions: list[int] = []
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if char == "`" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "#":
            break
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if (
            line.startswith("<<", index)
            and not line.startswith("<<<", index)
            and (index == 0 or line[index - 1] != "<")
        ):
            positions.append(index)
            index += 2
            continue
        index += 1
    return positions, quote


def powershell_heredoc_delimiter(command: str) -> str | None:
    """Return the bash heredoc delimiter that PowerShell cannot execute.

    ``<<`` inside a quoted string (including a PowerShell here-string) and a
    shift such as ``$x << 2`` are ignored. The caller decides whether the
    active shell is PowerShell; bash/zsh heredocs remain valid there.
    """
    command = _HERE_STRING_RE.sub("", command)
    quote: str | None = None
    for line in command.splitlines():
        positions, quote = _unquoted_heredoc_marks(line, quote)
        for index in positions:
            match = _HEREDOC_RE.match(line, index)
            if match is not None:
                return match.group("delimiter")
    return None


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
    except (TypeError, ValueError, OverflowError):
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
