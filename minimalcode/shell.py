from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import tempfile
import threading
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


class _Capture:
    """Keep a bounded head and tail of a stream instead of all of it."""

    def __init__(self, max_chars: int) -> None:
        # Four bytes is the widest UTF-8 encoding of one character.
        self.limit_bytes = max(64, max_chars * 4 + 16)
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = False

    def consume(self, chunk: bytes) -> None:
        head_limit = max(1, self.limit_bytes * 3 // 5)
        if len(self.head) < head_limit:
            take = min(head_limit - len(self.head), len(chunk))
            self.head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            self.tail.extend(chunk)
            tail_limit = max(0, self.limit_bytes - len(self.head))
            if len(self.tail) > tail_limit:
                del self.tail[: len(self.tail) - tail_limit]
                self.dropped = True

    def text(self) -> str:
        return _decode_output(bytes(self.head) + bytes(self.tail))


def _pump(stream: IO[bytes], capture: _Capture) -> None:
    try:
        read = getattr(stream, "read1", stream.read)
        while True:
            chunk = read(8192)
            if not chunk:
                break
            capture.consume(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _join_readers(threads: list[threading.Thread], timeout: float) -> None:
    for thread in threads:
        thread.join(timeout=timeout)


def _finish_capture(capture: _Capture, limit: int) -> tuple[str, bool]:
    text, truncated = truncate_text(capture.text(), limit)
    if not capture.dropped:
        return text, truncated
    if not truncated:
        marker = "\n... <truncated> ...\n"
        if limit <= len(marker):
            text = text[:limit]
        else:
            text = text[: limit - len(marker)] + marker
    return text, True


def run_shell(
    command: str,
    *,
    shell: ShellSpec,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
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

    # On POSIX the child leads its own process group so a timeout can signal the
    # tree; Windows walks the parent links with `taskkill /T`.
    tree_options: dict[str, object] = (
        {} if os.name == "nt" else {"start_new_session": True}
    )

    try:
        with tempfile.TemporaryFile() as in_file:
            if stdin_text is None:
                stdin_source: object = subprocess.DEVNULL
            else:
                in_file.write(str(stdin_text).encode("utf-8"))
                in_file.seek(0)
                stdin_source = in_file
            try:
                process = subprocess.Popen(
                    _prefix_command(shell, command),
                    cwd=cwd,
                    env=process_env,
                    stdin=stdin_source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **tree_options,
                )
            except (OSError, ValueError) as exc:
                return {
                    "status": "error",
                    "shell": shell.label,
                    "message": str(exc),
                }

            out_capture = _Capture(max_output_chars)
            err_capture = _Capture(max_output_chars)
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(
                    target=_pump,
                    args=(process.stdout, out_capture),
                    daemon=True,
                ),
                threading.Thread(
                    target=_pump,
                    args=(process.stderr, err_capture),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate_tree(process)
                _join_readers(readers, 2.0)
                stdout, stdout_truncated = _finish_capture(
                    out_capture, max_output_chars
                )
                stderr, stderr_truncated = _finish_capture(
                    err_capture, max_output_chars
                )
                return {
                    "status": "timeout",
                    "shell": shell.label,
                    "timeout_seconds": timeout,
                    "stdout": stdout.rstrip(),
                    "stderr": stderr.rstrip(),
                    "truncated": stdout_truncated or stderr_truncated,
                }
            except BaseException:
                # Ctrl-C must not leave the command's process tree running.
                _terminate_tree(process)
                _join_readers(readers, 2.0)
                raise
            _join_readers(readers, 5.0)
    except (OSError, ValueError) as exc:
        return {
            "status": "error",
            "shell": shell.label,
            "message": str(exc),
        }

    stdout, stdout_truncated = _finish_capture(out_capture, max_output_chars)
    stderr, stderr_truncated = _finish_capture(err_capture, max_output_chars)
    return {
        "status": "success" if returncode == 0 else "error",
        "shell": shell.label,
        "exit_code": returncode,
        "stdout": stdout.rstrip(),
        "stderr": stderr.rstrip(),
        "truncated": stdout_truncated or stderr_truncated,
    }


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill a timed-out command and every process it spawned.

    ``Popen.kill`` signals only the direct child, so a wrapper shell that ran a
    test runner or tool left its grandchildren behind as orphans that kept
    burning CPU long after the agent had given up on the command.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")
