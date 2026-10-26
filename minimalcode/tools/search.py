"""Search primitives for the grep and find tools.

The same ignore rules and regex semantics are applied by two engines: ripgrep
when it can be spawned, and a bounded Python fallback otherwise. This module
owns process launch and output parsing so the runtime only has to choose an
engine and shape the result.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import SandboxRunner

# Shared by the Python fallback walk and the ripgrep invocations so both
# implementations search the same set of files.
IGNORED_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
)
IGNORED_GLOBS: tuple[str, ...] = tuple(
    glob for name in IGNORED_DIRECTORIES for glob in (f"!{name}/**", f"!**/{name}/**")
)

# Match ripgrep's --max-columns output width and keep the Python fallback from
# returning a single minified line that swallows the whole context budget.
_MAX_GREP_LINE_CHARS = 300
_MAX_READ_LINE_CHARS = 1_000_000
_SEARCH_TIMEOUT_SECONDS = 30
_SEARCH_MAX_OUTPUT_CHARS = 1_000_000

# The fallback runs in a child interpreter so a pathological regex cannot block
# the agent process. It receives one JSON document on stdin and emits one JSON
# document on stdout; line counting deliberately ignores fragments of a line
# longer than the per-line cap.
_PYTHON_GREP_SCRIPT = r"""
import json
import os
import re
import sys

spec = json.load(sys.stdin)
try:
    regex = re.compile(
        spec["pattern"], re.IGNORECASE if spec["ignore_case"] else 0
    )
except re.error as exc:
    print(json.dumps({"error": f"invalid regular expression: {exc}"}))
    raise SystemExit(0)

matches = []
for path in spec["files"]:
    try:
        if os.path.getsize(path) > spec["max_bytes"]:
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            number = 0
            while True:
                line = handle.readline(spec["max_line_chars"])
                if not line:
                    break
                number += 1
                cut = len(line) >= spec["max_line_chars"] and not line.endswith(
                    ("\n", "\r")
                )
                if cut:
                    # Swallow the rest of this physical line so the next
                    # readline starts a new line and `number` stays aligned
                    # with the file instead of counting each fragment.
                    while True:
                        rest = handle.readline(spec["max_line_chars"])
                        if not rest or rest.endswith(("\n", "\r")):
                            break
                if not regex.search(line):
                    continue
                text = line.rstrip("\r\n")
                if cut or len(text) > spec["max_columns"]:
                    text = text[: spec["max_columns"]] + " [... omitted end of long line]"
                matches.append(f"{path}:{number}:{text}")
                if len(matches) >= spec["limit"]:
                    print(json.dumps({"matches": matches, "truncated": True}))
                    raise SystemExit(0)
    except (OSError, UnicodeError):
        continue

print(json.dumps({"matches": matches, "truncated": False}))
"""


def smart_case_ignore(pattern: str) -> bool:
    """Return True when ripgrep's --smart-case would ignore case."""
    literal = re.sub(r"\\[A-Za-z]", "", pattern)
    return not any(char.isupper() for char in literal)


def drain_physical_line(handle: Any, limit: int) -> None:
    """Consume the rest of a line that was longer than ``limit``."""
    while True:
        rest = handle.readline(limit)
        if not rest or rest.endswith(("\n", "\r")):
            return


def run_bounded_command(
    command: list[str],
    *,
    cwd: Path,
    limit: int,
    timeout: float = _SEARCH_TIMEOUT_SECONDS,
    sandbox: SandboxRunner | None = None,
) -> tuple[int, list[str], bool, str]:
    """Run a search process while keeping only ``limit`` output lines.

    The result is ``(returncode, lines, truncated, stderr)``. Reading stops as
    soon as one extra line proves the result is truncated, then the child is
    killed so a repository-wide search cannot outrun the caller.
    """
    if sandbox is not None:
        result = sandbox.run_argv(
            command,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=_SEARCH_MAX_OUTPUT_CHARS,
        )
        if result.get("status") == "timeout":
            raise ToolError(f"search timed out after {timeout:g} seconds")
        if result.get("status") == "error" and result.get("exit_code") is None:
            detail = str(
                result.get("message") or result.get("stderr") or "search failed"
            )
            raise ToolError(detail)
        lines = str(result.get("stdout") or "").splitlines()
        truncated = bool(result.get("truncated")) or len(lines) > limit
        stderr = str(result.get("stderr") or "").strip()
        return int(result.get("exit_code", 0) or 0), lines[:limit], truncated, stderr

    timed_out = threading.Event()
    finished = threading.Event()
    process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryFile() as err_file:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err_file,
            )
        except (OSError, ValueError) as exc:
            raise ToolError(f"search failed: {exc}") from exc

        def kill() -> None:
            """Timer callback: kill once, and only while the child is running."""
            if finished.is_set() or process.poll() is not None:
                return
            timed_out.set()
            try:
                process.kill()
            except OSError:
                pass

        timer = threading.Timer(timeout, kill)
        timer.daemon = True
        timer.start()
        lines: list[str] = []
        truncated = False
        try:
            assert process.stdout is not None
            # Read one line past the limit: its presence distinguishes an exact
            # fit from a truncated result.
            while len(lines) <= limit:
                raw = process.stdout.readline()
                if not raw:
                    break
                lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            if len(lines) > limit:
                truncated = True
                lines = lines[:limit]
                finished.set()
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait(timeout=5)
            finished.set()
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            timed_out.set()
        finally:
            # Always stop the timer and close the pipe, including on errors.
            finished.set()
            timer.cancel()
            if process.stdout is not None:
                process.stdout.close()
        err_file.seek(0)
        stderr = err_file.read(4000).decode("utf-8", errors="replace").strip()
        if timed_out.is_set():
            raise ToolError(f"search timed out after {timeout:g} seconds")
        return process.returncode, lines, truncated, stderr


