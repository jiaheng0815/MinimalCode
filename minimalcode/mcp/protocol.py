"""JSON-RPC 2.0 framing and payload conversion for MCP servers.

The client speaks the Model Context Protocol wire format directly instead of
adding an SDK dependency: a message is a JSON-RPC object, newline delimited on
stdio and carried in HTTP bodies by the remote transports. Keeping the encoding
in one module means the transports, the client, and the tool adapter cannot
disagree about what a message, a tool schema, or a tool result looks like.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

JSONRPC_VERSION = "2.0"

# Newest revision this client implements. The server picks the revision it will
# use; every published revision so far only added optional fields, so an
# unknown-but-newer answer is accepted with a note instead of disconnecting.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})

# OpenAI-compatible function names are limited to 64 characters of
# ``[A-Za-z0-9_-]``. ``mcp__<server>__<tool>`` keeps the origin visible in a
# transcript and matches the convention other MCP clients already use.
TOOL_PREFIX = "mcp__"
MAX_TOOL_NAME = 64

_TOOL_SPECIAL = re.compile(r"[^A-Za-z0-9_-]+")
_OMITTED_TEMPLATE = "\n... {} characters omitted ...\n"
# One server can describe hundreds of tools; a single runaway description must
# not crowd the prompt that carries every other declaration.
_MAX_DESCRIPTION_CHARS = 1000


class MCPError(Exception):
    """A transport, protocol, or JSON-RPC failure from one MCP server."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class MCPTimeout(MCPError):
    """A request that did not produce a response inside its deadline."""


