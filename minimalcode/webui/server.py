"""HTTP routes for the chat web UI.

One process, one ``WebRuntime``, and ``ThreadingHTTPServer``: no daemon, no
extra dependency. The socket is bound to loopback by default and every request
is checked against the ``Host`` and ``Origin`` headers so a page on another
origin cannot drive a tool loop that executes commands in the workspace.
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qs, urlsplit

from ..config import ConfigError, Settings
from ..errors import MinimalCodeError
from .runtime import Busy, WebRuntime, browse_directories

# One message is text; a megabyte is far more than a prompt needs and keeps a
# local misconfiguration from turning into an unbounded allocation.
MAX_BODY_BYTES = 1 << 20
_STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}
_LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
_ASSET_CACHE: dict[str, bytes] = {}


def _asset(name: str) -> bytes:
    data = _ASSET_CACHE.get(name)
    if data is None:
        data = (
            resources.files("minimalcode.webui").joinpath("assets", name).read_bytes()
        )
        _ASSET_CACHE[name] = data
    return data


def _host_only(value: str) -> str | None:
    text = value.strip()
    try:
        parsed = urlsplit(text if "://" in text else "//" + text)
        return parsed.hostname
    except ValueError:
        return None


def _authority(value: str) -> tuple[str, str] | None:
    """Return ``(hostname, port)`` for a ``Host`` or ``Origin`` header.

    The port matters as much as the name: a page served by another app on
    loopback (``http://127.0.0.1:3000``) is a different origin, and it must not
    reach a server that can run commands in the workspace. A missing port is
    filled in from the scheme so an ``http://host`` origin still matches
    ``Host: host:80``.
    """
    text = value.strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text if "://" in text else "//" + text)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None
    if port is None:
        port = 443 if parsed.scheme in {"https", "wss"} else 80
    return hostname.lower(), str(port)


def _is_loopback(host: str | None) -> bool:
    return host is not None and host.lower() in _LOOPBACK_NAMES


class _EventStream:
    """Write server-sent events, and stop quietly once the browser leaves."""

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self.handler = handler
        self.closed = False

    def send(self, event: str, data: dict[str, Any]) -> None:
        if self.closed:
            return
        payload = json.dumps(data, ensure_ascii=False)
        frame = f"event: {event}\ndata: {payload}\n\n".encode()
        try:
            self.handler.wfile.write(frame)
            self.handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The turn keeps running; it just has nobody left to tell.
            self.closed = True


class WebHandler(BaseHTTPRequestHandler):
    server_version = "MinimalCode"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet: the page is the interface, not the console."""

    @property
    def runtime(self) -> WebRuntime:
        return self.server.runtime

    # -- routing ----------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path
        try:
            if not self._request_allowed():
                self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request"})
                return
            if method == "GET":
                self._route_get(path)
            else:
                self._route_post(path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Busy as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except (
            ConfigError,
            MinimalCodeError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _route_get(self, path: str) -> None:
        if path in {"/", "/index.html"}:
            self._bytes(_asset("index.html"), _STATIC_TYPES["index.html"])
            return
        if path == "/app.css":
            self._bytes(_asset("app.css"), _STATIC_TYPES["app.css"])
            return
        if path == "/app.js":
            self._bytes(_asset("app.js"), _STATIC_TYPES["app.js"])
            return
        if path == "/api/state":
            self._json(HTTPStatus.OK, self.runtime.describe())
            return
        if path == "/api/history":
            self._json(HTTPStatus.OK, self.runtime.history())
            return
        if path == "/api/sessions":
            self._json(
                HTTPStatus.OK,
                {
                    "sessions": self.runtime.sessions(),
                    "folders": self.runtime.folders(),
                },
            )
            return
        if path == "/api/browse":
            query = parse_qs(urlsplit(self.path).query)
            raw = (query.get("path") or [""])[0]
            self._json(HTTPStatus.OK, browse_directories(raw))
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"no such path: {path}"})

    def _route_post(self, path: str) -> None:
        if path == "/api/chat":
            self._chat()
            return
        body = self._read_json()
        if path == "/api/settings":
            self._json(HTTPStatus.OK, self.runtime.apply_settings(body))
            return
        if path == "/api/session":
            self._json(HTTPStatus.OK, self.runtime.open_session(body.get("id")))
            return
        if path == "/api/session/delete":
            self._json(HTTPStatus.OK, self.runtime.delete_session(body.get("id")))
            return
        if path == "/api/session/folder":
            self._json(
                HTTPStatus.OK,
                self.runtime.assign_folder(body.get("id"), body.get("folder")),
            )
            return
        if path == "/api/folder":
            identifier = body.get("id")
            if identifier:
                payload = self.runtime.rename_folder(identifier, body.get("name"))
            else:
                payload = self.runtime.create_folder(body.get("name"))
            self._json(HTTPStatus.OK, payload)
            return
        if path == "/api/folder/delete":
            self._json(HTTPStatus.OK, self.runtime.delete_folder(body.get("id")))
            return
        if path == "/api/thinking":
            self._json(HTTPStatus.OK, self.runtime.set_thinking(body.get("enabled")))
            return
        if path == "/api/workspace":
            self._json(HTTPStatus.OK, self.runtime.set_workspace(body.get("path")))
            return
        if path == "/api/new":
            self._json(HTTPStatus.OK, self.runtime.new_session())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"no such path: {path}"})

    # -- requests ---------------------------------------------------------

    def _chat(self) -> None:
        body = self._read_json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message is required")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # The stream ends when the turn ends, so the body is delimited by the
        # connection closing rather than by a length the server cannot know.
        self.send_header("Connection", "close")
        self.end_headers()
        self._headers_sent = True
        self.close_connection = True
        stream = _EventStream(self)
        try:
            self.runtime.chat(message, stream.send)
        except (
            Busy,
            ConfigError,
            MinimalCodeError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            # The status line is already on the wire, so a refused turn has to
            # arrive as an event instead of an HTTP error the page would drop.
            stream.send("error", {"message": str(exc)})

    def _request_allowed(self) -> bool:
        """Refuse anything that is not this page talking to this server."""
        host = self.headers.get("Host", "")
        hostname = _host_only(host) if host else None
        bind = getattr(self.server, "bind_host", "127.0.0.1")
        # A DNS-rebinding page reaches loopback with its own name in ``Host``.
        if (
            host
            and not _is_loopback(hostname)
            and bind not in {"0.0.0.0", "::", ""}
            and hostname != bind.lower()
        ):
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin.strip() == "null":
            return False
        # Same origin means the same host *and* the same port. Accepting any
        # loopback origin would let another local app (a dev server on :3000, a
        # page with an XSS hole) post to /api/chat and run shell commands here.
        target = _authority(host)
        return target is not None and _authority(origin) == target

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            # The unread body would otherwise be parsed as the next request on
            # this keep-alive connection, so the refusal closes it.
            self.close_connection = True
            raise ValueError("request body is too large")
        if length == 0:
            return {}
        text = self.rfile.read(length).decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise TypeError("request body must be a JSON object")
        return payload

    # -- responses --------------------------------------------------------

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._bytes(body, "application/json; charset=utf-8", status)

    def _bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        if getattr(self, "_headers_sent", False):
            # Headers are already on the wire; closing is all that is left.
            self.close_connection = True
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._headers_sent = True
        if self.command != "HEAD":
            self.wfile.write(body)


class WebServer(ThreadingHTTPServer):
    """Threaded HTTP server that owns one runtime."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: WebRuntime,
        *,
        bind_host: str,
    ) -> None:
        self.runtime = runtime
        self.bind_host = bind_host
        super().__init__(address, WebHandler)


def create_server(
    runtime: WebRuntime,
    host: str = "127.0.0.1",
    port: int = 8090,
) -> WebServer:
    """Bind the web UI server without starting it (tests drive it directly)."""
    if not 0 <= port <= 65535:
        raise ConfigError(f"invalid port: {port}")
    try:
        return WebServer((host, port), runtime, bind_host=host)
    except OSError as exc:
        raise ConfigError(f"cannot listen on {host}:{port}: {exc}") from exc


def _display_url(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{shown}:{port}/"


def serve(
    settings: Settings,
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
    provider: str | None = None,
    model: str | None = None,
    read_only: bool = False,
    save_session: bool = True,
    output: TextIO | None = None,
) -> int:
    """Run the chat web UI until Ctrl-C, then release every provider handle."""
    stream = sys.stdout if output is None else output
    runtime = WebRuntime(
        settings,
        workspace,
        provider=provider,
        model=model,
        read_only=read_only,
        save_session=save_session,
    )
    try:
        server = create_server(runtime, host, port)
    except ConfigError:
        runtime.close()
        raise
    state = runtime.describe()
    print(
        f"MinimalCode web UI: {_display_url(host, server.server_address[1])}\n"
        f"workspace: {state['workspace']}\n"
        f"provider: {state['provider']} ({state['protocol']}) model: {state['model']}",
        file=stream,
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print(file=stream)
    finally:
        server.server_close()
        runtime.close()
    return 0


__all__ = ["WebHandler", "WebServer", "create_server", "serve"]
