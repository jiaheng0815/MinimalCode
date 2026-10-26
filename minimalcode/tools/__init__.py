"""Workspace-bound tools exposed to the agent loop.

The implementation is split by concern:

* :mod:`minimalcode.tools.schemas` - provider-facing tool declarations
* :mod:`minimalcode.tools.files` - read/write/edit
* :mod:`minimalcode.tools.search` - low-level grep process handling
* :mod:`minimalcode.tools.search_tools` - grep/find runtime methods
* :mod:`minimalcode.tools.process` - shell/subagent execution
* :mod:`minimalcode.tools.remote` - provider web and vision clients
* :mod:`minimalcode.tools.runtime` - shared state and dispatch

The names in ``__all__`` are the stable surface. Module-level names from the
pre-split ``tools.py`` are served by a temporary alias table that warns on use;
see ``docs/compatibility.md`` for the replacement paths and removal target.
"""

from __future__ import annotations

import json
from typing import Any

from .._compat import AliasTable, deprecated_attribute
from ..errors import ToolError
from .runtime import ToolRuntime
from .schemas import (
    READ_IMAGE_SCHEMA,
    SUBAGENT_SCHEMA,
    TOOL_SCHEMAS,
    available_tool_schemas,
)

__all__ = [
    "READ_IMAGE_SCHEMA",
    "SUBAGENT_SCHEMA",
    "TOOL_SCHEMAS",
    "ToolError",
    "ToolRuntime",
    "available_tool_schemas",
    "tool_result_text",
]

# Removed in 0.7.0. Each entry names the module that owns the object now, so
# tests and embedders can migrate their patch targets mechanically.
_DEPRECATED_ATTRIBUTES: AliasTable = {
    "IGNORED_DIRECTORIES": ("minimalcode.tools.search", "IGNORED_DIRECTORIES"),
    "IGNORED_GLOBS": ("minimalcode.tools.search", "IGNORED_GLOBS"),
    "Path": ("pathlib", "Path"),
    "_inline_python_grep": ("minimalcode.tools.search", "inline_python_grep"),
    "_parse_python_grep": ("minimalcode.tools.search", "_parse_python_grep"),
    "_run_bounded_command": ("minimalcode.tools.search", "run_bounded_command"),
    "_run_python_grep": ("minimalcode.tools.search", "run_python_grep"),
    "_smart_case_ignore": ("minimalcode.tools.search", "smart_case_ignore"),
    "run_shell": ("minimalcode.shell", "run_shell"),
    "shutil": ("shutil", None),
}


def __getattr__(name: str) -> Any:
    """Serve pre-split names with a deprecation warning."""
    return deprecated_attribute(__name__, name, _DEPRECATED_ATTRIBUTES)


def tool_result_text(result: dict[str, Any]) -> str:
    """Serialize a tool result compactly for the next model message."""
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
