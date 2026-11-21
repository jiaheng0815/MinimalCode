"""DeepSeek server-side web tool blocks: parsing, display, and replay.

The model asks DeepSeek's servers to search and to open pages; the client never
runs a tool. Three protocols report the same events in three shapes, so every
parser here produces one neutral block and every payload builder turns that
block back into its own wire form.

Only ``read_link`` blocks are replayed. The page body of a read link comes back
encrypted in ``signature`` and is what puts that page into the next turn's
context, so it must survive verbatim. Search results and the pages a deep search
opened are dropped from later turns by the service itself, so storing them would
only grow the transcript.

DeepSeek documents these as Chatbot conversation tools. MinimalCode is an agent,
so its default path is ``minimalcode.web.ProviderWebClient``, which calls
``/v1/tools/search`` and ``/v1/tools/fetch`` as normal function tools. The
switch lives in ``deepseek.server_tools()`` versus ``web_tools.enabled``:
``tools.schemas.available_tool_schemas`` hides the local pair only when these
server tools are explicitly active, so the two paths never compete inside one
turn.
"""

from __future__ import annotations

from typing import Any

from ..deepseek import READ_URL_TOOL, WEB_SEARCH_TOOL

# Bounds for what enters a transcript: a page can return hundreds of results and
# the block is stored once per assistant message.
_MAX_SOURCES = 20
_MAX_TITLE = 300
_MAX_SNIPPET = 600
_MAX_URL = 2000

SEARCH_RESULT_TYPES = frozenset({"web_search_tool_result", "web_fetch_tool_result"})


def _text(value: Any, limit: int | None = None) -> str | None:
    """Return a stripped string, or None when the field is unusable."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _action(raw: Any) -> dict[str, Any]:
    """Normalize a call's ``action`` object across the three protocols."""
    source = raw if isinstance(raw, dict) else {}
    queries = source.get("queries")
    return {
        "type": _text(source.get("type")) or "search",
        "queries": [
            text
            for text in (
                _text(item, 128)
                for item in (queries if isinstance(queries, list) else [])
            )
            if text
        ][:10],
        "url": _text(source.get("url"), _MAX_URL),
        "pattern": _text(source.get("pattern"), 200),
    }


def _sources(raw: Any) -> list[dict[str, Any]]:
    """Normalize search results into the fields a card can render."""
    results: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"), _MAX_URL)
        if not url:
            continue
        results.append(
            {
                "title": _text(item.get("title"), _MAX_TITLE) or url,
                "url": url,
                "snippet": _text(item.get("snippet"), _MAX_SNIPPET) or "",
                "site_name": _text(item.get("site_name"), _MAX_TITLE),
                "site_icon": _text(item.get("site_icon"), _MAX_URL),
                "published_at": _text(
                    item.get("published_at")
                    or item.get("page_age")
                    or item.get("date"),
                    64,
                ),
                "image_url": _text(item.get("image_url"), _MAX_URL),
            }
        )
        if len(results) >= _MAX_SOURCES:
            break
    return results


def _page(raw: Any) -> dict[str, Any] | None:
    """Normalize the ``{url, title, published_at}`` display object."""
    if not isinstance(raw, dict):
        return None
    url = _text(raw.get("url"), _MAX_URL)
    if not url:
        return None
    return {
        "url": url,
        "title": _text(raw.get("title"), _MAX_TITLE) or url,
        "published_at": _text(raw.get("published_at"), 64),
    }


def block(
    *,
    call_id: Any,
    name: Any,
    kind: Any = None,
    action: Any = None,
    status: Any = None,
    sources: Any = None,
    page: Any = None,
    error: Any = None,
    signature: Any = None,
) -> dict[str, Any]:
    """Build one neutral block. Empty display fields stay None."""
    return {
        "id": _text(call_id) or "",
        "name": _text(name) or "",
        "kind": _text(kind) or "search",
        "action": _action(action),
        "status": _text(status) or "completed",
        "sources": _sources(sources),
        "page": _page(page),
        "error": _text(error, 200),
        "signature": _text(signature),
    }


