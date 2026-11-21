"""Agent-vs-Chatbot web routing for DeepSeek providers.

DeepSeek documents ``deepseek_web_search`` / ``deepseek_read_url`` for Chatbot
conversations. MinimalCode supplies client-side function tools, so its presets
use the standalone Search/Fetch APIs. These tests pin the precedence down: an
enabled ``web_tools`` block keeps the local pair visible, while an explicit
server-only configuration still bypasses the standalone client.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx2

from minimalcode.config import DEFAULT_SETTINGS, load_settings
from minimalcode.deepseek import declarations, features, migration_notice
from minimalcode.errors import ToolError
from minimalcode.provider import OpenAICompatibleProvider
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime, available_tool_schemas
from minimalcode.tools.schemas import build_server_tool_schemas
from minimalcode.web import ProviderWebClient

_DEEPSEEK = DEFAULT_SETTINGS["providers"]["deepseek"]
_SEARCH_REQUEST = "/v1/tools/search"


def _web_block(*, enabled: bool) -> dict[str, Any]:
    """A complete ``web_tools`` block; the client rejects a partial one."""
    return {
        "enabled": enabled,
        "base_url": "https://api.deepseek.com/v1/tools",
        "search_path": "search",
        "fetch_path": "fetch",
        "timeout_seconds": 30,
        "default_count": 5,
        "max_count": 5,
        "max_content_chars": 100,
        "headers": {},
    }


def _wire_config(**overrides: Any) -> dict[str, Any]:
    """A DeepSeek provider config that never reaches the network."""
    config: dict[str, Any] = {
        "protocol": "openai_chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret",
        "stream": False,
        "server_tools": {"enabled": True, "web_search": True, "read_url": True},
        "web_tools": _web_block(enabled=False),
    }
    config.update(overrides)
    return config


def _reply_with_search() -> dict[str, Any]:
    """A Chat reply in which the service searched on the model's behalf."""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "冰岛人口约 39.4 万。",
                    "server_tool_calls": [
                        {
                            "index": 0,
                            "reasoning_content": "",
                            "calls": [
                                {
                                    "index": 0,
                                    "id": "srvtoolu_s_1",
                                    "type": "deepseek_web_search",
                                    "kind": "search",
                                    "action": {
                                        "type": "search",
                                        "queries": ["Iceland"],
                                    },
                                    "status": "completed",
                                    "sources": [
                                        {
                                            "type": "url",
                                            "url": "https://example.com/a",
                                            "title": "Statistics Iceland",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            }
        ]
    }


class AllowListTests(unittest.TestCase):
    def test_allowing_only_web_search_does_not_look_like_a_typo(self) -> None:
        """`tools.allow: [web_search]` stays valid even with server tools on.

        The name is still a dispatcher tool, so the allowlist resolves without a
        configuration error; the local schema is simply replaced by the
        server-side one, which needs no entry in `tools.allow`.
        """
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            workspace = Path(temp) / "project"
            workspace.mkdir()
            home.mkdir()
            (home / "settings.json").write_text(
                json.dumps({"tools": {"allow": ["web_search", "read"]}}),
                encoding="utf-8",
            )
            settings = load_settings(home)
            client = ProviderWebClient("deepseek", _wire_config())
            try:
                runtime = ToolRuntime(
                    settings,
                    workspace,
                    detect_shell(),
                    web_client=client,
                    server_tools=("deepseek_web_search", "deepseek_read_url"),
                )
                allowed = runtime.allowed_tools
                names = {
                    tool["function"]["name"]
                    for tool in available_tool_schemas(
                        runtime.web_client,
                        allowed=allowed,
                        server_tools=runtime.server_tools,
                    )
                }
            finally:
                client.close()
        self.assertEqual(allowed, frozenset({"web_search", "read"}))
        self.assertEqual(names, {"read"})


class DefaultConfigTests(unittest.TestCase):
    def test_deepseek_defaults_use_the_agent_search_path(self) -> None:
        # DeepSeek documents the server-side tools for chatbot conversations.
        # MinimalCode always supplies function tools, so its presets use the
        # standalone Search/Fetch API and keep the conversation-only path off.
        self.assertTrue(_DEEPSEEK["web_tools"]["enabled"])
        server = _DEEPSEEK["server_tools"]
        self.assertFalse(server["enabled"])
        self.assertTrue(server["web_search"])
        self.assertTrue(server["read_url"])

        capability = features(_DEEPSEEK)
        self.assertFalse(capability["web_search"])
        self.assertFalse(capability["read_url"])
        self.assertTrue(capability["search_api"])
        self.assertTrue(capability["fetch_api"])

    def test_standalone_web_tools_override_legacy_server_tools(self) -> None:
        """An existing settings file with both paths must keep working.

        Older releases wrote ``server_tools.enabled: true`` into the settings
        file. Deep merging the new preset must not make that stale choice hide
        the working client-side tools again.
        """
        config = _wire_config(web_tools=_web_block(enabled=True))
        self.assertEqual(declarations(config), [])
        self.assertEqual(build_server_tool_schemas(config), [])

        capability = features(config)
        self.assertFalse(capability["web_search"])
        self.assertFalse(capability["read_url"])
        self.assertTrue(capability["search_api"])
        self.assertTrue(capability["fetch_api"])

    def test_declarations_are_tool_entries_not_function_schemas(self) -> None:
        config = _wire_config(web_tools=_web_block(enabled=False))
        self.assertEqual(
            declarations(config),
            [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}],
        )
        self.assertEqual(
            build_server_tool_schemas(config),
            [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}],
        )

    def test_a_provider_without_server_tools_declares_nothing(self) -> None:
        for name in ("openrouter", "groq"):
            config = DEFAULT_SETTINGS["providers"][name]
            self.assertEqual(declarations(config), [], name)
            self.assertFalse(features(config)["web_search"], name)


class SchemaSelectionTests(unittest.TestCase):
    def _names(self, config: dict[str, Any]) -> set[str]:
        client = ProviderWebClient("deepseek", config)
        try:
            return {
                tool["function"]["name"]
                for tool in available_tool_schemas(
                    client,
                    server_tools=tuple(
                        entry["type"] for entry in build_server_tool_schemas(config)
                    ),
                )
            }
        finally:
            client.close()

    def test_server_tools_hide_the_local_web_pair(self) -> None:
        names = self._names(_wire_config())
        self.assertNotIn("web_search", names)
        self.assertNotIn("web_fetch", names)
        self.assertIn("read", names)
        self.assertIn("shell", names)

    def test_the_agent_default_advertises_the_local_web_pair(self) -> None:
        config = dict(_DEEPSEEK)
        config["api_key"] = "secret"
        names = self._names(config)
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_explicitly_enabling_web_tools_brings_the_pair_back(self) -> None:
        config = _wire_config(
            server_tools={"enabled": False},
            web_tools=_web_block(enabled=True),
        )
        self.assertEqual(build_server_tool_schemas(config), [])
        names = self._names(config)
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_an_openai_chat_provider_keeps_its_local_tools(self) -> None:
        config = {
            "protocol": "openai_chat",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "secret",
            "web_tools": _web_block(enabled=True),
        }
        self.assertEqual(build_server_tool_schemas(config), [])
        self.assertIn("web_search", self._names(config))


class NoLocalRequestTests(unittest.TestCase):
    def test_a_server_side_search_never_posts_to_the_search_api(self) -> None:
        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request.url.path)
            return httpx2.Response(200, json=_reply_with_search())

        provider = OpenAICompatibleProvider(
            name="deepseek",
            config=_wire_config(),
            model="deepseek-flash",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        self.addCleanup(provider.close)
        reply = provider.stream_chat([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(reply.server_tools[0]["kind"], "search")
        self.assertEqual(seen, ["/v1/chat/completions"])
        self.assertNotIn(_SEARCH_REQUEST, seen)

    def test_dispatch_refuses_a_replayed_local_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            settings = load_settings(workspace / ".MinimalCode")
            # The standalone client is on here to prove the refusal is about the
            # server-side tool, not about a missing endpoint.
            client = ProviderWebClient(
                "deepseek", _wire_config(web_tools=_web_block(enabled=True))
            )
            try:
                runtime = ToolRuntime(
                    settings,
                    workspace,
                    detect_shell(),
                    web_client=client,
                    server_tools=("deepseek_web_search", "deepseek_read_url"),
                )
                self.assertTrue(runtime.web_enabled)
                for name, arguments in (
                    ("web_search", {"query": "iceland"}),
                    ("web_fetch", {"url": "https://example.com"}),
                ):
                    with (
                        self.subTest(tool=name),
                        self.assertRaisesRegex(ToolError, "server-side tool"),
                    ):
                        runtime.dispatch(name, arguments)
            finally:
                client.close()

    def test_without_server_tools_the_local_call_still_works(self) -> None:
        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request.url.path)
            return httpx2.Response(
                200, json={"results": [{"title": "R", "url": "https://example.com"}]}
            )

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            settings = load_settings(workspace / ".MinimalCode")
            client = ProviderWebClient(
                "custom",
                {
                    "base_url": "https://provider.test",
                    "api_key": "secret",
                    "web_tools": _web_block(enabled=True),
                },
                transport=httpx2.MockTransport(handler),
            )
            try:
                runtime = ToolRuntime(
                    settings, workspace, detect_shell(), web_client=client
                )
                result = runtime.dispatch("web_search", {"query": "x", "count": 1})
            finally:
                client.close()
        self.assertEqual(result["results"][0]["title"], "R")
        self.assertEqual(seen, [_SEARCH_REQUEST])


class MigrationNoticeTests(unittest.TestCase):
    def test_a_server_only_agent_configuration_is_reported(self) -> None:
        notice = migration_notice(
            _wire_config(server_tools={"enabled": True}, web_tools={"enabled": False})
        )
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertIn("chatbot", notice)
        self.assertIn("web_tools", notice)
        self.assertIn("server_tools.enabled=false", notice)

    def test_the_new_default_is_silent(self) -> None:
        self.assertIsNone(migration_notice(_DEEPSEEK))

    def test_a_local_and_server_mix_is_silent(self) -> None:
        self.assertIsNone(
            migration_notice(_wire_config(web_tools=_web_block(enabled=True)))
        )

    def test_a_foreign_endpoint_is_silent(self) -> None:
        # `web_tools` on a non-DeepSeek endpoint is that vendor's own client, so
        # there is nothing to migrate.
        self.assertIsNone(
            migration_notice(
                {
                    "protocol": "openai_chat",
                    "base_url": "https://openrouter.ai/api/v1",
                    "web_tools": {"enabled": True},
                }
            )
        )


class ChatPayloadTests(unittest.TestCase):
    def test_the_agent_path_does_not_attach_server_tool_declarations(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            payloads.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ]
                },
            )

        provider = OpenAICompatibleProvider(
            name="deepseek",
            config=_wire_config(web_tools=_web_block(enabled=True)),
            model="deepseek-flash",
            thinking="high",
            transport=httpx2.MockTransport(handler),
        )
        self.addCleanup(provider.close)
        provider.stream_chat(
            [{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "read", "parameters": {"type": "object"}},
                }
            ],
        )
        self.assertEqual(
            [tool for tool in payloads[0]["tools"] if "function" not in tool], []
        )


class WarningTests(unittest.TestCase):
    """The startup notice: loud for the old path, silent for the new one."""

    def _capture(self, config: dict[str, Any], *, quiet: bool = False) -> str:
        import io

        from minimalcode.cli import _warn_web_routing

        stream = io.StringIO()
        _warn_web_routing(config, quiet=quiet, stream=stream)
        return stream.getvalue()

    def test_a_server_only_agent_configuration_warns_once(self) -> None:
        text = self._capture(
            _wire_config(server_tools={"enabled": True}, web_tools={"enabled": False})
        )
        self.assertIn("chatbot", text)
        self.assertIn("web_tools", text)

    def test_the_new_default_and_machine_output_stay_silent(self) -> None:
        self.assertEqual(self._capture(_DEEPSEEK), "")
        old = _wire_config(server_tools={"enabled": True}, web_tools={"enabled": False})
        self.assertEqual(self._capture(old, quiet=True), "")
