from __future__ import annotations

from pathlib import Path

from .config import Settings
from .shell import ShellSpec

DEFAULT_SYSTEM_PROMPT = """\
你是 MinimalCode，一个终端编码代理。
直接动手，但编辑前先查看相关文件。优先使用仓库工具（搜索、读取、编辑）；仅当 shell 更直接或更省成本时使用 shell。只做必要的最小正确修改，并运行与改动直接相关的验证；绝不声称执行过未实际运行的检查。
回复保持简洁，只说明：改了什么、验证了什么、阻塞项或风险。除非必要，不写计划、总结或格式。

工作区：{cwd}
Shell：{shell}
"""


def _read_limited(path: Path, limit: int, *, directory: Path) -> str:
    if limit <= 0:
        return ""
    # Instruction discovery walks parent directories, so an absolute path check
    # against the workspace would reject legitimate repo-level files. Instead,
    # require the resolved file to stay in the directory that declared it; this
    # blocks a symlinked AGENTS.md from leaking an unrelated file into the prompt.
    try:
        resolved = path.resolve(strict=False)
        base = directory.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return ""
    if resolved.parent != base:
        return ""
    try:
        with resolved.open(
            "r", encoding="utf-8-sig", errors="replace", newline=""
        ) as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _agent_files(cwd: Path) -> list[Path]:
    parents = list(reversed(cwd.parents))
    paths: list[Path] = []
    for directory in [*parents, cwd]:
        for filename in ("AGENTS.md", "CLAUDE.md"):
            candidate = directory / filename
            try:
                if candidate.is_file():
                    paths.append(candidate)
            except OSError:
                continue
    return paths


def build_system_prompt(settings: Settings, cwd: Path, shell: ShellSpec) -> str:
    max_chars = int(settings.get("context.max_instruction_chars", 60_000))
    custom_prompt = _read_limited(
        settings.home / "SYSTEM.md", max_chars, directory=settings.home
    )
    base = (
        custom_prompt.strip()
        or DEFAULT_SYSTEM_PROMPT.format(
            cwd=cwd,
            shell=shell.label,
        ).strip()
    )
    remaining = max(0, max_chars - len(base))

    instructions: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    global_agents = settings.home / "AGENTS.md"
    try:
        global_is_file = global_agents.is_file()
    except OSError:
        global_is_file = False
    if global_is_file:
        try:
            seen.add(global_agents.resolve())
        except (OSError, RuntimeError, ValueError):
            pass
        # Reserve at least half the remaining budget for project instructions.
        text = _read_limited(
            global_agents, remaining // 2, directory=settings.home
        ).strip()
        if text:
            instructions.append((global_agents, text))
            remaining -= len(text)

    for path in _agent_files(cwd):
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _read_limited(path, remaining, directory=path.parent).strip()
        if text:
            instructions.append((path, text))
            remaining -= len(text)
        if remaining <= 0:
            break

    if not instructions:
        return base

    blocks = [base, "\n# Repository instructions\n"]
    for path, text in instructions:
        blocks.append(f"\n## {path}\n{text}\n")
    return "".join(blocks)
