from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ToolError
from .shell import ShellSpec, run_shell
from .web import ProviderWebClient

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
                    "content": {"type": "string", "description": "Complete file content"},
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
                    "old_text": {"type": "string", "description": "Exact text to replace"},
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
                        "description": "Timeout in seconds",
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
                    "path": {"type": "string", "description": "File or directory, default ."},
                    "glob": {"type": "string", "description": "Optional glob such as *.py"},
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

def available_tool_schemas(
    web_client: ProviderWebClient | None = None,
) -> list[dict[str, Any]]:
    if web_client is None or not web_client.enabled:
        return list(TOOL_SCHEMAS)
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
                                "Number of results, default "
                                f"{web_client.default_count}"
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
    return [*TOOL_SCHEMAS, *web_schemas]


def _clip(value: str, limit: int = 180) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


class ToolRuntime:
    def __init__(
        self,
        settings: Settings,
        cwd: Path,
        shell: ShellSpec,
        *,
        web_client: ProviderWebClient | None = None,
    ):
        self.settings = settings
        self.cwd = cwd.resolve()
        self.shell_spec = shell
        self.web_client = web_client
        self.web_enabled = bool(web_client and web_client.enabled)
        self.allow_outside = bool(settings.get("tools.allow_outside_workspace", False))
        self.max_file_chars = int(settings.get("tools.max_file_chars", 160_000))
        self.max_file_bytes = int(settings.get("tools.max_file_bytes", 5_000_000))
        self.max_results = int(settings.get("tools.max_results", 200))
        self.shell_timeout = float(settings.get("shell.timeout_seconds", 120))
        self.max_output_chars = int(settings.get("shell.max_output_chars", 60_000))

    def _path(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("path must be a non-empty string")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.cwd / path
        resolved = path.resolve(strict=False)
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
        start = max(1, int(start_line))
        stop = int(end_line) if end_line is not None else None
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
                if actual_start is None:
                    actual_start = number
                rendered = f"{number:>6}\t{line.rstrip(chr(13) + chr(10))}\n"
                if used + len(rendered) > self.max_file_chars:
                    truncated = True
                    break
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
            }
        return {
            "status": "success",
            "path": str(target),
            "start_line": actual_start,
            "end_line": actual_end,
            "content": "".join(chunks),
            "truncated": truncated,
        }

    def write(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        if len(content) > self.max_file_chars * 8:
            raise ToolError("content is too large for write")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "created": not existed,
        }

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_text:
            raise ToolError("old_text must not be empty")
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string")
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        if target.stat().st_size > self.max_file_bytes:
            raise ToolError("file is too large for edit")

        with target.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            original = handle.read()
        newline = "\r\n" if "\r\n" in original else "\n"
        normalized = original.replace("\r\n", "\n")
        needle = old_text.replace("\r\n", "\n")
        replacement = new_text.replace("\r\n", "\n")
        count = normalized.count(needle)
        if count == 0:
            raise ToolError("old_text was not found")
        if count > 1 and not replace_all:
            raise ToolError(f"old_text occurs {count} times; make it unique or set replace_all")

        updated = normalized.replace(needle, replacement, -1 if replace_all else 1)
        if newline == "\r\n":
            updated = updated.replace("\n", "\r\n")
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
        return {
            "status": "success",
            "path": str(target),
            "replacements": count if replace_all else 1,
        }

    def shell(self, command: str, timeout: float | None = None, cwd: str = ".") -> dict[str, Any]:
        workdir = self._path(cwd)
        if not workdir.is_dir():
            raise ToolError(f"working directory not found: {workdir}")
        effective_timeout = min(
            float(timeout if timeout is not None else self.shell_timeout),
            max(self.shell_timeout, 3600.0),
        )
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
        if not pattern:
            raise ToolError("pattern must not be empty")
        target = self._path(path)
        limit = min(int(max_results or self.max_results), 2000)
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
            if glob_pattern and not fnmatch.fnmatch(file_path.name, glob_pattern):
                continue
            try:
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

    def find(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        if not pattern:
            raise ToolError("pattern must not be empty")
        target = self._path(path)
        if not target.is_dir():
            raise ToolError(f"directory not found: {target}")
        limit = min(int(max_results or self.max_results), 5000)
        rg = shutil.which("rg")
        if rg:
            command = [rg, "--files", "--glob", pattern, str(target)]
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
            try:
                relative = str(file_path.relative_to(target))
            except ValueError:
                relative = str(file_path)
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(file_path.name, pattern):
                paths.append(str(file_path))
                if len(paths) >= limit:
                    return {"status": "success", "files": paths, "truncated": True}
        return {"status": "success", "files": paths, "truncated": False}

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
        ignored = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}
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
            "web_search": self.web_search,
            "web_fetch": self.web_fetch,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolError("tool arguments must be an object")
        try:
            return handler(**arguments)
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc

    @staticmethod
    def activity(name: str, arguments: dict[str, Any]) -> str:
        if name == "shell":
            detail = _clip(str(arguments.get("command", "")))
        elif name in {"read", "write", "edit"}:
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