# -- error shapes ---------------------------------------------------------


def _error_code(content: Any) -> str | None:
    """Return the error code a failed result block carries, if any."""
    if isinstance(content, dict):
        if content.get("type") in {"web_search_tool_result_error"}:
            return _text(content.get("error_code"), 200) or "unavailable"
        if content.get("type") in {"web_fetch_tool_result_error"}:
            return _text(content.get("error_code"), 200) or "unavailable"
        return _text(content.get("error_code"), 200)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type", "").endswith("_error"):
            return _text(first.get("error_code"), 200) or "unavailable"
    return None


# -- Chat Completions -----------------------------------------------------


def chat_call_block(call: Any) -> dict[str, Any] | None:
    """Normalize one ``server_tool_calls[].calls[]`` entry."""
    if not isinstance(call, dict):
        return None
    error = call.get("error")
    return block(
        call_id=call.get("id"),
        name=call.get("type"),
        kind=call.get("kind"),
        action=call.get("action"),
        status=call.get("status"),
        sources=call.get("sources"),
        page=call.get("page"),
        error=_text(error.get("code"), 200) if isinstance(error, dict) else None,
        signature=call.get("signature"),
    )


def chat_blocks(message: Any) -> list[dict[str, Any]]:
    """Normalize the final ``message.server_tool_calls`` list."""
    if not isinstance(message, dict):
        return []
    blocks: list[dict[str, Any]] = []
    for step in message.get("server_tool_calls") or []:
        if not isinstance(step, dict):
            continue
        for call in step.get("calls") or []:
            parsed = chat_call_block(call)
            if parsed is not None:
                blocks.append(parsed)
    return blocks


def chat_event_block(raw: Any) -> dict[str, Any] | None:
    """Normalize one streamed ``server_tool_event`` for live display."""
    if not isinstance(raw, dict):
        return None
    event = _text(raw.get("type"))
    if event not in {"call_started", "call_completed"}:
        return None
    call = raw.get("call")
    if not isinstance(call, dict):
        return None
    return chat_call_block(call)


def chat_annotations(raw: Any) -> list[dict[str, Any]]:
    """Normalize ``message.annotations`` (or a streamed ``delta.annotations``)."""
    citations: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("type") != "url_citation":
            continue
        body = item.get("url_citation")
        if not isinstance(body, dict):
            continue
        url = _text(body.get("url"), _MAX_URL)
        if not url:
            continue
        citations.append(
            {
                "url": url,
                "title": _text(body.get("title"), _MAX_TITLE) or url,
                "start": body.get("start_index"),
                "end": body.get("end_index"),
            }
        )
    return citations


# -- Responses ------------------------------------------------------------


def responses_block(item: Any) -> dict[str, Any] | None:
    """Normalize one ``web_search_call`` output item."""
    if not isinstance(item, dict):
        return None
    if _text(item.get("type")) != "web_search_call":
        return None
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    error = item.get("error")
    return block(
        call_id=item.get("id"),
        name=item.get("name") or _tool_for_kind(item.get("kind")),
        kind=item.get("kind"),
        action=action,
        status=item.get("status"),
        sources=action.get("sources"),
        page=item.get("page"),
        error=_text(error.get("code"), 200) if isinstance(error, dict) else None,
        signature=item.get("signature"),
    )


def responses_blocks(output: Any) -> list[dict[str, Any]]:
    """Normalize the tool items of a complete Responses ``output`` list."""
    blocks: list[dict[str, Any]] = []
    for item in output if isinstance(output, list) else []:
        parsed = responses_block(item)
        if parsed is not None:
            blocks.append(parsed)
    return blocks