def request_message(
    request_id: int,
    method: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = dict(params)
    return message


def notification_message(
    method: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        message["params"] = dict(params)
    return message


def result_message(request_id: Any, result: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": dict(result or {}),
    }


def error_message(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def encode_message(message: Mapping[str, Any]) -> str:
    """Serialize one message as a single line, as stdio framing requires."""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    # An embedded newline would be read as a second, truncated message by the
    # peer. Escaping it keeps the payload intact and the framing valid.
    return text.replace("\r", "\\r").replace("\n", "\\n")


def decode_message(text: str) -> dict[str, Any]:
    """Parse one JSON-RPC message, rejecting shapes the protocol disallows."""
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPError(f"invalid JSON from server: {exc}") from exc
    if not isinstance(message, dict):
        raise MCPError("JSON-RPC message must be an object")
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise MCPError("JSON-RPC message is missing jsonrpc 2.0")
    if "method" in message:
        if not isinstance(message["method"], str) or not message["method"]:
            raise MCPError("JSON-RPC method must be a non-empty string")
        return message
    if "id" not in message:
        raise MCPError("JSON-RPC response is missing an id")
    if "error" not in message and "result" not in message:
        raise MCPError("JSON-RPC response has neither result nor error")
    return message


def error_from(message: Mapping[str, Any]) -> MCPError:
    """Convert a JSON-RPC error response into an exception."""
    error = message.get("error")
    if not isinstance(error, dict):
        return MCPError("server returned a malformed error object")
    code = error.get("code")
    message_text = error.get("message")
    if not isinstance(message_text, str) or not message_text:
        message_text = "server returned an error"
    detail = f"{message_text} (code {code})" if isinstance(code, int) else message_text
    data = error.get("data")
    if data is not None:
        try:
            rendered = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(data)
        if len(rendered) <= 400:
            detail += f": {rendered}"
    return MCPError(
        detail,
        code=code if isinstance(code, int) else None,
        data=data,
    )


def sanitize_tool_name(server: str, tool: str) -> str:
    """Map a server/tool pair onto a provider-safe function name.

    A name that does not fit the 64-character budget keeps a short hash of the
    original, so two tools that only differ after the cut cannot collide.
    """
    cleaned = _TOOL_SPECIAL.sub("_", tool).strip("_") or "tool"
    prefix = f"{TOOL_PREFIX}{server}__"
    room = MAX_TOOL_NAME - len(prefix)
    if len(cleaned) > room:
        digest = hashlib.sha1(tool.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[: max(1, room - 9)]}_{digest}"
    return f"{prefix}{cleaned}"


def tool_schema(server: str, tool: Mapping[str, Any]) -> dict[str, Any] | None:
    """Convert one ``tools/list`` entry into an OpenAI function declaration."""
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = tool.get("description") or tool.get("title")
    if not isinstance(description, str) or not description.strip():
        description = f"Run the {name} tool on the {server} MCP server."
    return {
        "type": "function",
        "function": {
            "name": sanitize_tool_name(server, name),
            "description": _shorten_description(description),
            "parameters": normalize_input_schema(tool.get("inputSchema")),
        },
    }


def _shorten_description(description: str) -> str:
    """Keep one tool description from spending the context window by itself."""
    text = " ".join(description.split())
    if len(text) <= _MAX_DESCRIPTION_CHARS:
        return text
    return text[: _MAX_DESCRIPTION_CHARS - 3] + "..."


def normalize_input_schema(schema: Any) -> dict[str, Any]:
    """Return an object-typed JSON Schema the provider will accept.

    ``inputSchema`` is optional in the protocol, and a server that omits it or
    sends a non-object schema is treated as taking no arguments rather than
    forwarding a declaration the provider would reject in the whole request.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    result = dict(schema)
    result.pop("$schema", None)
    if result.get("type") not in (None, "object"):
        return {"type": "object", "properties": {}}
    result["type"] = "object"
    if not isinstance(result.get("properties"), dict):
        result["properties"] = {}
    return result


def tool_read_only(tool: Mapping[str, Any]) -> bool:
    """Return the server's read-only hint, defaulting to a mutating tool."""
    annotations = tool.get("annotations")
    if isinstance(annotations, dict):
        hint = annotations.get("readOnlyHint")
        if isinstance(hint, bool):
            return hint
    return False


def clip_text(text: str, limit: int) -> tuple[str, bool]:
    """Bound one tool result by head and tail, as the file tools do."""
    if limit <= 0 or len(text) <= limit:
        return text, False
    head = max(1, limit * 3 // 4)
    tail = max(0, limit - head)
    omitted = len(text) - head - tail
    clipped = text[:head] + _OMITTED_TEMPLATE.format(omitted) + text[-tail:]
    return clipped, True


def content_text(result: Mapping[str, Any], *, limit: int) -> tuple[str, bool]:
    """Render ``tools/call`` content as text for the next model message.

    Image and audio blocks are described, not inlined: base64 payloads would
    spend the context window on data the model cannot decode. A result that
    carries only ``structuredContent`` is rendered as JSON so the model still
    sees the answer.
    """
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            parts.extend(_content_item_parts(item))
    if not parts:
        structured = result.get("structuredContent")
        if isinstance(structured, (dict, list)):
            try:
                parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                pass
    if not parts:
        return "", False
    return clip_text("\n\n".join(parts), limit)


def _content_item_parts(item: Mapping[str, Any]) -> list[str]:
    kind = item.get("type")
    if kind == "text":
        text = item.get("text")
        return [text] if isinstance(text, str) and text else []
    if kind in {"image", "audio"}:
        mime = item.get("mimeType")
        if not isinstance(mime, str) or not mime:
            mime = "application/octet-stream"
        data = item.get("data")
        size = len(data) if isinstance(data, str) else 0
        return [f"[{kind} {mime}, {size} base64 characters omitted]"]
    if kind == "resource":
        resource = item.get("resource")
        return _resource_parts(resource) if isinstance(resource, dict) else []
    if kind == "resource_link":
        uri = item.get("uri")
        name = item.get("name")
        label = name if isinstance(name, str) and name else str(uri or "")
        return [f"[resource link] {label}: {uri}"]
    return [f"[{kind or 'unknown'} content]"]


def _resource_parts(resource: Mapping[str, Any]) -> list[str]:
    uri = resource.get("uri")
    location = str(uri or "resource")
    text = resource.get("text")
    if isinstance(text, str):
        return [f"[{location}]\n{text}"]
    blob = resource.get("blob")
    if isinstance(blob, str):
        mime = resource.get("mimeType")
        if not isinstance(mime, str) or not mime:
            mime = "application/octet-stream"
        return [f"[{location} {mime}, {len(blob)} base64 characters omitted]"]
    return [f"[{location}]"]
