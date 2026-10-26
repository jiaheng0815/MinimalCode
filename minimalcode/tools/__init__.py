"""Workspace-bound tools exposed to the agent loop.

The implementation is split by concern:

* :mod:`minimalcode.tools.schemas` - provider-facing tool declarations
* :mod:`minimalcode.tools.files` - read/write/edit
* :mod:`minimalcode.tools.search` - low-level grep process handling
* :mod:`minimalcode.tools.search_tools` - grep/find runtime methods
* :mod:`minimalcode.tools.process` - shell/subagent execution
* :mod:`minimalcode.tools.remote` - provider web and vision clients
* :mod:`minimalcode.tools.validation` - model-argument checks
* :mod:`minimalcode.tools.runtime` - shared state and dispatch

``__all__`` is the complete public surface. Names that lived on the pre-0.7
single ``tools`` module are no longer re-exported; import the helper from the
module that defines it.
"""

from __future__ import annotations

import json
from typing import Any

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


def tool_result_text(result: dict[str, Any]) -> str:
    """Serialize a tool result compactly for the next model message."""
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
