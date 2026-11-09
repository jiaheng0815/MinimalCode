"""DeepSeek web (chat.deepseek.com) protocol: proof of work, patch SSE, text tools.

The consumer web endpoint differs from every API dialect this package speaks:

* Every completion is gated by ``x-ds-pow-response``, a base64 ticket whose
  ``answer`` must satisfy ``DeepSeekHashV1(salt_expire_at_nonce) == challenge``.
  That hash ships only as a WebAssembly module, so the module runs DeepSeek's own
  binary through wasmtime instead of reimplementing non-standard Keccak.
* The stream is not one event per token: it is JSON-Patch over the response
  object, and thinking-mode answers only appear in full in a trailing snapshot.
* There is no function calling, so tools are rendered into the prompt and parsed
  back out of a delimited block (``tool_mode = "text"``). ``"none"`` disables
  both the instructions and the filter.

Everything here is one round trip per ``stream_chat`` call: a fresh chat session
is created each turn and the whole history is flattened into one prompt, which
keeps the client stateless and immune to the server's ``invalid message id``
chain failures.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ..errors import ProviderError
from .types import ProviderReply, ToolCall

CHAT_PATH = "/api/v0/chat/completion"
SESSION_PATH = "/api/v0/chat_session/create"
POW_PATH = "/api/v0/chat/create_pow_challenge"

# One delimited block instead of bare JSON: the model may still wrap prose around
# it, and a fenced marker is far easier to find in a token stream than balanced
# braces.
TOOL_OPEN = "<<<TOOL_CALLS>>>"
TOOL_CLOSE = "<<<END_TOOL_CALLS>>>"

TOOL_INSTRUCTIONS = f"""\
# Tool protocol

You cannot call functions directly. To use a tool, print exactly one block:

{TOOL_OPEN}
[{{"name": "<tool name>", "arguments": {{<json arguments>}}}}]
{TOOL_CLOSE}

Rules:
- Emit the block alone, with no prose before or after it.
- The block must be strict JSON: double quotes only, no comments, no trailing
  commas, and every double quote *inside* a string escaped as \\".
- Emit several entries in one block to run tools in parallel.
- After the block, stop: the tool results arrive in the next user message.
- Only use tools from the list in this prompt; never invent a tool name.

Correct, note the escaped inner quotes:

{TOOL_OPEN}
[{{"name": "shell", "arguments": {{"command": "python -c \\"print(1)\\""}}}}]
{TOOL_CLOSE}
"""

# Sent as an extra turn when the tool block cannot be parsed. It names the exact
# reason so the model repairs its own syntax instead of repeating it.
_TOOL_CORRECTION = """

# Correction

Your previous reply was rejected by the tool-call parser: {error}