def responses_citations(content: Any) -> list[dict[str, Any]]:
    """Normalize ``url_citation`` annotations on ``output_text`` parts.

    Callers hand over either the parts themselves or the message items that
    carry them, so a nested ``content`` list is followed one level down.
    """
    citations: list[dict[str, Any]] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        parts = item.get("content")
        for part in parts if isinstance(parts, list) else [item]:
            if not isinstance(part, dict):
                continue
            for raw in part.get("annotations") or []:
                if not isinstance(raw, dict) or raw.get("type") != "url_citation":
                    continue
                url = _text(raw.get("url"), _MAX_URL)
                if not url:
                    continue
                citations.append(
                    {
                        "url": url,
                        "title": _text(raw.get("title"), _MAX_TITLE) or url,
                        "start": raw.get("start_index"),
                        "end": raw.get("end_index"),
                    }
                )
    return citations


def _tool_for_kind(kind: Any) -> str:
    """Name the tool a Responses item belongs to when it omits one."""
    return READ_URL_TOOL if _text(kind) == "read_link" else WEB_SEARCH_TOOL


# -- Messages -------------------------------------------------------------


def anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    """Merge Messages call blocks with their results, matched by ``tool_use_id``."""
    blocks: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        item_type = _text(item.get("type"))
        if item_type == "server_tool_use":
            parsed = block(
                call_id=item.get("id"),
                name=item.get("name"),
                kind=item.get("kind"),
                action=item.get("input"),
                status="in_progress",
            )
            blocks.append(parsed)
            if parsed["id"]:
                by_id[parsed["id"]] = parsed
            continue
        if item_type not in SEARCH_RESULT_TYPES:
            continue
        target = by_id.get(_text(item.get("tool_use_id")) or "")
        kind = _text(item.get("kind")) or (
            "read_link" if item_type.endswith("fetch_tool_result") else "search"
        )
        payload = item.get("content")
        if target is None:
            target = block(
                call_id=item.get("tool_use_id"), name=_tool_for_kind(kind), kind=kind
            )
            blocks.append(target)
            if target["id"]:
                by_id[target["id"]] = target
        target["kind"] = kind
        target["status"] = "failed" if _error_code(payload) else "completed"
        target["error"] = _error_code(payload)
        if item_type == "web_search_tool_result":
            target["sources"] = _sources(payload)
        elif isinstance(payload, dict):
            body = payload.get("content")
            page = {
                "url": payload.get("url"),
                "title": body.get("title") if isinstance(body, dict) else None,
                "published_at": payload.get("published_at"),
            }
            target["page"] = _page(page)
        target["signature"] = _text(item.get("signature")) or target["signature"]
    return blocks


def anthropic_citations(content: Any) -> list[dict[str, Any]]:
    """Normalize ``citations`` on Messages text blocks."""
    citations: list[dict[str, Any]] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict) or _text(item.get("type")) != "text":
            continue
        for raw in item.get("citations") or []:
            if not isinstance(raw, dict):
                continue
            url = _text(raw.get("url"), _MAX_URL)
            if not url:
                continue
            citations.append(
                {
                    "url": url,
                    "title": _text(raw.get("title"), _MAX_TITLE) or url,
                    "start": None,
                    "end": None,
                }
            )
    return citations


def anthropic_citation_delta(raw: Any) -> dict[str, Any] | None:
    """Normalize one streamed ``citations_delta`` citation."""
    if not isinstance(raw, dict):
        return None
    url = _text(raw.get("url"), _MAX_URL)
    if not url:
        return None
    return {
        "url": url,
        "title": _text(raw.get("title"), _MAX_TITLE) or url,
        "start": None,
        "end": None,
    }


# -- replay ---------------------------------------------------------------


