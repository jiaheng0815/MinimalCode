"""Helpers for dispatching a child MinimalCode process.

The design is deliberately blunt: a subagent is one more shell command that
runs ``<python> -m minimalcode -p`` in the workspace. The task text travels on
stdin so no prompt text ever reaches the command line, and the nesting depth
travels through the environment so grandchildren can be disabled.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

from .shell import ShellSpec

DEPTH_ENV = "MINIMALCODE_SUBAGENT_DEPTH"

PREAMBLE = (
    "You are a subagent dispatched by a parent MinimalCode agent. "
    "Work only on the task below and answer with the result itself; "
    "do not ask questions back.\n\n"
)

_POWERSHELL_KINDS = {"pwsh7", "windows_powershell"}


def quote_argument(value: str, shell: ShellSpec) -> str:
    """Quote one command-line argument for the shell that runs the child."""
    text = str(value)
    if shell.kind in _POWERSHELL_KINDS:
        return "'" + text.replace("'", "''") + "'"
    return shlex.quote(text)


def build_command(
    shell: ShellSpec,
    *,
    cwd: Path,
    python: str | None = None,
    home: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    max_turns: int | None = None,
) -> str:
    """Build the ``minimalcode -p`` command line for a child agent."""
    launcher = quote_argument(python or sys.executable, shell)
    if shell.kind in _POWERSHELL_KINDS:
        # A bare quoted path is a string literal, not a command; PowerShell
        # needs the call operator. The trailing exit also keeps the child's
        # real exit code, which pwsh otherwise collapses to 1.
        launcher = f"& {launcher}"
        suffix = "; exit $LASTEXITCODE"
    else:
        suffix = ""
    parts = [
        launcher,
        "-m",
        "minimalcode",
        "-p",
        "--no-session",
        "--quiet",
    ]
    options: list[tuple[str, str | None]] = [
        ("--cwd", str(cwd)),
        ("--home", None if home is None else str(home)),
        ("--provider", provider),
        ("--thinking", thinking),
        ("--max-turns", None if max_turns is None else str(max_turns)),
        ("-m", model),
    ]
    for flag, value in options:
        if value:
            parts.extend([flag, quote_argument(value, shell)])
    return " ".join(parts) + suffix


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def child_env(depth: int, *, python_path: Path | None = None) -> dict[str, str]:
    """Environment overrides so the child runs this checkout and knows its depth."""
    root = str(python_path or package_root())
    existing = os.environ.get("PYTHONPATH", "")
    entries = [entry for entry in existing.split(os.pathsep) if entry]
    if root not in entries:
        entries.insert(0, root)
    return {
        DEPTH_ENV: str(max(0, int(depth))),
        "PYTHONPATH": os.pathsep.join(entries),
    }


def depth_from_env(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get(DEPTH_ENV, "") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0
