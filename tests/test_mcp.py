"""Regression tests for MCP server support.

The stdio tests run a real child process that speaks JSON-RPC over its pipes,
because framing, handshake ordering, and shutdown are the parts that break
silently. The HTTP and SSE tests drive the transports through a fake client so
header propagation and stream parsing are covered without a listening socket.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2

from minimalcode.agent import Agent
from minimalcode.config import ConfigError, load_settings, redact_config
from minimalcode.mcp import (
    MCPClient,
    MCPError,
    MCPManager,
    MCPServerConfig,
    protocol,
    sanitize_tool_name,
)
from minimalcode.mcp.transport import (
    HTTPTransport,
    SSETransport,
    _payload_messages,
    stdio_environment,
)
from minimalcode.session import SessionStore
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolError, ToolRuntime, available_tool_schemas

FAKE_SERVER = r"""
import json
import os
import sys
import time

send = lambda message: (
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n"),
    sys.stdout.flush(),
)

def result(request_id, value):
    send({"jsonrpc": "2.0", "id": request_id, "result": value})

def error(request_id, code, text):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}})

def read_message():
    for line in sys.stdin:
        line = line.strip()
        if line:
            return json.loads(line)
    return None

def wait_for_response(request_id):
    while True:
        message = read_message()
        if message is None:
            return None
        if message.get("id") == request_id and "method" not in message:
            return message

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        },
    },
    {
        "name": "write_note",
        "description": "Write a note.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "boom",
        "description": "Fail.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "big",
        "description": "Return a large result.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "environ",
        "description": "Return environment and cwd.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "image",
        "description": "Return an image block.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "expose_extra",
        "description": "Add a tool and notify.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "Answer slowly.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dot.name",
        "description": "Name with a dot.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dot_name",
        "description": "Name with an underscore.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

state = {"extra": False, "root": "<none>"}

def main():
    while True:
        message = read_message()
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        if method is None or request_id is None:
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": 900, "method": "roots/list"})
            answer = wait_for_response(900)
            roots = ((answer or {}).get("result") or {}).get("roots") or []
            state["root"] = roots[0]["uri"] if roots else "<none>"
            result(request_id, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0"},
                "instructions": "Use echo for text.",
            })
        elif method == "tools/list":
            tools = list(TOOLS)
            if state["extra"]:
                tools.append({
                    "name": "extra",
                    "description": "Added later.",
                    "inputSchema": {"type": "object", "properties": {}},
                })
            cursor = (message.get("params") or {}).get("cursor")
            if cursor == "page2":
                result(request_id, {"tools": tools[4:]})
            else:
                result(request_id, {"tools": tools[:4], "nextCursor": "page2"})
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                result(request_id, {"content": [{"type": "text", "text": arguments["text"]}]})
            elif name == "add":
                total = arguments.get("a", 0) + arguments.get("b", 0)
                result(request_id, {
                    "content": [],
                    "structuredContent": {"sum": total},
                })
            elif name == "write_note":
                result(request_id, {"content": [{"type": "text", "text": "ok"}]})
            elif name == "boom":
                error(request_id, -32000, "kaboom")
            elif name == "big":
                result(request_id, {"content": [{"type": "text", "text": "x" * 5000}]})
            elif name == "environ":
                text = "|".join([
                    os.environ.get("MCP_TEST_TOKEN", "<unset>"),
                    os.environ.get("SECRET_TOKEN", "<unset>"),
                    os.getcwd(),
                    state["root"],
                ])
                result(request_id, {"content": [{"type": "text", "text": text}]})
            elif name == "image":
                result(request_id, {"content": [{
                    "type": "image",
                    "data": "aGVsbG8=",
                    "mimeType": "image/png",
                }]})
            elif name == "expose_extra":
                state["extra"] = True
                send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                result(request_id, {"content": [{"type": "text", "text": "exposed"}]})
            elif name == "slow":
                time.sleep(2.0)
                result(request_id, {"content": [{"type": "text", "text": "late"}]})
            else:
                result(request_id, {"content": [{"type": "text", "text": name}]})
        else:
            error(request_id, -32601, "method not found")

