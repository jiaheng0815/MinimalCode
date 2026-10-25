from __future__ import annotations

import fnmatch
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from .config import Settings
from .errors import ToolError
from .shell import ShellSpec, run_shell
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


def available_tool_schemas(
    web_client: ProviderWebClient | None = None,
    vision_client: ProviderVisionClient | None = None,
) -> list[dict[str, Any]]:
    schemas = list(TOOL_SCHEMAS)
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
    ):
        self.settings = settings
        self.cwd = cwd.resolve()
        self.shell_spec = shell
        self.web_client = web_client
        self.web_enabled = bool(web_client and web_client.enabled)
        self.vision_client = vision_client
        self.vision_enabled = bool(vision_client and vision_client.enabled)
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
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for number, line in enumerate(handle, 1):
                if number < start:
                    continue
                if stop is not None and number > stop:
                    break
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

        if actual_start is None:
            return {
                "status": "success",
                "path": str(target),
                "content": "",
                "start_line": start,
                "end_line": start,
                "truncated": truncated,
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
                "--smart-case",
                "--max-columns",
                "300",
                "--max-columns-preview",
            ]
            if glob:
                command.extend(["--glob", glob])
            for ignored_glob in IGNORED_GLOBS:
                command.extend(["--glob", ignored_glob])
            command.extend(["--", pattern, str(target)])
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ToolError(f"grep failed: {exc}") from exc
            stdout = completed.stdout.decode("utf-8", errors="replace")
            stderr = completed.stderr.decode("utf-8", errors="replace")
            lines = stdout.splitlines()
            if completed.returncode not in (0, 1):
                raise ToolError(stderr.strip() or f"grep exited {completed.returncode}")
            return {
                "status": "success",
                "matches": lines[:limit],
                "truncated": len(lines) > limit,
            }
        return self._python_grep(pattern, target, glob, limit)

    def _python_grep(
        self,
        pattern: str,
        target: Path,
        glob_pattern: str | None,
        limit: int,
    ) -> dict[str, Any]:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        files = [target] if target.is_file() else self._walk_files(target)
        matches: list[str] = []
        for file_path in files:
            if glob_pattern and not self._matches_glob(file_path, target, glob_pattern):
                continue
            try:
                if file_path.stat().st_size > self.max_file_bytes:
                    continue
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if regex.search(line):
                            matches.append(f"{file_path}:{number}:{line.rstrip()}")
                            if len(matches) >= limit:
                                return {
                                    "status": "success",
                                    "matches": matches,
                                    "truncated": True,
                                }
            except (OSError, UnicodeError):
                continue
        return {"status": "success", "matches": matches, "truncated": False}

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
            command = [rg, "--files", "--glob", pattern]
            for ignored_glob in IGNORED_GLOBS:
                command.extend(["--glob", ignored_glob])
            command.append(str(target))
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ToolError(f"find failed: {exc}") from exc
            if completed.returncode not in (0, 1):
                stderr = completed.stderr.decode("utf-8", errors="replace")
                raise ToolError(stderr.strip() or f"find exited {completed.returncode}")
            paths = completed.stdout.decode("utf-8", errors="replace").splitlines()
            return {
                "status": "success",
                "files": paths[:limit],
                "truncated": len(paths) > limit,
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