Re-emit the tool call now, exactly once, following every rule above. You may use
either the JSON block or DeepSeek's invoke markup, but the argument values must
never contain a stray unescaped double quote or leftover markup tags.
"""

# The wasm binary is DeepSeek's own build of the hash. It is cached on disk and
# only fetched when missing, so a normal install never needs the network twice.
WASM_FILENAME = "sha3_wasm_bg.wasm"
WASM_URLS = (
    "https://fe-static.deepseek.com/chat/static/sha3_wasm_bg.7b9ca65ddd.wasm",
    "https://raw.githubusercontent.com/sums001/Deepseek-API/main/deepseek/sha3_wasm_bg.wasm",
)
# A downloaded fallback is executed, so it has to match the module that ships in
# the wheel; the mirror above is third-party and must not be able to swap it.
WASM_SHA256 = "b3fca8cc072c1defbd60c02266a8e48bd307a1804aaff4314900aea720e72f7d"


def _cache_dir() -> Path:
    override = os.environ.get("MINIMALCODE_CACHE_DIR")
    root = Path(override) if override else Path.home() / ".minimalcode"
    return root / "cache"


def _ensure_wasm(configured: str | None = None) -> Path:
    """Return a usable wasm path, downloading once into the cache."""
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(__file__).resolve().parent / WASM_FILENAME)
    candidates.append(_cache_dir() / WASM_FILENAME)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 1024:
            return path
    import hashlib

    target = _cache_dir() / WASM_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in WASM_URLS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MinimalCode"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            if data[:4] != b"\x00asm":
                raise ValueError("not a wasm binary")
            digest = hashlib.sha256(data).hexdigest()
            if digest != WASM_SHA256:
                raise ValueError(
                    f"sha256 {digest} does not match the bundled module "
                    f"({WASM_SHA256}); refusing to execute it"
                )
            target.write_bytes(data)
            return target
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            errors.append(f"{url}: {exc}")
    raise ProviderError(
        "deepseek_web: cannot obtain sha3_wasm_bg.wasm; place it at "
        f"{target} or set providers.<name>.pow_wasm_path\n" + "\n".join(errors)
    )


class _PowSolver:
    """DeepSeekHashV1 solver backed by the vendor's wasm in wasmtime.

    A wasmtime store is not reentrant, so ``solve`` serialises on a lock; the
    computation itself is milliseconds at the usual difficulty of 144000.
    """

    def __init__(self, wasm_path: Path):
        self._lock = threading.Lock()
        self._wasm_path = wasm_path

    def _load(self) -> None:
        try:
            import wasmtime
        except ImportError as exc:
            raise ProviderError(
                "deepseek_web: proof of work needs the optional wasmtime package; "
                "install it with `pip install 'minimalcode[deepseek-web]'`"
            ) from exc

        try:
            engine = wasmtime.Engine()
            module = wasmtime.Module.from_file(engine, str(self._wasm_path))
            store = wasmtime.Store(engine)
            linker = wasmtime.Linker(engine)
            linker.define_wasi()
            instance = linker.instantiate(store, module)
            exports = instance.exports(store)
            self._memory = exports["memory"]
            self._solve_fn = exports["wasm_solve"]
            self._stack = exports["__wbindgen_add_to_stack_pointer"]
            names = ("__wbindgen_malloc", "__wbindgen_export_0")
            self._malloc = next(exports[name] for name in names if name in exports)
        except ProviderError:
            raise
        except Exception as exc:  # wasmtime raises its own error types
            raise ProviderError(
                "deepseek_web: cannot load the proof-of-work module "
                f"{self._wasm_path}: {exc}"
            ) from exc
        self._store = store

    def _write(self, text: str) -> tuple[int, int]:
        """Copy one UTF-8 string into wasm memory and return ``(ptr, len)``.

        The allocation is exactly the string length, never a reused or rounded
        buffer: the module takes ownership of the block and frees it with the
        length it was handed, so a larger allocation corrupts its allocator and
        the call traps. The block is intentionally never freed from here — a
        free after ``wasm_solve`` panics the module too, and a challenge only
        leaks a few dozen bytes.
        """
        data = text.encode("utf-8")
        pointer = self._malloc(self._store, max(len(data), 1), 1)
        self._memory.write(self._store, data, pointer)
        return pointer, len(data)

    def _solve(self, challenge: str, prefix: str, difficulty: float) -> int | None:
        store = self._store
        ret = self._stack(store, -16)
        try:
            c_ptr, c_len = self._write(challenge)
            p_ptr, p_len = self._write(prefix)
            self._solve_fn(store, ret, c_ptr, c_len, p_ptr, p_len, float(difficulty))
            status = struct.unpack("<i", self._memory.read(store, ret, ret + 4))[0]
            value = struct.unpack("<d", self._memory.read(store, ret + 8, ret + 16))[0]
        finally:
            self._stack(store, 16)
        return int(value) if status else None

    def header(self, challenge: dict[str, Any]) -> str:
        """Build the base64 ``x-ds-pow-response`` value for one challenge."""
        if not isinstance(challenge, dict) or not challenge:
            raise ProviderError(
                f"deepseek_web: pow challenge is {type(challenge).__name__}, "
                "not an object"
            )
        if challenge.get("algorithm") != "DeepSeekHashV1":
            raise ProviderError(
                "deepseek_web: unsupported pow algorithm "
                f"{challenge.get('algorithm')!r}"
            )
        required = ("challenge", "salt", "expire_at", "difficulty", "signature")
        missing = [key for key in required if key not in challenge]
        if missing:
            raise ProviderError(
                "deepseek_web: pow challenge is missing "
                f"{', '.join(missing)}; the server changed its challenge shape"
            )
        prefix = f"{challenge['salt']}_{challenge['expire_at']}_"
        with self._lock:
            if not hasattr(self, "_store"):
                self._load()
            answer = self._solve(
                challenge["challenge"], prefix, challenge["difficulty"]
            )
        if answer is None:
            raise ProviderError("deepseek_web: pow challenge expired or unsolvable")
        payload = {
            "algorithm": challenge["algorithm"],
            "challenge": challenge["challenge"],
            "salt": challenge["salt"],
            "answer": answer,
            "signature": challenge["signature"],
            "target_path": challenge.get("target_path", ""),
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")


_SOLVERS: dict[str, _PowSolver] = {}
_SOLVERS_LOCK = threading.Lock()


def _pow_solver(wasm_path: Path) -> _PowSolver:
    with _SOLVERS_LOCK:
        solver = _SOLVERS.get(str(wasm_path))
        if solver is None:
            solver = _PowSolver(wasm_path)
            _SOLVERS[str(wasm_path)] = solver
        return solver


def _client_stream_id() -> str:
    """``YYYYMMDD-<16 hex>``: the id the web client sends with every stream."""
    import secrets
    import time

    return f"{time.strftime('%Y%m%d')}-{secrets.token_hex(8)}"


def _frag_kind(kind: str | None) -> str:
    """Map a fragment type onto the three display channels.

    The type is compared case-insensitively, and an untyped fragment counts as
    answer text: the server omitting ``type`` used to drop the whole reply
    silently, which is worse than showing a SEARCH/TIP line by accident.
    """
    if isinstance(kind, str):
        lowered = kind.strip().lower()
        if lowered == "think":
            return "thinking"
        if lowered in {"response", "answer"}:
            return "answer"
    if kind is None:
        return "answer"
    return "other"


class _Fragments:
    """Track per-fragment output length so snapshots never repeat text.

    Thinking-mode replies stream THINK deltas and then send a trailing full
    snapshot whose RESPONSE fragment was never streamed. Differencing against the
    already emitted length delivers that tail exactly once.
    """

    def __init__(self) -> None:
        self.kinds: list[str | None] = []
        self.emitted: list[int] = []

    def _grow(self, size: int) -> None:
        while len(self.kinds) < size:
            self.kinds.append(None)
            self.emitted.append(0)

    def full(self, index: int, fragment: dict[str, Any]) -> tuple[str, str] | None:
        self._grow(index + 1)
        if fragment.get("type") is not None:
            self.kinds[index] = fragment.get("type")
        content = fragment.get("content") or ""
        if len(content) <= self.emitted[index]:
            return None
        delta = content[self.emitted[index] :]
        self.emitted[index] = len(content)
        return _frag_kind(self.kinds[index]), delta

    def append(self, index: int, text: str) -> tuple[str, str]:
        self._grow(index + 1)
        self.emitted[index] += len(text)
        return _frag_kind(self.kinds[index]), text

    def next_index(self) -> int:
        return len(self.kinds)


def _frag_index(path: str, count: int) -> int:
    """Parse the index out of ``response/fragments/<i>/content`` (-1 = last)."""
    head = path[: -len("/content")]
    try:
        index = int(head.rsplit("/", 1)[-1])
    except ValueError:
        return 0
    if index < 0:
        index += count
    return max(index, 0)


def iter_reply_chunks(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, text)`` increments from DeepSeek's patch-style SSE stream."""
    tracker = _Fragments()
    active: int | None = None
    current = ""

    def apply(path: str, value: Any) -> Iterator[tuple[str, str]]:
        nonlocal active
        if path == "response/fragments":
            items = value if isinstance(value, list) else [value]
            for offset, fragment in enumerate(items):
                if not isinstance(fragment, dict):
                    continue
                index = offset if isinstance(value, list) else tracker.next_index()
                chunk = tracker.full(index, fragment)
                if chunk is not None:
                    yield chunk
            return
        if isinstance(value, list) and all(
            isinstance(sub, dict) and "p" in sub for sub in value
        ):
            # {"p": "response", "o": "BATCH", "v": [{"p": "fragments", ...}]}
            for sub in value:
                yield from apply(f"{path}/{sub['p']}".lstrip("/"), sub.get("v"))
            return
        if "/fragments/" in path and path.endswith("/content"):
            active = _frag_index(path, tracker.next_index())
            if isinstance(value, str) and value:
                yield tracker.append(active, value)
            return
        if path.endswith("thinking_content") and isinstance(value, str) and value:
            yield "thinking", value
            return
        if path == "response/content" and isinstance(value, str) and value:
            # Same text as the fragment append above, addressed without an index.
            index = active if active is not None else 0
            yield tracker.append(index, value)

    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        value = event.get("v", event)
        response = value.get("response") if isinstance(value, dict) else None
        if isinstance(response, dict) and "fragments" in response:
            current = "response"
            for index, fragment in enumerate(response.get("fragments") or []):
                chunk = tracker.full(index, fragment)
                if chunk is not None:
                    yield chunk
            continue
        path = event.get("p")
        if isinstance(path, str):
            current = path
            yield from apply(path, value)
            continue
        if isinstance(value, list):
            yield from apply(current, value)
            continue
        if isinstance(value, str) and value and active is not None:
            yield tracker.append(active, value)


