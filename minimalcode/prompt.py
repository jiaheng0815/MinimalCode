from __future__ import annotations

from pathlib import Path

from .config import Settings
from .shell import ShellSpec

DEFAULT_SYSTEM_PROMPT = """\
You are MinimalCode, a terminal coding agent.
Act directly and inspect relevant files before editing. Prefer repository tools over shell commands when they are cheaper. Make the smallest correct change, run focused verification, and never claim a check you did not run.
Keep answers concise: what changed, what was verified, and any blocker or risk. Do not add plans, summaries, or formatting unless useful.

Workspace: {cwd}
Shell: {shell}
"""


def _read_limited(path: Path, limit: int) -> str:
    if limit <= 0:
        return ""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _agent_files(cwd: Path) -> list[Path]:
    parents = list(reversed(cwd.parents))
    paths: list[Path] = []
    for directory in [*parents, cwd]:
        for filename in ("AGENTS.md", "CLAUDE.md"):
            candidate = directory / filename
            if candidate.is_file():
                paths.append(candidate)
    return paths


def build_system_prompt(settings: Settings, cwd: Path, shell: ShellSpec) -> str:
    max_chars = int(settings.get("context.max_instruction_chars", 60_000))
    custom_prompt = _read_limited(settings.home / "SYSTEM.md", max_chars)
    base = custom_prompt.strip() or DEFAULT_SYSTEM_PROMPT.format(
        cwd=cwd,
        shell=shell.label,
    ).strip()

    instructions: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    global_agents = settings.home / "AGENTS.md"
    if global_agents.is_file():
        seen.add(global_agents.resolve())
        text = _read_limited(global_agents, max_chars).strip()
        if text:
            instructions.append((global_agents, text))
            max_chars -= len(text)

    remaining = max_chars
    for path in _agent_files(cwd):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _read_limited(path, remaining).strip()
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