def read_links(blocks: Any) -> list[dict[str, Any]]:
    """Return the blocks that must be replayed, deduplicated by call id."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in blocks if isinstance(blocks, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "read_link" or not item.get("signature"):
            continue
        identifier = _text(item.get("id")) or ""
        if identifier and identifier in seen:
            continue
        if identifier:
            seen.add(identifier)
        selected.append(item)
    return selected


def chat_replay(blocks: Any) -> list[dict[str, Any]]:
    """Build the ``server_tool_calls`` extension for a replayed turn."""
    calls: list[dict[str, Any]] = []
    for item in read_links(blocks):
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        calls.append(
            {
                "index": len(calls),
                "id": item.get("id") or f"srvtoolu_{len(calls)}",
                "type": READ_URL_TOOL,
                "kind": "read_link",
                "action": {
                    "type": "read_link",
                    "url": action.get("url")
                    or (item.get("page") or {}).get("url")
                    or "",
                },
                "status": "completed",
                "page": item.get("page"),
                "signature": item.get("signature"),
            }
        )
    if not calls:
        return []
    return [{"index": 0, "reasoning_content": "", "calls": calls}]


def anthropic_replay(blocks: Any) -> list[dict[str, Any]]:
    """Build the Messages content blocks for a replayed turn."""
    out: list[dict[str, Any]] = []
    for item in read_links(blocks):
        page = item.get("page") or {}
        url = item.get("action", {}).get("url") or page.get("url") or ""
        call_id = item.get("id") or f"srvtoolu_{len(out)}"
        out.append(
            {
                "type": "server_tool_use",
                "id": call_id,
                "name": READ_URL_TOOL,
                "input": {"action": "read_link", "url": url},
                "caller": {"type": "direct"},
                "kind": "read_link",
            }
        )
        out.append(
            {
                "type": "web_fetch_tool_result",
                "tool_use_id": call_id,
                "content": {
                    "type": "web_fetch_result",
                    "url": url,
                    "content": {
                        "type": "document",
                        "source": {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "",
                        },
                        "title": page.get("title") or url,
                    },
                    "published_at": page.get("published_at"),
                },
                "kind": "read_link",
                "signature": item.get("signature"),
            }
        )
    return out


def responses_replay(blocks: Any) -> list[dict[str, Any]]:
    """Build the Responses input items for a replayed turn."""
    items: list[dict[str, Any]] = []
    for item in read_links(blocks):
        page = item.get("page") or {}
        url = item.get("action", {}).get("url") or page.get("url") or ""
        items.append(
            {
                "type": "web_search_call",
                "id": item.get("id") or f"srvtoolu_{len(items)}",
                "status": "completed",
                "kind": "read_link",
                "action": {"type": "open_page", "url": url},
                "page": {
                    "url": url,
                    "title": page.get("title") or url,
                    "published_at": page.get("published_at"),
                },
                "signature": item.get("signature"),
            }
        )
    return items


def activity(block: Any) -> str:
    """Return one terminal-sized line describing a server-tool block."""
    if not isinstance(block, dict):
        return ""
    action = block.get("action") if isinstance(block.get("action"), dict) else {}
    labels = {
        "search": "search",
        "deep_search": "deep search",
        "read_link": "read link",
        "image_search": "image search",
        "deep_image_search": "deep image search",
    }
    label = labels.get(str(block.get("kind") or "search"), str(block.get("kind")))
    queries = " / ".join(action.get("queries") or [])
    detail = queries or action.get("url") or action.get("pattern") or ""
    if block.get("error"):
        suffix = f" ({block['error']})"
    elif str(block.get("status") or "") == "in_progress":
        suffix = "..."
    elif block.get("sources"):
        suffix = f" ({len(block['sources'])} results)"
    elif block.get("page"):
        suffix = " (page)"
    else:
        suffix = ""
    return f"{label}{': ' + detail if detail else ''}{suffix}"


__all__ = [
    "SEARCH_RESULT_TYPES",
    "activity",
    "anthropic_blocks",
    "anthropic_citation_delta",
    "anthropic_citations",
    "anthropic_replay",
    "block",
    "chat_annotations",
    "chat_blocks",
    "chat_call_block",
    "chat_event_block",
    "chat_replay",
    "read_links",
    "responses_block",
    "responses_blocks",
    "responses_citations",
    "responses_replay",
]