class _ToolParseError(ProviderError):
    """A tool block was found but could not be decoded.

    Separate from ``ProviderError`` so a turn can retry on a syntax problem while
    transport and auth failures still abort immediately.
    """


# The model is not consistent about the markup it emits, so every shape seen in
# practice is accepted: the requested JSON block (ASCII or fullwidth bars, with or
# without the ``/`` and ``END_`` spelling) and DeepSeek's own DSML invoke markup.
# One alternation is what keeps an unexpected spelling from leaking into the
# user-visible text, which used to leave raw tags inside tool arguments.
_TAG = (
    r"<<<\s*/?\s*(?:END_)?\s*TOOL_CALLS\s*>>>"
    r"|<<\s*/?\s*(?:END_)?\s*TOOL_CALLS\s*>>"
    r"|</?\s*(?:END_)?\s*TOOL_CALLS\s*>"
    # A DSML tag carries attributes, so the whole tag up to its own `>` is
    # consumed; stopping at the name left `name="path"` behind in stripped
    # values. The last branch is the same tag truncated at end of string.
    r"|<[^<>]*\bDSML\b[^<>]*>"
    r"|<[^<>]*\bDSML\b[^<>]*\Z"
)
_TAG_RE = re.compile(_TAG, re.IGNORECASE)
_DSML_TAG = re.compile(
    r"<\s*(?P<closing>/)?\s*[|｜]{0,2}\s*DSML[|｜]{0,2}\s*"
    r"(?P<kind>[A-Za-z_]+)?\s*(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)
_MARKUP_RE = re.compile(_TAG, re.IGNORECASE)
# Longest accepted opener, so a marker split across chunks is never displayed.
_TAG_HOLD = 24
# A bare payload without markers is only a tool call when it carries an argument
# key; ``{"name": "Alice", "age": 30}`` is an answer that happens to have a name.
_BARE_CALL_KEYS = ('"arguments"', '"parameters"', '"args"')


def _looks_like_bare_call(text: str) -> bool:
    return (
        text[:1] in {"[", "{"}
        and '"name"' in text
        and any(key in text for key in _BARE_CALL_KEYS)
    )


class _ToolFilter:
    """Split streamed text into display text and one trailing tool-call block.

    Both the requested JSON block and native DSML invokes are recognized. An opener
    can be split across chunks, so a short suffix is held back until it is provably
    not the start of one. Once an opener is seen nothing further is displayed: the
    remainder is markup plus arguments, and showing it is what put raw tags into the
    transcript before.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.buffer = ""
        self.calls: list[dict[str, Any]] = []
        self._collecting = False
        self._closed = False

    def feed(self, text: str) -> str:
        if not self.enabled or self._closed:
            return text
        self.buffer += text
        if self._collecting:
            return ""
        match = _TAG_RE.search(self.buffer)
        if match is not None:
            head = self.buffer[: match.start()]
            self.buffer = self.buffer[match.end() :]
            self._collecting = True
            return head
        if self.buffer.lstrip()[:1] in {"[", "{"}:
            # A reply that opens with JSON is either a marker-less tool call or a
            # JSON answer; emitting it now would print the payload before
            # ``finish`` can classify it, so nothing is shown until then.
            return ""
        keep = _TAG_HOLD - 1
        if len(self.buffer) > keep:
            out, self.buffer = self.buffer[:-keep], self.buffer[-keep:]
            return out
        return ""

    def finish(self) -> str:
        """Return still-unemitted display text and decode any collected block."""
        if not self.enabled or self._closed:
            return ""
        self._closed = True
        if self._collecting:
            body = self.buffer
            self.buffer = ""
            self.calls = extract_tool_calls(body)
            return ""
        tail, self.buffer = self.buffer, ""
        stripped = tail.strip()
        # Models sometimes drop the markers entirely and answer with bare JSON.
        # Treating that as prose would bury a tool call inside the reply and print
        # the payload at the user, so it is parsed instead; only the leading
        # whitespace is kept so the transcript keeps its spacing. An ordinary JSON
        # answer has no argument key and stays prose, and a payload that looks like
        # a call but does not decode raises so the model is asked to repair it.
        if _looks_like_bare_call(stripped):
            self.calls = extract_tool_calls(stripped)
            return tail[: len(tail) - len(tail.lstrip())]
        return tail


def _dsml_attrs(blob: str) -> dict[str, str]:
    """Parse ``name="value"`` pairs out of a tag's attribute blob."""
    return {
        key.lower(): value
        for key, value in re.findall(r"([A-Za-z_]+)\s*=\s*\"([^\"]*)\"", blob)
    }


def _strip_markup(text: str) -> str:
    """Remove any residual tag so it can never reach a tool argument."""
    return _MARKUP_RE.sub("", text)


def _dsml_value(raw: str, attributes: str) -> Any:
    """Decode one DSML parameter: only ``string="false"`` marks a JSON literal."""
    cleaned = _strip_markup(raw).strip()
    if 'string="false"' in attributes or "string='false'" in attributes:
        try:
            return json.loads(cleaned)
        except ValueError:
            return cleaned
    return cleaned


def _split_dsml(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse DSML by walking tags, tolerating missing close tags.

    A regex that required ``</...parameter>`` swallowed the following markup into
    the value whenever the model omitted the close, which is how a path argument
    ended up as ``D:\\MinimalCode\\`` plus tags. Bounding every value by the *next
    tag* instead makes a missing close tag harmless, and each value is stripped of
    residual markup afterwards.
    """
    calls: list[dict[str, Any]] = []
    plain: list[str] = []
    current: dict[str, Any] | None = None
    param_name: str | None = None
    param_attrs = ""
    value: list[str] = []
    cursor = 0

    def flush_param() -> None:
        nonlocal param_name, param_attrs, value
        if current is not None and param_name:
            current["arguments"][param_name] = _dsml_value("".join(value), param_attrs)
        param_name, param_attrs, value = None, "", []

    def flush_call() -> None:
        nonlocal current
        flush_param()
        if current is not None and str(current.get("name") or "").strip():
            calls.append(current)
        current = None

    for match in _DSML_TAG.finditer(text):
        chunk = text[cursor : match.start()]
        cursor = match.end()
        if param_name is not None:
            value.append(chunk)
        elif current is None:
            plain.append(chunk)
        closing = bool(match.group("closing"))
        kind = (match.group("kind") or "").lower()
        attrs = _dsml_attrs(match.group("attrs") or "")
        if closing:
            if kind == "parameter":
                flush_param()
            elif kind in {"invoke", "calls"}:
                flush_call()
            continue
        if kind == "invoke":
            flush_call()
            current = {"name": attrs.get("name") or "", "arguments": {}}
        elif kind == "parameter" and current is not None:
            flush_param()
            param_name = attrs.get("name") or None
            param_attrs = match.group("attrs") or ""
        elif kind == "calls":
            flush_call()
    tail = text[cursor:]
    if param_name is not None:
        value.append(tail)
    elif current is None and tail:
        plain.append(tail)
    flush_call()
    return "".join(plain), calls


def _escape_inner_quotes(text: str) -> str:
    """Escape quotes that appear inside a JSON string instead of ending it.

    The model writes ``"command": "python -c "print(1)""`` often enough that
    rejecting it wastes a whole turn. A quote only closes a string when the next
    significant character is a delimiter, which is exactly the case the model gets
    wrong.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            if not in_string:
                in_string = True
                out.append(char)
                continue
            look = index + 1
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look >= length or text[look] in ",:}]":
                in_string = False
                out.append(char)
            else:
                out.append('\\"')
            continue
        if in_string and char in "\r\n":
            out.append("\\n")
            continue
        out.append(char)
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Drop commas before ``}``/``]`` without touching string contents.
    A plain regex that matched a comma before a closing brace also rewrote the
    same sequence inside a string literal, corrupting the argument that the
    repair pass was trying to save.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            out.append(char)
            continue
        if char == ",":
            look = index + 1
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look < length and text[look] in "}]":
                continue
        out.append(char)
    return "".join(out)


