"""Transports that carry MCP JSON-RPC messages to a server.

Three transports are defined by the protocol and all three are supported:
``stdio`` (a child process with newline-delimited messages), ``http``
(streamable HTTP, one POST per message), and ``sse`` (the legacy HTTP+SSE
transport, a long-lived GET whose endpoint event names the POST target).

Every transport is bounded: reads have deadlines, stdout/stderr and response
bodies are captured through caps, and closing is idempotent so a failed startup
cannot leak a child process.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx2

from ..httpclient import build_client
from . import protocol

# Sentinel pushed onto a receive queue once the peer can no longer answer.
_CLOSED = object()
_STDERR_LINES = 12
_ERROR_BODY_CHARS = 2000
# A successful response carries the whole result (or the whole tool list), so
# it needs a generous cap; only the error path is limited to a short excerpt.
_MAX_BODY_CHARS = 8 * 1024 * 1024
_CLOSE_GRACE_SECONDS = 3.0

# Variables a stdio server needs to find its interpreter, its package cache,
# and its home directory. Everything else stays behind unless the server is
# configured with ``inherit_env`` or an explicit value, so adding an MCP server
# does not hand it every API key the parent process happens to hold.
_BASE_ENV_NAMES = frozenset(
    name.lower()
    for name in (
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "NO_COLOR",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PWD",
        "PYTHONIOENCODING",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    )
)


def stdio_environment(
    configured: Mapping[str, str] | None = None,
    *,
    inherit: bool = False,
) -> dict[str, str]:
    """Build the child environment for a stdio MCP server.

    ``inherit`` copies the parent environment for servers that need a
    vendor-specific variable the base list does not cover; the default keeps
    the server to the variables it needs to start plus whatever the user
    configured explicitly.
    """
    if inherit:
        env = dict(os.environ)
    else:
        env = {
            key: value
            for key, value in os.environ.items()
            if key.lower() in _BASE_ENV_NAMES
        }
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if configured:
        env.update({str(key): str(value) for key, value in configured.items()})
    return env


class StdioTransport:
    """Newline-delimited JSON-RPC over a child process's pipes."""

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
        *,
        cwd: Path,
        env: Mapping[str, str],
        label: str = "stdio",
    ) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.env = dict(env)
        self.label = label
        self._process: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[Any] = queue.Queue()
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        self._protocol_errors: deque[str] = deque(maxlen=3)

    def start(self) -> None:
        try:
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.cwd),
                env=self.env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise protocol.MCPError(f"cannot start {self.command!r}: {exc}") from exc
        threading.Thread(
            target=self._read_stdout,
            name=f"mcp-{self.label}-out",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            name=f"mcp-{self.label}-err",
            daemon=True,
        ).start()

    def send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            raise protocol.MCPError(self._closed_message())
        line = protocol.encode_message(message)
        with self._write_lock:
            try:
                assert process.stdin is not None
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise protocol.MCPError(
                    f"cannot write to the server: {exc}. {self._closed_message()}"
                ) from exc

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            item = self._queue.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return None
        if item is _CLOSED:
            # Keep the sentinel so a later receive reports the same failure
            # instead of blocking until its own deadline.
            self._queue.put(_CLOSED)
            raise protocol.MCPError(self._closed_message())
        return item

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_CLOSE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=_CLOSE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                    pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        self._queue.put(_CLOSED)

    def _read_stdout(self) -> None:
        process = self._process
        try:
            if process is not None and process.stdout is not None:
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = protocol.decode_message(line)
                    except protocol.MCPError as exc:
                        # A server that prints a log line to stdout is out of
                        # spec but common; keep reading instead of failing the
                        # session over one stray line.
                        self._protocol_errors.append(str(exc))
                        continue
                    self._queue.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._queue.put(_CLOSED)

    def _read_stderr(self) -> None:
        process = self._process
        try:
            if process is not None and process.stderr is not None:
                for line in process.stderr:
                    text = line.strip()
                    if text:
                        self._stderr.append(text[:300])
        except (OSError, ValueError):
            pass

    def _closed_message(self) -> str:
        process = self._process
        code = process.poll() if process is not None else None
        status = (
            "server closed its pipes" if code is None else f"server exited ({code})"
        )
        detail = f"; stderr: {' | '.join(self._stderr)}" if self._stderr else ""
        if self._protocol_errors:
            detail += f"; ignored output: {self._protocol_errors[-1]}"
        return f"{status}{detail}"


