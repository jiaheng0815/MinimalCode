from __future__ import annotations

import fnmatch
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from .config import Settings
from .errors import ToolError
from .shell import ShellSpec, run_shell
from .subagent import PREAMBLE as SUBAGENT_PREAMBLE
from .subagent import build_command as build_subagent_command
from .subagent import child_env as subagent_env
from .subagent import depth_from_env as subagent_depth_from_env
from .vision import SUPPORTED_IMAGE_FORMATS, ProviderVisionClient, image_media_type
from .web import ProviderWebClient

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
                if not regex.search(line):
                    continue
                text = line.rstrip("\r\n")
                if len(text) > spec["max_columns"]:
                    text = text[: spec["max_columns"]] + " [... omitted end of long line]"
                matches.append(f"{path}:{number}:{text}")
                if len(matches) >= spec["limit"]:
                    print(json.dumps({"matches": matches, "truncated": True}))
                    raise SystemExit(0)
    except (OSError, UnicodeError):
        continue

print(json.dumps({"matches": matches, "truncated": False}))
"""

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a UTF-8 text file by line range. Output includes line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create a file or overwrite it with complete UTF-8 text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {
                        "type": "string",
                        "description": "Complete file content",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace exact text in a file. Fails if the match is ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to replace",
                    },
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every exact match instead of requiring one",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a command in the configured shell. Use it for tests, builds, git, and commands unavailable as focused tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "timeout": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 3600,
                        "description": (
                            "Timeout in seconds, capped by shell.timeout_seconds"
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory, relative to the workspace",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search text with ripgrep semantics. Use before reading large codebases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression"},
                    "path": {
                        "type": "string",
                        "description": "File or directory, default .",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional glob such as *.py",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2000,
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find",
            "description": "Find files by glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob such as **/*.py or *.toml",
                    },
                    "path": {"type": "string", "description": "Directory, default ."},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5000,
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
]


READ_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": (
            "Describe an image or screenshot with the provider's vision model. "
            "Use it to read text, UI details, or diagrams that plain text tools cannot see."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Image path in the workspace",
                },
                "prompt": {
                    "type": ["string", "null"],
                    "description": (
                        "Question about the image; defaults to a full description "
                        "with verbatim visible text"
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


SUBAGENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "subagent",
        "description": (
            "Delegate a self-contained task to a fresh MinimalCode agent in a child "
            "shell and return its final answer. Use it for code review, broad "
            "analysis, or independent verification whose output would flood this "
            "context. The subagent is workspace-aware and uses the same provider."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Complete instruction for the subagent",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory, relative to the workspace",
                },
                "timeout": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 3600,
                    "description": "Timeout in seconds, capped by subagent.timeout_seconds",
                },
                "max_turns": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10000,
                    "description": "Model/tool rounds allowed in the child, capped by config",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
}


def available_tool_schemas(
    web_client: ProviderWebClient | None = None,
    vision_client: ProviderVisionClient | None = None,
    subagent: bool = False,
) -> list[dict[str, Any]]:
    schemas = list(TOOL_SCHEMAS)
    if subagent:
        schemas.append(SUBAGENT_SCHEMA)
    if vision_client is not None and vision_client.enabled:
        schemas.append(READ_IMAGE_SCHEMA)
    if web_client is None or not web_client.enabled:
        return schemas
    web_schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web through the configured provider.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query, 1-128 characters",
                        },
                        "count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": web_client.max_count,
                            "description": (
                                f"Number of results, default {web_client.default_count}"
                            ),
                        },
                        "need_content": {
                            "type": "boolean",
                            "description": "Include page content when available",
                        },
                        "ori_question": {
                            "type": ["string", "null"],
                            "description": "Original user question",
                        },
                        "localization": {
                            "type": ["string", "null"],
                            "description": "Optional localization IP",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch an HTTP or HTTPS page through the provider.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Absolute HTTP or HTTPS URL",
                        }
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    return [*schemas, *web_schemas]


def _clip(value: str, limit: int = 180) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _positive_limit(
    requested: int | None,
    default: int,
    maximum: int,
    label: str,
) -> int:
    if requested is not None and (
        isinstance(requested, bool) or not isinstance(requested, int)
    ):
        raise ToolError(f"{label} must be an integer")
    # A configured default above the hard cap must not silently exceed it.
    limit = min(default if requested is None else requested, maximum)
    if limit < 1:
        raise ToolError(f"{label} must be at least 1")
    return limit


def _smart_case_ignore(pattern: str) -> bool:
    """Return True when ripgrep's --smart-case would ignore case."""
    literal = re.sub(r"\\[A-Za-z]", "", pattern)
    return not any(char.isupper() for char in literal)