def _loads_lenient(text: str) -> Any:
    """Parse JSON, repairing the two mistakes the model actually makes."""
    repaired = _escape_inner_quotes(text)
    attempts = [
        text,
        repaired,
        _strip_trailing_commas(text),
        _strip_trailing_commas(repaired),
    ]
    last: ValueError | None = None
    for candidate in attempts:
        try:
            return json.loads(candidate)
        except ValueError as exc:
            last = exc
    raise _ToolParseError(
        f"invalid tool-call JSON ({last}); argument strings likely contain "
        'unescaped double quotes, which must be written as \\"'
    )


def _iter_json_arrays(text: str) -> Iterator[str]:
    """Yield every balanced top-level ``[...]`` slice, ignoring brackets in strings."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            if depth == 0:
                start = index
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : index + 1]


def _entries_from_json(payload: Any) -> list[dict[str, Any]]:
    """Normalize one decoded JSON payload into ``{name, arguments}`` entries."""
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool")
        arguments = item.get("arguments", item.get("parameters", item.get("args")))
        if not isinstance(name, str):
            # Also accept the OpenAI-shaped {"function": {"name", "arguments"}}.
            function = item.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                arguments = function.get("arguments", arguments)
        if not isinstance(name, str) or not name.strip():
            continue
        if arguments is None:
            arguments = {}
        if isinstance(arguments, str):
            # A string here is a JSON object serialized one level too deep. If it
            # cannot be decoded the call is broken: silently passing the raw text
            # as `{"value": ...}` handed garbage to the tool.
            arguments = _loads_lenient(arguments)
        if not isinstance(arguments, (dict, list)):
            raise _ToolParseError(
                f"tool call {name.strip()!r} has {type(arguments).__name__} "
                "arguments; expected a JSON object"
            )
        entries.append({"name": name.strip(), "arguments": arguments})
    return entries


def extract_tool_calls(body: str) -> list[dict[str, Any]]:
    """Decode a collected block, accepting DSML markup or a JSON array.

    Raises ``_ToolParseError`` when the block clearly tried to call a tool but
    could not be decoded, so the caller can ask the model to regenerate instead of
    passing garbage to a tool.
    """
    markupped = _DSML_TAG.search(body) is not None
    remaining, dsml_calls = _split_dsml(body)
    text = _strip_markup(remaining)
    calls: list[dict[str, Any]] = list(dsml_calls)
    parse_error: _ToolParseError | None = None
    for candidate in _iter_json_arrays(text):
        try:
            calls.extend(_entries_from_json(_loads_lenient(candidate)))
        except _ToolParseError as exc:
            # Keep the specific reason: "arguments are not JSON" tells the model
            # what to repair, while the generic message below does not.
            parse_error = exc
            continue
    if not calls and parse_error is not None:
        raise parse_error
    looks_like_call = any(
        token in text for token in ("name", "arguments", "invoke", "{")
    )
    # Empty DSML markup is a broken call, not prose: dropping it silently would
    # leave the turn with no tool call and no explanation.
    if not calls and (looks_like_call or markupped):
        raise _ToolParseError(f"tool block decoded to no calls: {body.strip()[:200]!r}")
    return calls


def _parse_dsml(text: str) -> list[dict[str, Any]]:
    """Parse native ``invoke``/``parameter`` markup into tool-call entries."""
    _plain, calls = _split_dsml(text)
    if not calls and _DSML_TAG.search(text) is not None:
        raise _ToolParseError(f"DSML markup holds no named invoke: {text[:200]!r}")
    return calls


def _parse_tool_block(body: str) -> list[dict[str, Any]]:
    """Parse the JSON array inside one tool block, tolerating fences and prose."""
    text = body.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    for candidate in _iter_json_arrays(text):
        try:
            entries = _entries_from_json(_loads_lenient(candidate))
        except _ToolParseError:
            continue
        if entries:
            return entries
    if not text:
        return []
    raise _ToolParseError(f"tool block is not a JSON array: {body.strip()[:200]!r}")


class DeepSeekWebProtocolMixin:
    """``deepseek_web`` wire format for the provider client."""

    # Supplied by OpenAICompatibleProvider.
    config: dict[str, Any]
    _client: Any
    model: str
    thinking: str

    def _open(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _read_body(self, response: Any) -> bytes:
        raise NotImplementedError

    # ---- payload ---------------------------------------------------------
    @staticmethod
    def _render_message(message: dict[str, Any]) -> str:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, list):
            parts = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict)
            ]
            text = "\n".join(part for part in parts if part)
        else:
            text = "" if content is None else str(content)
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            rendered = []
            for call in calls:
                function = call.get("function") or {}
                rendered.append(
                    json.dumps(
                        {
                            "name": function.get("name"),
                            "arguments": function.get("arguments"),
                        },
                        ensure_ascii=False,
                    )
                )
            text = (text + "\n" + "\n".join(rendered)).strip()
        label = "tool result" if role == "tool" else role
        return f"## {label}\n{text}".rstrip()

    def _ds_web_prompt(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> str:
        """Flatten the conversation (and tool schemas) into one prompt."""
        blocks: list[str] = []
        tool_mode = str(self.config.get("tool_mode", "text")).lower()
        if tools and tool_mode == "text":
            schema = json.dumps(tools, ensure_ascii=False)
            blocks.append(f"{TOOL_INSTRUCTIONS}\nAvailable tools:\n{schema}")
        for message in messages:
            if not isinstance(message, dict):
                continue
            if not isinstance(message.get("role"), str):
                continue
            blocks.append(self._render_message(message))
        prompt = "\n\n".join(blocks).strip()
        if not prompt:
            raise ProviderError("deepseek_web: refusing to send an empty prompt")
        return prompt

    # ---- handshake -------------------------------------------------------
    def _ds_web_url(self, path: str) -> str:
        """Absolute URL: the pooled client carries no base_url of its own."""
        base = str(
            getattr(self, "base_url", "") or self.config.get("base_url") or ""
        ).rstrip("/")
        if not base:
            raise ProviderError("deepseek_web: provider requires base_url")
        return f"{base}{path}"

    def _ds_web_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a handshake request, retrying TLS resets the same way a turn does.

        These calls used to go through a bare ``client.post``, so a single
        "[SSL: UNEXPECTED_EOF_WHILE_READING]" aborted the whole turn even though the
        request is idempotent and the endpoint answers fine on the next attempt.
        """
        url = self._ds_web_url(path)
        try:
            body_bytes = self._post_json(url, payload)
        except ProviderError as exc:
            message = str(exc)
            if "SSL" in message or "UNEXPECTED_EOF" in message.upper():
                raise ProviderError(
                    f"deepseek_web: TLS connection to {url} dropped before a reply "
                    f"({message}). A local proxy or VPN is the usual cause: set "
                    "HTTPS_PROXY (and NO_PROXY=127.0.0.1,localhost) so the request "
                    "and the proxy agree, then retry."
                ) from exc
            raise
        text = body_bytes.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except ValueError as exc:
            raise ProviderError(
                f"deepseek_web: {path} returned non-JSON (Cloudflare block?): "
                f"{text[:300]}"
            ) from exc
        if not isinstance(body, dict):
            raise ProviderError(
                f"deepseek_web: {path} returned {type(body).__name__} instead of "
                f"a JSON object: {text[:300]}"
            )
        if body.get("code") not in (0, None):
            raise ProviderError(
                f"deepseek_web: {path} rejected the request: {body.get('msg') or body}"
            )
        data = body.get("data")
        biz = data.get("biz_data") if isinstance(data, dict) else None
        if not isinstance(biz, dict):
            raise ProviderError(
                f"deepseek_web: {path} returned no biz_data: {text[:300]}"
            )
        return biz

    def _ds_web_session(self) -> str:
        # The live endpoint returns the id as `biz_data.id`; older shapes nest it
        # under `chat_session`, which is kept as a fallback.
        biz = self._ds_web_post(SESSION_PATH, {"character_id": None})
        session = biz.get("chat_session")
        session_id = biz.get("id")
        if not session_id and isinstance(session, dict):
            session_id = session.get("id")
        if not session_id:
            raise ProviderError(f"deepseek_web: session create returned {biz!r}")
        return str(session_id)

    def _ds_web_pow(self) -> str:
        wasm = _ensure_wasm(self.config.get("pow_wasm_path"))
        biz = self._ds_web_post(POW_PATH, {"target_path": CHAT_PATH})
        challenge = biz.get("challenge") or {}
        return _pow_solver(wasm).header(challenge)

    def _ds_web_apply_cookie(self) -> None:
        """Attach the session cookie from config or the environment.

        The Bearer token is the credential; the cookie only matters behind a WAF
        that inspects it, so a missing cookie is not an error. It is not a preset
        header on purpose: a ``${...}`` placeholder in a builtin preset would make
        an unconfigured install fail to build its endpoint.
        """
        cookie = self.config.get("cookie")
        if not cookie:
            env_name = str(self.config.get("cookie_env") or "DEEPSEEK_WEB_COOKIE")
            cookie = os.environ.get(env_name, "")
        if cookie:
            self._client.headers["Cookie"] = str(cookie)

    # ---- one turn --------------------------------------------------------
    def _ds_web_endpoint_checked(self) -> str:
        """Return the completion URL after proving the request really is "web".

        ``protocol`` selects this mixin, but a provider entry that pairs a web
        protocol with an API ``base_url`` would otherwise send a web-shaped body to
        an API host and fail obscurely. The host is verified against ``base_url``
        and the resolved URL is reported through ``notify``, so the transcript
        records where the turn actually went.
        """
        from urllib.parse import urlsplit

        if getattr(self, "protocol", "") != "deepseek_web":
            raise ProviderError(
                "deepseek_web: turn dispatched for protocol "
                f"{getattr(self, 'protocol', None)!r}; expected 'deepseek_web'"
            )
        url = self._endpoint()
        expected = urlsplit(str(self.config.get("base_url") or "")).hostname
        actual = urlsplit(url).hostname
        if not actual or actual != expected:
            raise ProviderError(
                f"deepseek_web: endpoint host {actual!r} does not match base_url "
                f"{expected!r}; refusing to send a web payload elsewhere"
            )
        if not url.endswith(CHAT_PATH):
            raise ProviderError(
                f"deepseek_web: endpoint {url!r} does not end with {CHAT_PATH}"
            )
        notify = getattr(self, "notify", None)
        if callable(notify):
            notify(f"[provider] deepseek_web POST {url}")
        return url

    def _ds_web_once(
        self,
        prompt: str,
        tools: list[dict[str, Any]] | None,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        endpoint = self._ds_web_endpoint_checked()
        # The Bearer token authenticates every call; the optional cookie is
        # attached first so a WAF that inspects it also sees the handshakes.
        self._ds_web_apply_cookie()
        thinking = self.thinking.strip().lower() not in {"", "none", "off"}
        payload: dict[str, Any] = {
            "chat_session_id": self._ds_web_session(),
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": thinking,
            "search_enabled": bool(self.config.get("web_search", False)),
            "action": None,
            "preempt": False,
            # Without these two the endpoint answers with one non-SSE JSON body,
            # which the stream parser then has to reject.
            "stream": True,
            "client_stream_id": _client_stream_id(),
        }
        # `-m`/`/model` has to win over the preset's `model_type`, otherwise a
        # chosen web model was silently replaced by the preset's `default`.
        model_type = str(self.model or "").strip()
        configured_model = str(self.config.get("model") or "").strip()
        if self.config.get("model_type") and model_type in {"", configured_model}:
            model_type = str(self.config["model_type"]).strip()
        # The web UI's default model is the fast one; an explicit type is only
        # honoured when the account's model_configs enable it.
        if model_type and model_type not in {"auto", "web"}:
            payload["model_type"] = model_type

        # ``_open`` builds the request from the client headers, so the ticket has
        # to be installed there; challenges are single-use and short lived, which
        # is why this runs immediately before every request. It is removed again
        # right after: a ticket minted for the completion path must not ride along
        # on the next turn's handshake.
        self._client.headers["x-ds-pow-response"] = self._ds_web_pow()
        try:
            response = self._open(payload)
        finally:
            self._client.headers.pop("x-ds-pow-response", None)
        kind = response.headers.get("content-type", "")
        if "text/event-stream" not in kind:
            status = response.status_code
            try:
                body = self._read_body(response).decode("utf-8", errors="replace")
            finally:
                response.close()
            raise ProviderError(
                f"deepseek_web: {endpoint} returned {kind or 'unknown'} "
                f"({status}) instead of text/event-stream: {body[:300]}"
            )

        enabled = str(self.config.get("tool_mode", "text")).lower() == "text"
        tool_filter = _ToolFilter(enabled and bool(tools))
        parts: list[str] = []
        usage: dict[str, int] = {}
        try:
            for chunk_kind, text in iter_reply_chunks(self._sse_lines(response)):
                if chunk_kind == "thinking":
                    if on_thinking is not None:
                        on_thinking(text)
                    continue
                if chunk_kind != "answer":
                    continue
                visible = tool_filter.feed(text)
                if visible:
                    parts.append(visible)
                    if on_text is not None:
                        on_text(visible)
            tail = tool_filter.finish()
            if tail:
                parts.append(tail)
                if on_text is not None:
                    on_text(tail)
        finally:
            response.close()

        calls: list[ToolCall] = []
        for index, entry in enumerate(tool_filter.calls):
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            # `_entries_from_json` only produces objects/arrays, so anything else
            # here is a defensive default rather than a silent coercion of raw
            # text into a `{"value": ...}` argument.
            arguments = entry.get("arguments")
            if not isinstance(arguments, (dict, list)):
                arguments = {}
            rendered = json.dumps(arguments, ensure_ascii=False)
            calls.append(ToolCall(id=f"dsweb_{index}", name=name, arguments=rendered))

        return ProviderReply(
            content="".join(parts).strip(),
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            usage=usage,
        )

    def _ds_web_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        on_text: Callable[[str], None] | None,
        on_thinking: Callable[[str], None] | None,
    ) -> ProviderReply:
        """Run one turn, re-asking once when the tool block cannot be parsed.

        A malformed block used to end the turn, and raw markup could still reach a
        tool as an argument value. Now the failure is reported back to the model
        with its exact reason, since the model is the only party that can fix its
        own syntax.
        """
        prompt = self._ds_web_prompt(messages, tools)
        try:
            retries = int(self.config.get("tool_parse_retries", 1) or 0)
        except (TypeError, ValueError):
            retries = 1
        last_error: _ToolParseError | None = None
        for attempt in range(max(0, retries) + 1):
            ask = (
                prompt
                if attempt == 0
                else prompt + _TOOL_CORRECTION.format(error=last_error)
            )
            try:
                return self._ds_web_once(
                    ask, tools, on_text=on_text, on_thinking=on_thinking
                )
            except _ToolParseError as exc:
                last_error = exc
                notify = getattr(self, "notify", None)
                if callable(notify):
                    notify(f"[retry] tool call rejected: {exc}")
        if last_error is not None:
            raise last_error
        raise ProviderError("deepseek_web: turn produced no reply")