class HTTPTransport:
    """Streamable HTTP: each message is a POST, answered by JSON or SSE."""

    def __init__(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        timeout: float,
        trust_env: Any = None,
    ) -> None:
        self.url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._trust_env = trust_env
        self._client: httpx2.Client | None = None
        self._queue: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    def start(self) -> None:
        self._client = build_client(
            base_url=self.url,
            headers=self._headers,
            timeout=self._timeout,
            trust_env=self._trust_env,
        )

    def set_protocol_version(self, version: str) -> None:
        self._protocol_version = version

    def send(self, message: Mapping[str, Any]) -> None:
        client = self._client
        if client is None:
            raise protocol.MCPError("HTTP transport is not started")
        with self._lock:
            headers = dict(self._headers)
            headers["Accept"] = "application/json, text/event-stream"
            headers["Content-Type"] = "application/json"
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            if self._protocol_version:
                headers["MCP-Protocol-Version"] = self._protocol_version
            request_id = message.get("id")
            body = protocol.encode_message(message)
            try:
                with client.stream(
                    "POST",
                    self.url,
                    content=body.encode("utf-8"),
                    headers=headers,
                    timeout=self._timeout,
                ) as response:
                    self._remember_session(response)
                    if response.status_code >= 400:
                        detail = _read_body(response)
                        suffix = f": {detail}" if detail else ""
                        raise protocol.MCPError(
                            f"HTTP {response.status_code} from MCP server{suffix}"
                        )
                    if request_id is None or response.status_code == 202:
                        _drain(response)
                        return
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        messages = _read_sse(response, expect_id=request_id)
                    else:
                        messages = _payload_messages(
                            _read_body(response, limit=_MAX_BODY_CHARS)
                        )
            except httpx2.HTTPError as exc:
                raise protocol.MCPError(f"HTTP request failed: {exc}") from exc
        for item in messages:
            self._queue.put(item)

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            item = self._queue.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return None
        if item is _CLOSED:
            self._queue.put(_CLOSED)
            raise protocol.MCPError("HTTP session is closed")
        return item

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()
        self._queue.put(_CLOSED)

    def _remember_session(self, response: httpx2.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id


class SSETransport:
    """Legacy HTTP+SSE: a GET stream plus a POST endpoint it announces."""

    def __init__(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        timeout: float,
        startup_timeout: float,
        trust_env: Any = None,
    ) -> None:
        self.url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._startup_timeout = startup_timeout
        self._trust_env = trust_env
        self._client: httpx2.Client | None = None
        self._queue: queue.Queue[Any] = queue.Queue()
        self._post_url: str | None = None
        self._error: protocol.MCPError | None = None
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._client = build_client(
            base_url=self.url,
            headers=self._headers,
            timeout=self._timeout,
            trust_env=self._trust_env,
        )
        threading.Thread(
            target=self._read_stream,
            name="mcp-sse-reader",
            daemon=True,
        ).start()
        deadline = time.monotonic() + self._startup_timeout
        while self._post_url is None:
            if self._error is not None:
                raise self._error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise protocol.MCPTimeout(
                    f"no SSE endpoint event within {self._startup_timeout:g}s"
                )
            time.sleep(min(0.02, remaining))

    def send(self, message: Mapping[str, Any]) -> None:
        client = self._client
        if client is None or self._post_url is None:
            raise protocol.MCPError("SSE transport is not started")
        headers = dict(self._headers)
        headers["Content-Type"] = "application/json"
        body = protocol.encode_message(message)
        with self._lock:
            try:
                response = client.post(
                    self._post_url,
                    content=body.encode("utf-8"),
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx2.HTTPError as exc:
                raise protocol.MCPError(f"HTTP request failed: {exc}") from exc
        try:
            if response.status_code >= 400:
                detail = _read_body(response)
                suffix = f": {detail}" if detail else ""
                raise protocol.MCPError(
                    f"HTTP {response.status_code} from MCP server{suffix}"
                )
        finally:
            response.close()

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            item = self._queue.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return None
        if item is _CLOSED:
            self._queue.put(_CLOSED)
            raise self._error or protocol.MCPError("SSE stream closed")
        return item

    def close(self) -> None:
        self._closed = True
        client = self._client
        self._client = None
        if client is not None:
            client.close()
        self._queue.put(_CLOSED)

    def _read_stream(self) -> None:
        client = self._client
        headers = dict(self._headers)
        headers["Accept"] = "text/event-stream"
        event: str | None = None
        data: list[str] = []
        try:
            assert client is not None
            with client.stream(
                "GET",
                self.url,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                if response.status_code >= 400:
                    self._error = protocol.MCPError(
                        f"HTTP {response.status_code} from MCP server"
                    )
                    return
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line.strip():
                        payload = "\n".join(data)
                        data = []
                        if self._closed:
                            return
                        self._dispatch(event, payload)
                        event = None
        except httpx2.HTTPError as exc:
            if not self._closed:
                self._error = protocol.MCPError(f"SSE stream failed: {exc}")
        except protocol.MCPError as exc:
            self._error = exc
        finally:
            self._queue.put(_CLOSED)

    def _dispatch(self, event: str | None, payload: str) -> None:
        if not payload.strip():
            return
        if event == "endpoint":
            self._post_url = urljoin(self.url, payload.strip())
            return
        try:
            self._queue.put(protocol.decode_message(payload))
        except protocol.MCPError as exc:
            self._error = exc


def _read_body(response: httpx2.Response, limit: int = _ERROR_BODY_CHARS) -> str:
    """Read at most ``limit`` characters of a response body."""
    try:
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) >= limit:
                break
        return chunks[:limit].decode("utf-8", errors="replace").strip()
    except (httpx2.HTTPError, OSError, ValueError):
        return ""


def _drain(response: httpx2.Response) -> None:
    """Consume a body that is not used, bounded by a small budget."""
    _read_body(response, limit=4096)


def _payload_messages(text: str) -> list[dict[str, Any]]:
    """Parse a JSON or batch JSON response into validated messages."""
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise protocol.MCPError(f"invalid JSON response: {exc}") from exc
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise protocol.MCPError("JSON-RPC response must be an object or an array")
    messages: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise protocol.MCPError("JSON-RPC batch entries must be objects")
        messages.append(protocol.decode_message(json.dumps(item)))
    return messages


def _read_sse(
    response: httpx2.Response,
    *,
    expect_id: Any,
) -> list[dict[str, Any]]:
    """Read one SSE response, stopping once the matching reply arrives."""
    messages: list[dict[str, Any]] = []
    data: list[str] = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
            continue
        if line.strip():
            # event:, id:, and retry: fields carry no payload for a JSON-RPC
            # reply; the streamable transport uses the default "message" event.
            continue
        if not data:
            continue
        payload = "\n".join(data)
        data = []
        for message in _payload_messages(payload):
            messages.append(message)
            if "method" not in message and message.get("id") == expect_id:
                return messages
    if data:
        messages.extend(_payload_messages("\n".join(data)))
    return messages


def create_transport(
    *,
    transport: str,
    command: str = "",
    args: tuple[str, ...] = (),
    cwd: Path,
    env: Mapping[str, str] | None = None,
    url: str = "",
    headers: Mapping[str, str] | None = None,
    timeout: float,
    startup_timeout: float,
    label: str = "stdio",
    trust_env: Any = None,
) -> StdioTransport | HTTPTransport | SSETransport:
    """Build the transport named by one server configuration."""
    if transport == "stdio":
        return StdioTransport(
            command,
            args,
            cwd=cwd,
            env=stdio_environment(env),
            label=label,
        )
    if transport == "http":
        return HTTPTransport(
            url,
            headers,
            timeout=timeout,
            trust_env=trust_env,
        )
    if transport == "sse":
        return SSETransport(
            url,
            headers,
            timeout=timeout,
            startup_timeout=startup_timeout,
            trust_env=trust_env,
        )
    raise protocol.MCPError(f"unknown MCP transport: {transport!r}")
