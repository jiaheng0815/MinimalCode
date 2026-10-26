"""Bounded child-process execution and resource limiting."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any

from .policy import SandboxPolicy


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Keep a bounded head and tail, with a marker naming the dropped amount."""
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
        """Append a chunk, evicting old tail bytes once the budget is full."""
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
        """Decode the retained bytes as UTF-8 with replacement."""
        return _decode_output(bytes(self.head) + bytes(self.tail))


def _pump(stream: IO[bytes], capture: _Capture) -> None:
    """Read a pipe to EOF in a daemon thread and always close it."""
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
    """Wait briefly for pipe readers without blocking process cleanup."""
    for thread in threads:
        thread.join(timeout=timeout)


def _finish_capture(capture: _Capture, limit: int) -> tuple[str, bool]:
    """Apply the character budget and mark dropped middle bytes as truncated."""
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


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill a timed-out command and every process it spawned."""
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
    # The tree kill can race with an already-exiting process; the direct kill
    # is a harmless fallback and ensures the leader is reaped.
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _decode_output(data: bytes | str | None) -> str:
    """Decode captured output defensively."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _child_env(
    user_env: Mapping[str, str] | None,
    temp_path: str | None,
) -> dict[str, str]:
    """Build a deterministic UTF-8 child environment.

    User-provided values are applied last so tools can override locale or
    explicit variables without inheriting a different default stack.
    """
    env = os.environ.copy()
    env.update(
        {
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "PYTHONIOENCODING": "utf-8",
            "LANG": env.get("LANG", "C.UTF-8"),
        }
    )
    if temp_path:
        env["TMPDIR"] = temp_path
        env["TMP"] = temp_path
        env["TEMP"] = temp_path
        env["MINIMALCODE_SANDBOX_TMPDIR"] = temp_path
    if user_env:
        env.update({str(key): str(value) for key, value in user_env.items()})
    return env


def _set_limit(resource: Any, kind: int, value: int) -> None:
    """Set one rlimit without exceeding the inherited hard limit."""
    try:
        _, hard = resource.getrlimit(kind)
        limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(kind, (limit, hard))
    except (OSError, ValueError):
        # Resource limits are best-effort; execution should still proceed.
        pass


def _posix_limits(
    policy: SandboxPolicy, timeout: float, *, linux: bool
) -> list[tuple[str, int]]:
    """Return the ``(rlimit name, value)`` pairs the baseline should apply."""
    limits: list[tuple[str, int]] = [
        ("RLIMIT_CORE", 0),
        ("RLIMIT_CPU", max(1, math.ceil(timeout) + 1)),
    ]
    if linux:
        if policy.max_memory_mb:
            limits.append(("RLIMIT_AS", policy.max_memory_mb * 1024 * 1024))
        if policy.max_processes:
            limits.append(("RLIMIT_NPROC", policy.max_processes))
    return limits


def _resource_limiter(
    policy: SandboxPolicy, timeout: float
) -> Callable[[], None] | None:
    """Return a POSIX pre-exec hook that caps CPU and opt-in memory/processes.

    Address-space (`RLIMIT_AS`) limits are opt-in because they are per-process
    reservations, not resident memory: .NET (pwsh), JVM, and Go children reserve
    several gigabytes of virtual address space and abort with "Out of memory"
    long before they touch that much RAM.
    """
    if os.name == "nt":
        return None
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX always ships resource
        return None

    limits = _posix_limits(policy, timeout, linux=sys.platform.startswith("linux"))

    def apply() -> None:
        for name, value in limits:
            kind = getattr(resource, name, None)
            if kind is not None:
                _set_limit(resource, kind, value)

    return apply


def _execute(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    env: Mapping[str, str],
    stdin_text: str | None,
    shell_label: str,
    preexec: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run one prepared command with bounded captures and tree-wide timeout."""
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
                    argv,
                    cwd=cwd,
                    env=dict(env),
                    stdin=stdin_source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    # Limits must be inherited by exec; there is no portable
                    # alternative in the standard library.
                    preexec_fn=preexec,  # noqa: PLW1509
                    **tree_options,
                )
            except (OSError, ValueError) as exc:
                return {
                    "status": "error",
                    "shell": shell_label,
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
                    "shell": shell_label,
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
            "shell": shell_label,
            "message": str(exc),
        }

    stdout, stdout_truncated = _finish_capture(out_capture, max_output_chars)
    stderr, stderr_truncated = _finish_capture(err_capture, max_output_chars)
    return {
        "status": "success" if returncode == 0 else "error",
        "shell": shell_label,
        "exit_code": returncode,
        "stdout": stdout.rstrip(),
        "stderr": stderr.rstrip(),
        "truncated": stdout_truncated or stderr_truncated,
    }