def _parse_python_grep(stdout: str, limit: int) -> dict[str, Any]:
    """Validate the fallback child's JSON and cap its match list."""
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError("search returned an invalid result") from exc
    if not isinstance(parsed, dict):
        raise ToolError("search returned an invalid result")
    if parsed.get("error"):
        raise ToolError(str(parsed["error"]))
    matches = parsed.get("matches")
    if not isinstance(matches, list):
        raise ToolError("search returned an invalid result")
    return {
        "status": "success",
        "matches": [str(item) for item in matches[:limit]],
        "truncated": bool(parsed.get("truncated")),
    }


def run_python_grep(
    *,
    pattern: str,
    files: list[Path],
    limit: int,
    max_bytes: int,
    sandbox: SandboxRunner | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run the regex fallback in a child process so a bad pattern can time out."""
    spec = {
        "pattern": pattern,
        "ignore_case": smart_case_ignore(pattern),
        "files": [str(path) for path in files],
        "limit": limit,
        "max_bytes": max_bytes,
        "max_line_chars": min(max_bytes + 1, _MAX_READ_LINE_CHARS),
        "max_columns": _MAX_GREP_LINE_CHARS,
    }
    script = [sys.executable, "-c", _PYTHON_GREP_SCRIPT]
    if sandbox is not None:
        result = sandbox.run_argv(
            script,
            cwd=cwd or Path.cwd(),
            timeout=_SEARCH_TIMEOUT_SECONDS,
            max_output_chars=_SEARCH_MAX_OUTPUT_CHARS,
            stdin_text=json.dumps(spec),
        )
        if result.get("status") == "timeout":
            raise ToolError("search timed out")
        if result.get("status") == "error" and result.get("exit_code") is None:
            detail = str(
                result.get("message") or result.get("stderr") or "search failed"
            )
            raise ToolError(detail)
        returncode = int(result.get("exit_code", 0) or 0)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "").strip()
    else:
        try:
            completed = subprocess.run(
                script,
                input=json.dumps(spec),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_SEARCH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("search timed out") from exc
        except OSError as exc:
            raise ToolError(f"search failed: {exc}") from exc
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr.strip()
    if returncode != 0:
        detail = stderr or f"search exited {returncode}"
        raise ToolError(detail)
    return _parse_python_grep(stdout, limit)


def inline_python_grep(
    *,
    pattern: str,
    files: list[Path],
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Pure-Python fallback used when the backend cannot run argv children."""
    flags = re.IGNORECASE if smart_case_ignore(pattern) else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"invalid regular expression: {exc}") from exc
    matches: list[str] = []
    for path in files:
        try:
            if path.stat().st_size > max_bytes:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    if not regex.search(line):
                        continue
                    text = line.rstrip("\r\n")
                    if len(text) > _MAX_GREP_LINE_CHARS:
                        text = (
                            text[:_MAX_GREP_LINE_CHARS]
                            + " [... omitted end of long line]"
                        )
                    matches.append(f"{path}:{number}:{text}")
                    if len(matches) >= limit:
                        return {
                            "status": "success",
                            "matches": matches[:limit],
                            "truncated": True,
                        }
        except (OSError, UnicodeError):
            # An unreadable file must not abort a repository-wide search.
            continue
    return {"status": "success", "matches": matches, "truncated": False}
