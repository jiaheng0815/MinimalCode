"""Tool schema declarations exposed to the model.

Schemas live apart from execution code so protocol payloads can be reviewed and
tested without importing the filesystem, sandbox, or HTTP layers.
"""

from __future__ import annotations

from typing import Any

from ..vision import ProviderVisionClient
from ..web import ProviderWebClient

# Every name the dispatcher accepts. `tools.allow`, `--read-only`, and the
# advertised schemas are all expressed against this set so that a tool cannot
# be offered to the model without the dispatcher accepting it, or the reverse.
TOOL_NAMES = frozenset(
    {
        "read",
        "read_range",
        "write",
        "append_file",
        "edit",
        "shell",
        "grep",
        "find",
        "subagent",
        "read_image",
        "web_search",
        "web_fetch",
        "ocr",
        "transcribe",
        "speech",
    }
)

# Tools that cannot change the workspace or start a process. They are the whole
# of `--read-only`, and the only tools that may run concurrently: a batch of
# reads has no ordering dependency, while `write` followed by `shell` does.
# `ocr` and `transcribe` upload a file but write nothing back, so they keep
# working under `--read-only`; `speech` writes an audio file and does not.
READ_ONLY_TOOLS = frozenset(
    {
        "read",
        "read_range",
        "grep",
        "find",
        "read_image",
        "web_search",
        "web_fetch",
        "ocr",
        "transcribe",
    }
)

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
            "name": "read_range",
            "description": (
                "Read an explicit line range with line numbers. Use it to inspect "
                "the exact region before a line-number edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path", "start_line", "end_line"],
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
            "name": "append_file",
            "description": (
                "Append UTF-8 text at EOF, creating the file when missing. Use it "
                "instead of an edit that guesses the file tail; the existing line "
                "ending style is preserved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {
                        "type": "string",
                        "description": "Text to append at the end of the file",
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
            "description": (
                "Replace a line range with a patch block. Read the target lines "
                "first, then pass start_line/end_line and new_text; use the "
                "exact old_text fallback only when line numbers are unreliable. "
                "end_line = start_line - 1 inserts before start_line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "First line to replace (from a fresh read)",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Last line to replace; start_line - 1 inserts instead"
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement patch block",
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "Exact-text fallback; do not pass with line numbers"
                        ),
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Exact-text fallback only: replace every match",
                    },
                },
                "required": ["path", "new_text"],
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
            "context. Adjacent subagent calls in one turn run concurrently, so "
            "dispatch independent tasks as separate calls. The subagent is "
            "workspace-aware and uses the same provider."
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
                "description": (
                    "Search the web through the configured provider. The query "
                    "may use search operators: site:host, -site:host, -word, "
                    '-"exact phrase", "exact phrase", lang:zh,en, after:YYYY, '
                    "before:YYYY-MM-DD. Duplicate URLs are dropped and results "
                    "are ordered by the provider's relevance score."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Search query, 1-128 characters, with optional "
                                "search operators"
                            ),
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
                            "type": ["boolean", "null"],
                            "description": (
                                "Include page content when available; omit to "
                                "use the provider default "
                                f"({web_client.default_need_content})"
                            ),
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


# Document, audio, and speech schemas. Each one is advertised only when its
# endpoint is configured, so the model never sees a capability the current
# provider would refuse.
OCR_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ocr",
        "description": (
            "Extract text from a PDF, Word, PowerPoint, or image file page by "
            "page with the provider's OCR API. Use it to read a scanned "
            "document or a screenshot-bearing report that plain text tools "
            "cannot decode; the result is returned as text here, not stored."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "purpose": {
                    "type": ["string", "null"],
                    "description": (
                        "'text_recognition' (default) drops document images; "
                        "'rich_layout_recognition' keeps them in place"
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

TRANSCRIBE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "transcribe",
        "description": (
            "Transcribe an audio file into text with the provider's speech "
            "recognition API. The audio must be 16-bit PCM WAV or Ogg Opus, "
            "at most 8 MiB and 120 seconds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file path"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

SPEECH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "speech",
        "description": (
            "Synthesize speech from text and write the audio file into the "
            "workspace. There is no playback here: report the written path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to speak; Markdown is stripped",
                },
                "path": {
                    "type": "string",
                    "description": "Output audio file path (.wav)",
                },
                "voice_id": {
                    "type": ["string", "null"],
                    "description": "Voice: 'stella' (default) or 'tide'",
                },
            },
            "required": ["text", "path"],
            "additionalProperties": False,
        },
    },
}


def _media_schemas(media_client: Any) -> list[dict[str, Any]]:
    """Return the media schemas whose endpoint the provider actually configured."""
    schemas: list[dict[str, Any]] = []
    if getattr(media_client, "ocr_enabled", False):
        schemas.append(OCR_SCHEMA)
    if getattr(media_client, "asr_enabled", False):
        schemas.append(TRANSCRIBE_SCHEMA)
    if getattr(media_client, "tts_enabled", False):
        schemas.append(SPEECH_SCHEMA)
    return schemas


def available_tool_schemas(
    web_client: ProviderWebClient | None = None,
    vision_client: ProviderVisionClient | None = None,
    subagent: bool = False,
    *,
    allowed: frozenset[str] | None = None,
    mcp_schemas: list[dict[str, Any]] | None = None,
    media_client: Any = None,
) -> list[dict[str, Any]]:
    """Return the schemas enabled by the active clients and depth limit.

    Keeping this decision in one place ensures the advertised tool set and the
    dispatcher's accepted names stay in sync. ``allowed`` carries the
    ``tools.allow`` / ``--read-only`` restriction so a disabled tool is never
    advertised: a schema the model can see but not call only wastes a turn.
    ``mcp_schemas`` are the declarations discovered from MCP servers; they are
    filtered by the same allowlist as the built-in tools. ``media_client`` is
    duck-typed because it is only asked which of its capabilities are enabled.
    """
    schemas = list(TOOL_SCHEMAS)
    if subagent:
        schemas.append(SUBAGENT_SCHEMA)
    if vision_client is not None and vision_client.enabled:
        schemas.append(READ_IMAGE_SCHEMA)
    if web_client is not None and web_client.enabled:
        schemas.extend(_web_schemas(web_client))
    if media_client is not None and getattr(media_client, "enabled", False):
        schemas.extend(_media_schemas(media_client))
    if mcp_schemas:
        schemas.extend(mcp_schemas)
    if allowed is None:
        return schemas
    return [schema for schema in schemas if schema["function"]["name"] in allowed]