main()
"""


class FakeProvider:
    """A provider that calls one tool, then answers."""

    name = "fake"
    model = "fake-model"
    context_window = 100_000
    thinking = "none"

    def __init__(self, tool_name: str, arguments: dict[str, object]):
        self.replies = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    }
                ],
                "finish_reason": "tool_calls",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
            {
                "content": "done",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 20, "completion_tokens": 2},
            },
        ]

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        from minimalcode.provider import ProviderReply, ToolCall

        reply = self.replies.pop(0)
        calls = [
            ToolCall(id=item["id"], name=item["name"], arguments=item["arguments"])
            for item in reply["tool_calls"]
        ]
        if reply["content"] and on_text:
            on_text(reply["content"])
        return ProviderReply(
            content=reply["content"],
            tool_calls=calls,
            finish_reason=reply["finish_reason"],
            usage=reply["usage"],
        )

    def complete_text(self, messages):
        return "summary"


class MCPTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / ".MinimalCode"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.server_script = self.root / "fake_server.py"
        self.server_script.write_text(FAKE_SERVER, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def settings_with(self, servers: dict[str, object], **block: object):
        self.home.mkdir(exist_ok=True)
        document = {"mcp": {"servers": servers, **block}}
        (self.home / "settings.json").write_text(json.dumps(document), encoding="utf-8")
        return load_settings(self.home)

    def stdio_server(self, name: str = "fake", **overrides: object) -> dict:
        config: dict[str, object] = {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(self.server_script)],
        }
        config.update(overrides)
        return {name: config}

    def runtime(self, servers: dict[str, object], **kwargs: object) -> ToolRuntime:
        settings = self.settings_with(servers, **kwargs)
        return ToolRuntime(settings, self.workspace, detect_shell())


class ProtocolTests(unittest.TestCase):
    def test_tool_names_are_provider_safe_and_keep_a_hash(self) -> None:
        self.assertEqual(
            sanitize_tool_name("fake", "read.file"),
            "mcp__fake__read_file",
        )
        name = sanitize_tool_name("fake", "t" * 100)
        self.assertLessEqual(len(name), protocol.MAX_TOOL_NAME)
        self.assertNotEqual(name, sanitize_tool_name("fake", "t" * 99 + "u"))

    def test_schema_conversion_accepts_missing_and_non_object_schemas(self) -> None:
        schema = protocol.tool_schema("fake", {"name": "plain"})
        self.assertEqual(
            schema["function"]["parameters"],
            {"type": "object", "properties": {}},
        )
        schema = protocol.tool_schema(
            "fake",
            {"name": "list", "inputSchema": {"type": "array"}},
        )
        self.assertEqual(schema["function"]["parameters"]["type"], "object")
        self.assertIsNone(protocol.tool_schema("fake", {"description": "no name"}))

    def test_schema_conversion_bounds_the_description(self) -> None:
        schema = protocol.tool_schema(
            "fake",
            {"name": "wordy", "description": "x" * 5000},
        )
        description = schema["function"]["description"]
        self.assertLessEqual(len(description), 1000)
        self.assertTrue(description.endswith("..."))

    def test_read_only_hint_defaults_to_mutating(self) -> None:
        self.assertTrue(
            protocol.tool_read_only({"annotations": {"readOnlyHint": True}})
        )
        self.assertFalse(protocol.tool_read_only({"annotations": {}}))
        self.assertFalse(protocol.tool_read_only({}))

    def test_content_text_renders_every_block_type(self) -> None:
        result = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "data": "AAAA", "mimeType": "image/png"},
                {"type": "resource", "resource": {"uri": "file:///a", "text": "body"}},
                {"type": "resource", "resource": {"uri": "file:///b", "blob": "AAAA"}},
                {"type": "resource_link", "uri": "https://x", "name": "x"},
            ]
        }
        text, clipped = protocol.content_text(result, limit=10_000)
        self.assertIn("hello", text)
        self.assertIn("image/png", text)
        self.assertIn("base64 characters omitted", text)
        self.assertIn("file:///a", text)
        self.assertIn("https://x", text)
        self.assertFalse(clipped)

    def test_content_text_uses_structured_result_and_clips_head_and_tail(
        self,
    ) -> None:
        text, clipped = protocol.content_text(
            {"content": [], "structuredContent": {"sum": 3}},
            limit=1000,
        )
        self.assertIn('"sum": 3', text)
        self.assertFalse(clipped)
        text, clipped = protocol.content_text(
            {"content": [{"type": "text", "text": "h" * 5000}]},
            limit=100,
        )
        self.assertTrue(clipped)
        self.assertLess(len(text), 200)
        self.assertIn("characters omitted", text)


class ConfigTests(MCPTestBase):
    def test_defaults_include_the_servers_block(self) -> None:
        settings = load_settings(self.home)
        self.assertTrue(settings.get("mcp.enabled"))
        self.assertEqual(settings.get("mcp.servers"), {})
        self.assertEqual(settings.get("mcp.max_tools"), 200)

    def test_invalid_server_is_a_config_error(self) -> None:
        cases = {
            "transport": {"transport": "smoke"},
            "name": {"transport": "stdio", "command": "x"},
            "command": {"transport": "stdio"},
            "url": {"transport": "http", "url": "ftp://example.test"},
            "timeout": {"transport": "stdio", "command": "x", "timeout_seconds": 0},
        }
        for label, config in cases.items():
            with self.subTest(label=label):
                servers = {"bad" if label != "name" else "bad name": config}
                with self.assertRaises(ConfigError):
                    self.settings_with(servers)

    def test_env_placeholder_is_retyped_after_expansion(self) -> None:
        with patch.dict(os.environ, {"MCP_TIMEOUT": "45"}):
            settings = self.settings_with(
                self.stdio_server(timeout_seconds="${MCP_TIMEOUT}"),
            )
        self.assertEqual(settings.get("mcp.servers.fake.timeout_seconds"), 45.0)

    def test_tool_filters_and_startup_timeout_are_validated(self) -> None:
        settings = self.settings_with(
            self.stdio_server(
                include_tools=["echo"],
                exclude_tools=["boom"],
                startup_timeout_seconds=5,
            )
        )
        self.assertEqual(
            settings.get("mcp.servers.fake.include_tools"),
            ["echo"],
        )
        self.assertEqual(settings.get("mcp.servers.fake.startup_timeout_seconds"), 5)
        with self.assertRaises(ConfigError):
            self.settings_with(self.stdio_server(include_tools="echo"))

    def test_headers_and_env_are_redacted(self) -> None:
        redacted = redact_config(
            {
                "mcp": {
                    "servers": {
                        "fake": {
                            "headers": {
                                "Authorization": "Bearer secret",
                                "Accept": "application/json",
                            },
                            "env": {"GITHUB_TOKEN": "secret", "PATH": "/bin"},
                        }
                    }
                }
            }
        )
        server = redacted["mcp"]["servers"]["fake"]
        self.assertEqual(server["headers"]["Authorization"], "<redacted>")
        self.assertEqual(server["headers"]["Accept"], "application/json")
        self.assertEqual(server["env"]["GITHUB_TOKEN"], "<redacted>")
        self.assertEqual(server["env"]["PATH"], "/bin")


class StdioClientTests(MCPTestBase):
    def test_client_discovers_paged_tools_and_calls_them(self) -> None:
        settings = self.settings_with(self.stdio_server())
        manager = MCPManager.from_settings(settings, self.workspace)
        try:
            manager.start()
            names = manager.tool_names
            self.assertIn("mcp__fake__echo", names)
            self.assertIn("mcp__fake__slow", names)
            summary = manager.summary()
            self.assertEqual(summary["servers"][0]["state"], "ready")
            self.assertEqual(summary["servers"][0]["server"], "fake-mcp")
            self.assertEqual(summary["servers"][0]["protocol_version"], "2025-06-18")

            echoed = manager.call("mcp__fake__echo", {"text": "hi"})
            self.assertEqual(echoed["status"], "success")
            self.assertEqual(echoed["content"], "hi")

            added = manager.call("mcp__fake__add", {"a": 1, "b": 2})
            self.assertIn('"sum": 3', added["content"])

            image = manager.call("mcp__fake__image", {})
            self.assertIn("image/png", image["content"])
            self.assertNotIn("aGVsbG8=", image["content"])
        finally:
            manager.close()

    def test_include_and_exclude_filters_shape_the_tool_list(self) -> None:
        settings = self.settings_with(
            self.stdio_server(
                include_tools=["echo", "boom"],
                exclude_tools=["boom"],
            )
        )
        manager = MCPManager.from_settings(settings, self.workspace)
        try:
            manager.start()
            self.assertEqual(manager.tool_names, frozenset({"mcp__fake__echo"}))
        finally:
            manager.close()

    def test_server_error_becomes_a_tool_error(self) -> None:
        settings = self.settings_with(self.stdio_server())
        manager = MCPManager.from_settings(settings, self.workspace)
        try:
            manager.start()
            with self.assertRaises(ToolError) as caught:
                manager.call("mcp__fake__boom", {})
            self.assertIn("kaboom", str(caught.exception))
        finally:
            manager.close()

    def test_timeout_is_bounded_and_reported(self) -> None:
        settings = self.settings_with(self.stdio_server(timeout_seconds=1))
        manager = MCPManager.from_settings(settings, self.workspace)
        try:
            manager.start()
            started = time.monotonic()
            with self.assertRaises(ToolError) as caught:
                manager.call("mcp__fake__slow", {})
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("timed out", str(caught.exception))
        finally:
            manager.close()

    def test_cwd_env_and_roots_are_workspace_bound(self) -> None:
        subdir = self.workspace / "sub"
        subdir.mkdir()
        servers = self.stdio_server(cwd="sub", env={"MCP_TEST_TOKEN": "visible"})
        with patch.dict(os.environ, {"SECRET_TOKEN": "hidden"}):
            runtime = self.runtime(servers)
        try:
            result = runtime.dispatch("mcp__fake__environ", {})
        finally:
            runtime.mcp.close()
        token, secret, cwd, root = result["content"].split("|")
        self.assertEqual(token, "visible")
        self.assertEqual(secret, "<unset>")
        self.assertEqual(Path(cwd).resolve(), subdir.resolve())
        self.assertEqual(root, self.workspace.resolve().as_uri())

    def test_tool_name_collision_is_disambiguated(self) -> None:
        runtime = self.runtime(self.stdio_server())
        try:
            names = runtime.mcp.tool_names
        finally:
            runtime.mcp.close()
        dotted = [name for name in names if name.startswith("mcp__fake__dot")]
        self.assertEqual(len(dotted), 2)

    def test_missing_command_records_an_error_and_keeps_other_tools(self) -> None:
        servers = {"broken": {"transport": "stdio", "command": "definitely-missing"}}
        runtime = self.runtime(servers)
        try:
            summary = runtime.mcp.summary()
            self.assertEqual(summary["servers"][0]["state"], "error")
            self.assertIn("error", summary["servers"][0])
            self.assertTrue(runtime.allowed_tools)
            with self.assertRaises(ToolError):
                runtime.read("missing.txt", 1, 1)
        finally:
            runtime.mcp.close()

    def test_refresh_discovers_tools_announced_at_runtime(self) -> None:
        runtime = self.runtime(self.stdio_server())
        try:
            self.assertNotIn("mcp__fake__extra", runtime.mcp.tool_names)
            runtime.dispatch("mcp__fake__expose_extra", {})
            added = runtime.mcp.refresh()
            self.assertIn("mcp__fake__extra", added)
            self.assertIn("mcp__fake__extra", runtime.mcp.tool_names)
        finally:
            runtime.mcp.close()


class RuntimeIntegrationTests(MCPTestBase):
    def test_dispatch_schemas_and_read_only_policy(self) -> None:
        runtime = self.runtime(self.stdio_server())
        try:
            schemas = available_tool_schemas(
                mcp_schemas=runtime.mcp.schemas(),
                allowed=runtime.allowed_tools,
            )
            names = {schema["function"]["name"] for schema in schemas}
            self.assertIn("mcp__fake__echo", names)
            self.assertIn("read", names)

            result = runtime.dispatch("mcp__fake__echo", {"text": "hello"})
            self.assertEqual(result["content"], "hello")

            with self.assertRaises(ToolError) as caught:
                runtime.dispatch("mcp__fake__missing", {})
            self.assertIn("unknown tool", str(caught.exception))
        finally:
            runtime.mcp.close()

    def test_tools_allow_accepts_a_server_wildcard(self) -> None:
        settings = self.settings_with(self.stdio_server())
        settings.data["tools"]["allow"] = ["read", "mcp__fake"]
        runtime = ToolRuntime(settings, self.workspace, detect_shell())
        try:
            self.assertIn("mcp__fake__echo", runtime.allowed_tools)
            self.assertNotIn("find", runtime.allowed_tools)
            self.assertEqual(
                runtime.dispatch("mcp__fake__echo", {"text": "ok"})["status"],
                "success",
            )
        finally:
            runtime.mcp.close()

    def test_tools_allow_reports_unknown_mcp_tool_with_a_hint(self) -> None:
        settings = self.settings_with(self.stdio_server())
        settings.data["tools"]["allow"] = ["mcp__fake__typo"]
        with self.assertRaises(ConfigError) as caught:
            ToolRuntime(settings, self.workspace, detect_shell())
        message = str(caught.exception)
        self.assertIn("mcp__fake__typo", message)
        self.assertIn("mcp__fake__echo", message)

    def test_read_only_allows_annotated_tools_only(self) -> None:
        settings = self.settings_with(self.stdio_server())
        runtime = ToolRuntime(
            settings,
            self.workspace,
            detect_shell(),
            read_only=True,
        )
        try:
            self.assertIn("mcp__fake__echo", runtime.allowed_tools)
            self.assertNotIn("mcp__fake__write_note", runtime.allowed_tools)
            with self.assertRaises(ToolError) as caught:
                runtime.dispatch("mcp__fake__write_note", {})
            self.assertIn("--read-only", str(caught.exception))
        finally:
            runtime.mcp.close()

    def test_agent_loop_calls_an_mcp_tool_and_advertises_it(self) -> None:
        settings = self.settings_with(self.stdio_server())
        shell = detect_shell()
        runtime = ToolRuntime(settings, self.workspace, shell)
        session = SessionStore(settings).create(
            self.workspace,
            provider="fake",
            model="fake-model",
            save=False,
        )
        agent = Agent(
            settings=settings,
            provider=FakeProvider("mcp__fake__echo", {"text": "from mcp"}),
            session=session,
            tool_runtime=runtime,
            shell=shell,
            quiet=True,
            stream_output=False,
        )
        try:
            advertised = {schema["function"]["name"] for schema in agent.tool_schemas}
            self.assertIn("mcp__fake__echo", advertised)
            result = agent.run("use the echo tool")
            self.assertEqual(result.text, "done")
            tool_messages = [
                message for message in session.messages if message["role"] == "tool"
            ]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("from mcp", tool_messages[0]["content"])
            self.assertIn("mcp", agent.status())
        finally:
            runtime.mcp.close()

    def test_new_runtime_can_share_the_manager(self) -> None:
        settings = self.settings_with(self.stdio_server())
        runtime = ToolRuntime(settings, self.workspace, detect_shell())
        try:
            shared = ToolRuntime(
                settings,
                self.workspace,
                detect_shell(),
                mcp_manager=runtime.mcp,
            )
            self.assertIs(shared.mcp, runtime.mcp)
            self.assertEqual(
                shared.dispatch("mcp__fake__echo", {"text": "shared"})["content"],
                "shared",
            )
        finally:
            runtime.mcp.close()


class TransportTests(unittest.TestCase):
    def test_stdio_environment_keeps_a_safe_baseline_only(self) -> None:
        with patch.dict(os.environ, {"SECRET_TOKEN": "hidden", "PATH": "/bin"}):
            env = stdio_environment({"MCP_TEST_TOKEN": "visible"})
            inherited = stdio_environment(inherit=True)
        self.assertEqual(env["MCP_TEST_TOKEN"], "visible")
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertIn("PATH", env)
        self.assertEqual(inherited["SECRET_TOKEN"], "hidden")

    def test_payload_messages_accept_objects_batches_and_reject_junk(self) -> None:
        one = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(len(_payload_messages(one)), 1)
        self.assertEqual(len(_payload_messages(f"[{one},{one}]")), 2)
        with self.assertRaises(MCPError):
            _payload_messages("not json")
        with self.assertRaises(MCPError):
            _payload_messages('{"id": 1}')

    def test_http_transport_posts_session_headers_and_reads_sse(self) -> None:
        seen: list[tuple[dict, dict]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payload = json.loads(request.content)
            seen.append((payload, dict(request.headers)))
            if payload["method"] == "initialize":
                return httpx2.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"protocolVersion": "2025-06-18"},
                    },
                    headers={"mcp-session-id": "session-1"},
                )
            if payload["method"] == "tools/list":
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"tools": []},
                    }
                )
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=f"event: message\ndata: {body}\n\n".encode(),
                )
            return httpx2.Response(202)

        transport = HTTPTransport(
            "https://example.test/mcp",
            {"X-Test": "1"},
            timeout=5,
        )
        transport._client = httpx2.Client(transport=httpx2.MockTransport(handler))
        try:
            transport.send(protocol.request_message(1, "initialize"))
            self.assertEqual(
                transport.receive(1),
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
            transport.set_protocol_version("2025-06-18")
            transport.send(protocol.request_message(2, "tools/list"))
            self.assertEqual(transport.receive(1)["id"], 2)
        finally:
            transport.close()

        _, initialize_headers = seen[0]
        self.assertEqual(initialize_headers.get("x-test"), "1")
        self.assertIn("application/json", initialize_headers.get("accept", ""))
        _, list_headers = seen[1]
        self.assertEqual(list_headers.get("mcp-session-id"), "session-1")
        self.assertEqual(list_headers.get("mcp-protocol-version"), "2025-06-18")

    def test_http_transport_reports_status_and_body(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(400, text="bad request")

        transport = HTTPTransport("https://example.test/mcp", {}, timeout=5)
        transport._client = httpx2.Client(transport=httpx2.MockTransport(handler))
        try:
            with self.assertRaises(MCPError) as caught:
                transport.send(protocol.request_message(1, "tools/list"))
            self.assertIn("400", str(caught.exception))
            self.assertIn("bad request", str(caught.exception))
        finally:
            transport.close()

    def test_sse_transport_reads_the_endpoint_event(self) -> None:
        message = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

        class Stream:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_lines(self):
                return iter(
                    [
                        "event: endpoint",
                        "data: /messages?session=1",
                        "",
                        f"data: {message}",
                        "",
                    ]
                )

        class Client:
            def __init__(self) -> None:
                self.posted: list[tuple[str, dict]] = []

            def stream(self, method, url, headers=None, timeout=None):
                return Stream()

            def post(self, url, content=None, headers=None, timeout=None):
                self.posted.append((url, json.loads(content)))
                return httpx2.Response(202)

            def close(self) -> None:
                return None

        transport = SSETransport(
            "https://example.test/sse",
            {},
            timeout=5,
            startup_timeout=5,
        )
        fake = Client()
        transport._client = fake
        transport._read_stream()
        self.assertEqual(
            transport.receive(0.1),
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
        )
        transport.send(protocol.request_message(2, "tools/list"))
        self.assertEqual(fake.posted[0][0], "https://example.test/messages?session=1")
        self.assertEqual(fake.posted[0][1]["method"], "tools/list")


class ClientUnitTests(unittest.TestCase):
    def test_config_falls_back_to_the_shared_block(self) -> None:
        config = MCPServerConfig.from_settings(
            "fake",
            {"command": "x", "args": ["a"]},
            {
                "timeout_seconds": 7,
                "startup_timeout_seconds": 3,
                "max_tools": 10,
            },
        )
        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.args, ("a",))
        self.assertEqual(config.timeout_seconds, 7.0)
        self.assertEqual(config.startup_timeout_seconds, 3.0)

    def test_starting_without_a_transport_is_an_error(self) -> None:
        client = MCPClient(
            MCPServerConfig.from_settings("fake", {"command": "x"}),
            workspace=Path.cwd(),
        )
        with self.assertRaises(MCPError):
            client._request("tools/list")


if __name__ == "__main__":
    unittest.main()
