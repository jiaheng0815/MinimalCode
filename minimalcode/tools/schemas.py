"""Tool schema declarations exposed to the model.

Schemas live apart from execution code so protocol payloads can be reviewed and
tested without importing the filesystem, sandbox, or HTTP layers.
"""

from __future__ import annotations

from typing import Any

from ..vision import ProviderVisionClient
from ..web import ProviderWebClient

# The always-available focused tools. Order is stable because providers and
# prompt caches can key off the serialized schema list.
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

# Optional schemas are appended only when the backing client is enabled, so the
# model never sees a tool that the current configuration would reject.
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


def _web_schemas(web_client: ProviderWebClient) -> list[dict[str, Any]]:
    """Build provider-backed web schemas with the configured result bounds."""
    return [
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


def available_tool_schemas(
    web_client: ProviderWebClient | None = None,
    vision_client: ProviderVisionClient | None = None,
    subagent: bool = False,
) -> list[dict[str, Any]]:
    """Return the schemas enabled by the active clients and depth limit.

    Keeping this decision in one place ensures the advertised tool set and the
    dispatcher's accepted names stay in sync.
    """
    schemas = list(TOOL_SCHEMAS)
    if subagent:
        schemas.append(SUBAGENT_SCHEMA)
    if vision_client is not None and vision_client.enabled:
        schemas.append(READ_IMAGE_SCHEMA)
    if web_client is None or not web_client.enabled:
        return schemas
    return [*schemas, *_web_schemas(web_client)]