def _run_bounded_command(
    command: list[str],
    *,
    cwd: Path,
    limit: int,
    timeout: float = _SEARCH_TIMEOUT_SECONDS,
) -> tuple[int, list[str], bool, str]:
    """Run a search process while keeping only ``limit`` output lines."""
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
            finished.set()
            timer.cancel()
            if process.stdout is not None:
                process.stdout.close()
        err_file.seek(0)
        stderr = err_file.read(4000).decode("utf-8", errors="replace").strip()
        if timed_out.is_set():
            raise ToolError(f"search timed out after {timeout:g} seconds")
        return process.returncode, lines, truncated, stderr


def _run_python_grep(
    *,
    pattern: str,
    files: list[Path],
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Run the regex fallback in a child process so a bad pattern can time out."""
    spec = {
        "pattern": pattern,
        "ignore_case": _smart_case_ignore(pattern),
        "files": [str(path) for path in files],
        "limit": limit,
        "max_bytes": max_bytes,
        "max_line_chars": min(max_bytes + 1, _MAX_READ_LINE_CHARS),
        "max_columns": _MAX_GREP_LINE_CHARS,
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PYTHON_GREP_SCRIPT],
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
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"search exited {completed.returncode}"
        raise ToolError(detail)
    try:
        parsed = json.loads(completed.stdout)
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


_UNION_ORIGINS = {Union, UnionType}
_TYPE_LABELS: dict[Any, str] = {
    str: "a string",
    bool: "a boolean",
    int: "an integer",
    float: "a number",
    type(None): "null",
}


def _describe_annotation(annotation: Any) -> str:
    if get_origin(annotation) in _UNION_ORIGINS:
        return " or ".join(_describe_annotation(arg) for arg in get_args(annotation))
    return _TYPE_LABELS.get(annotation, "the documented type")


def _matches_annotation(value: Any, annotation: Any) -> bool:
    if get_origin(annotation) in _UNION_ORIGINS:
        return any(_matches_annotation(value, arg) for arg in get_args(annotation))
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is type(None):
        return value is None
    return True


def _check_argument_types(
    name: str,
    handler: Any,
    arguments: dict[str, Any],
) -> None:
    """Reject model-supplied arguments that do not match the handler signature.

    ``bind`` only checks names and arity, so a call such as
    ``{"command": 5}`` would otherwise reach the handler and raise
    ``AttributeError``/``TypeError`` outside the tool-loop error handling.
    """
    try:
        hints = get_type_hints(handler)
    except (NameError, TypeError):  # pragma: no cover - stay permissive
        return
    for key, value in arguments.items():
        annotation = hints.get(key)
        if annotation is None or _matches_annotation(value, annotation):
            continue
        raise ToolError(
            f"invalid arguments for {name}: {key} must be "
            f"{_describe_annotation(annotation)}"
        )


class ToolRuntime:
    def __init__(
        self,
        settings: Settings,
        cwd: Path,
        shell: ShellSpec,
        *,
        web_client: ProviderWebClient | None = None,
        vision_client: ProviderVisionClient | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
    ):
        self.settings = settings
        self.cwd = cwd.resolve()
        self.shell_spec = shell
        self.web_client = web_client
        self.web_enabled = bool(web_client and web_client.enabled)
        self.vision_client = vision_client
        self.vision_enabled = bool(vision_client and vision_client.enabled)
        self.provider_name = provider_name
        self.model = model
        self.thinking = thinking
        self.subagent_depth = subagent_depth_from_env()
        self.subagent_max_depth = int(settings.get("subagent.max_depth", 2))
        self.subagent_enabled = (
            bool(settings.get("subagent.enabled", True))
            and self.subagent_depth < self.subagent_max_depth
        )
        self.subagent_timeout = float(settings.get("subagent.timeout_seconds", 900))
        self.subagent_max_turns = int(settings.get("subagent.max_turns", 40))
        self.allow_outside = bool(settings.get("tools.allow_outside_workspace", False))
        self.max_file_chars = int(settings.get("tools.max_file_chars", 160_000))
        self.max_file_bytes = int(settings.get("tools.max_file_bytes", 5_000_000))
        self.max_results = int(settings.get("tools.max_results", 200))
        self.shell_timeout = float(settings.get("shell.timeout_seconds", 120))
        self.max_output_chars = int(settings.get("shell.max_output_chars", 60_000))

    def _path(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("path must be a non-empty string")
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.cwd / path
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolError(f"invalid path: {raw}") from exc
        if not self.allow_outside:
            try:
                resolved.relative_to(self.cwd)
            except ValueError as exc:
                raise ToolError(f"path is outside workspace: {resolved}") from exc
        return resolved

    def read(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if isinstance(start_line, bool) or not isinstance(start_line, int):
            raise ToolError("start_line must be an integer")
        if end_line is not None and (
            isinstance(end_line, bool) or not isinstance(end_line, int)
        ):
            raise ToolError("end_line must be an integer or null")
        start = max(1, start_line)
        stop = end_line
        if stop is not None and stop < start:
            raise ToolError("end_line must be greater than or equal to start_line")

        with target.open("rb") as binary:
            sample = binary.read(4096)
        if b"\x00" in sample:
            raise ToolError("binary files are not supported by read")

        chunks: list[str] = []
        used = 0
        actual_start = None
        actual_end = 0
        truncated = False
        last_line = 0
        line_limit = min(self.max_file_chars + 1, _MAX_READ_LINE_CHARS)
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            number = 0
            while True:
                line = handle.readline(line_limit)
                if not line:
                    break
                number += 1
                last_line = number
                if number < start:
                    continue
                if stop is not None and number > stop:
                    break
                line_cut = len(line) >= line_limit and not line.endswith(("\n", "\r"))
                if line_cut:
                    truncated = True
                rendered = f"{number:>6}\t{line.rstrip(chr(13) + chr(10))}\n"
                remaining = self.max_file_chars - used
                if len(rendered) > remaining:
                    truncated = True
                    if actual_start is None and remaining > 0:
                        # A first line larger than the budget must not look like an
                        # empty file; return its prefix so the caller sees content.
                        chunks.append(rendered[:remaining])
                        actual_start = number
                        actual_end = number
                    break
                if actual_start is None:
                    actual_start = number
                chunks.append(rendered)
                used += len(rendered)
                actual_end = number
                if line_cut:
                    break

        if actual_start is None:
            if start > max(1, last_line):
                raise ToolError(f"start_line {start} is beyond the end of the file")
            return {
                "status": "success",
                "path": str(target),
                "content": "",
                "start_line": start,
                "end_line": 0,
                "truncated": truncated,
                "eof": True,
            }
        return {
            "status": "success",
            "path": str(target),
            "start_line": actual_start,
            "end_line": actual_end,
            "content": "".join(chunks),
            "truncated": truncated,
        }

    @staticmethod
    def _write_atomic(target: Path, text: str, *, mode: int | None = None) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                try:
                    os.chmod(temp_path, mode)
                except OSError:
                    pass
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)

    def write(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"content is not valid UTF-8 text: {exc}") from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolError(
                f"content is too large for write: {len(encoded)} bytes exceeds "
                f"tools.max_file_bytes ({self.max_file_bytes})"
            )
        target = self._path(path)
        if target.exists() and target.is_dir():
            raise ToolError(f"path is a directory: {target}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(f"cannot create parent directory: {exc}") from exc
        existed = target.exists()
        mode = target.stat().st_mode & 0o7777 if existed else None
        self._write_atomic(target, content, mode=mode)
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(encoded),
            "created": not existed,
        }

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string")
        if not isinstance(replace_all, bool):
            raise ToolError("replace_all must be a boolean")
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if target.stat().st_size > self.max_file_bytes:
            raise ToolError("file is too large for edit")

        with target.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            original = handle.read()
        if "\r\n" in original:
            default_newline = "\r\n"
        elif "\r" in original and "\n" not in original:
            default_newline = "\r"
        else:
            default_newline = "\n"
        needle = old_text.replace("\r\n", "\n").replace("\r", "\n")
        pattern = re.compile(
            r"(?:\r\n|\r|\n)".join(re.escape(part) for part in needle.split("\n"))
        )
        matches = list(pattern.finditer(original))
        count = len(matches)
        if count == 0:
            raise ToolError("old_text was not found")
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_text occurs {count} times; make it unique or set replace_all"
            )
        if not replace_all:
            matches = matches[:1]

        # Replace only the matched spans so untouched lines keep their own endings.
        normalized_new = new_text.replace("\r\n", "\n").replace("\r", "\n")
        updated = original
        for match in reversed(matches):
            span = match.group(0)
            if "\r\n" in span:
                span_newline = "\r\n"
            elif "\r" in span:
                span_newline = "\r"
            elif "\n" in span:
                span_newline = "\n"
            else:
                span_newline = default_newline
            replacement = normalized_new.replace("\n", span_newline)
            updated = updated[: match.start()] + replacement + updated[match.end() :]
        try:
            encoded = updated.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"edited content is not valid UTF-8 text: {exc}") from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolError(
                f"edited file is too large: {len(encoded)} bytes exceeds "
                f"tools.max_file_bytes ({self.max_file_bytes})"
            )
        self._write_atomic(target, updated, mode=target.stat().st_mode & 0o7777)
        return {
            "status": "success",
            "path": str(target),
            "replacements": count if replace_all else 1,
        }

    def shell(
        self, command: str, timeout: float | None = None, cwd: str = "."
    ) -> dict[str, Any]:
        if not isinstance(command, str):
            raise ToolError("command must be a string")
        workdir = self._path(cwd)
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ToolError("timeout must be a number")
        requested_timeout = float(
            timeout if timeout is not None else self.shell_timeout
        )
        if math.isnan(requested_timeout):
            raise ToolError("timeout must be a number")
        effective_timeout = max(1.0, min(requested_timeout, self.shell_timeout))
        return run_shell(
            command,
            shell=self.shell_spec,
            cwd=workdir,
            timeout=effective_timeout,
            max_output_chars=self.max_output_chars,
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
        workdir = self._path(cwd)
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ToolError("timeout must be a number")
        requested_timeout = float(
            timeout if timeout is not None else self.subagent_timeout
        )
        if math.isnan(requested_timeout):
            raise ToolError("timeout must be a number")
        effective_timeout = max(1.0, min(requested_timeout, self.subagent_timeout))
        turns = self.subagent_max_turns if max_turns is None else max_turns
        if isinstance(turns, bool) or not isinstance(turns, int):
            raise ToolError("max_turns must be an integer")
        turns = max(1, min(turns, self.subagent_max_turns))

        command = build_subagent_command(
            self.shell_spec,
            cwd=workdir,
            home=self.settings.home,
            provider=self.provider_name,
            model=self.model,
            thinking=self.thinking,
            max_turns=turns,
        )
        result = run_shell(
            command,
            shell=self.shell_spec,
            cwd=workdir,
            timeout=effective_timeout,
            max_output_chars=self.max_output_chars,
            env=subagent_env(self.subagent_depth + 1),
            stdin_text=SUBAGENT_PREAMBLE + task.strip(),
        )
        return {**result, "command": command}

    def grep(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("pattern must be a non-empty string")
        if glob is not None and not isinstance(glob, str):
            raise ToolError("glob must be a string or null")
        target = self._path(path)
        if not target.exists():
            raise ToolError(f"path not found: {target}")
        if not target.is_file() and not target.is_dir():
            raise ToolError(f"path is not a file or directory: {target}")
        limit = _positive_limit(max_results, self.max_results, 2000, "max_results")
        rg = shutil.which("rg")
        if rg:
            command = [
                rg,
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--hidden",
                "--no-ignore",
                "--smart-case",
                "--max-columns",
                str(_MAX_GREP_LINE_CHARS),
                "--max-columns-preview",
            ]
            if glob:
                command.extend(["--glob", glob])
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            command.extend(["--", pattern, str(target)])
            returncode, lines, truncated, stderr = _run_bounded_command(
                command, cwd=self.cwd, limit=limit
            )
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"grep exited {returncode}")
            return {
                "status": "success",
                "matches": lines[:limit],
                "truncated": truncated,
            }
        return self._python_grep(pattern, target, glob, limit)

    def _ignore_globs(self, target: Path) -> tuple[str, ...]:
        # An explicitly requested ignored path must still be searchable;
        # otherwise `grep(..., path="build")` silently returns nothing.
        try:
            relative = target.relative_to(self.cwd)
        except ValueError:
            relative = target
        parts = relative.parts[:-1] if target.is_file() else relative.parts
        if any(part in IGNORED_DIRECTORIES for part in parts):
            return ()
        return IGNORED_GLOBS

    def _python_grep(
        self,
        pattern: str,
        target: Path,
        glob_pattern: str | None,
        limit: int,
    ) -> dict[str, Any]:
        flags = re.IGNORECASE if _smart_case_ignore(pattern) else 0
        try:
            re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        files = [target] if target.is_file() else list(self._walk_files(target))
        if glob_pattern:
            files = [
                file_path
                for file_path in files
                if self._matches_glob(file_path, target, glob_pattern)
            ]
        if not files:
            return {"status": "success", "matches": [], "truncated": False}
        return _run_python_grep(
            pattern=pattern,
            files=files,
            limit=limit,
            max_bytes=self.max_file_bytes,
        )

    @staticmethod
    def _matches_glob(file_path: Path, root: Path, glob_pattern: str) -> bool:
        """Match a ripgrep-style glob against the file name and relative path."""
        if fnmatch.fnmatch(file_path.name, glob_pattern):
            return True
        try:
            relative = file_path.relative_to(root).as_posix()
        except ValueError:
            relative = file_path.as_posix()
        if fnmatch.fnmatch(relative, glob_pattern):
            return True
        # `**/` is optional in ripgrep globs, required by fnmatch.
        if glob_pattern.startswith("**/"):
            return fnmatch.fnmatch(relative, glob_pattern[3:])
        return False

    def find(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("pattern must be a non-empty string")
        target = self._path(path)
        if not target.is_dir():
            raise ToolError(f"directory not found: {target}")
        limit = _positive_limit(max_results, self.max_results, 5000, "max_results")
        rg = shutil.which("rg")
        if rg:
            command = [
                rg,
                "--files",
                "--hidden",
                "--no-ignore",
                "--glob",
                pattern,
            ]
            for ignored_glob in self._ignore_globs(target):
                command.extend(["--glob", ignored_glob])
            command.append(str(target))
            returncode, paths, truncated, stderr = _run_bounded_command(
                command, cwd=self.cwd, limit=limit
            )
            if not truncated and returncode not in (0, 1):
                raise ToolError(stderr or f"find exited {returncode}")
            return {
                "status": "success",
                "files": paths[:limit],
                "truncated": truncated,
            }

        paths: list[str] = []
        for file_path in self._walk_files(target):
            if self._matches_glob(file_path, target, pattern):
                paths.append(str(file_path))
                if len(paths) >= limit:
                    return {"status": "success", "files": paths, "truncated": True}
        return {"status": "success", "files": paths, "truncated": False}

    def read_image(self, path: str, prompt: str | None = None) -> dict[str, Any]:
        if not self.vision_enabled or self.vision_client is None:
            raise ToolError("read_image is not enabled for the current provider")
        if prompt is not None and not isinstance(prompt, str):
            raise ToolError("prompt must be a string or null")
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ToolError(f"cannot read image: {target}: {exc}") from exc
        if size > self.vision_client.max_image_bytes:
            raise ToolError(
                f"image is too large: {size} bytes exceeds "
                f"vision.max_image_bytes ({self.vision_client.max_image_bytes})"
            )
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"cannot read image: {target}: {exc}") from exc
        media_type = image_media_type(data)
        if media_type is None:
            raise ToolError(
                f"unsupported image format; expected {SUPPORTED_IMAGE_FORMATS}"
            )
        description = self.vision_client.describe(
            media_type, data, question=prompt or None
        )
        return {
            "status": "success",
            "path": str(target),
            "media_type": media_type,
            "bytes": len(data),
            "description": description,
        }

    def web_search(
        self,
        query: str,
        count: int | None = None,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str | None = None,
    ) -> dict[str, Any]:
        if not self.web_enabled or self.web_client is None:
            raise ToolError("web tools are not enabled for the current provider")
        return self.web_client.search(
            query,
            count=count,
            need_content=need_content,
            ori_question=ori_question,
            localization=localization,
        )

    def web_fetch(self, url: str) -> dict[str, Any]:
        if not self.web_enabled or self.web_client is None:
            raise ToolError("web tools are not enabled for the current provider")
        return self.web_client.fetch(url)

    def _walk_files(self, root: Path):
        ignored = set(IGNORED_DIRECTORIES)
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in ignored]
            for filename in filenames:
                yield Path(directory) / filename

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "read": self.read,
            "write": self.write,
            "edit": self.edit,
            "shell": self.shell,
            "subagent": self.subagent,
            "grep": self.grep,
            "find": self.find,
            "read_image": self.read_image,
            "web_search": self.web_search,
            "web_fetch": self.web_fetch,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolError("tool arguments must be an object")
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc
        _check_argument_types(name, handler, arguments)
        return handler(**arguments)

    @staticmethod
    def activity(name: str, arguments: dict[str, Any]) -> str:
        if name == "shell":
            detail = _clip(str(arguments.get("command", "")))
        elif name == "subagent":
            detail = _clip(str(arguments.get("task", "")))
        elif name in {"read", "write", "edit", "read_image"}:
            detail = _clip(str(arguments.get("path", "")))
        elif name == "grep":
            detail = f"{_clip(str(arguments.get('pattern', '')))} in {arguments.get('path', '.')}"
        elif name == "find":
            detail = f"{arguments.get('pattern', '*')} in {arguments.get('path', '.')}"
        elif name == "web_search":
            detail = _clip(str(arguments.get("query", "")))
        elif name == "web_fetch":
            detail = _clip(str(arguments.get("url", "")))
        else:
            detail = ""
        return f"[{name}] {detail}".rstrip()


def tool_result_text(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
