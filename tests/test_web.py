from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from minimalcode.config import load_settings
from minimalcode.errors import ToolError
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime, available_tool_schemas
from minimalcode.web import ProviderWebClient


def _web_config(
    *,
    enabled: bool = True,
    base_url: str = "https://provider.test/v1/tools",
) -> dict[str, Any]:
    return {
        "base_url": base_url,
        "api_key": "secret",
        "web_tools": {
            "enabled": enabled,
            "base_url": base_url,
            "search_path": "search",
            "fetch_path": "fetch",
            "timeout_seconds": 10,
            "default_count": 5,
            "max_count": 5,
            "max_content_chars": 100,
            "headers": {},
        },
    }


class WebToolTests(unittest.TestCase):
    def test_configured_search_and_fetch(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            if request.url.path.endswith("/search"):
                body = json.loads(request.content)
                self.assertEqual(body["query"], "minimalcode")
                self.assertEqual(body["count"], 3)
                return httpx.Response(
                    200,
                    json={"results": [{"title": "Result", "url": "https://example.com"}]},
                )
            body = json.loads(request.content)
            self.assertEqual(body["url"], "https://example.com")
            return httpx.Response(
                200,
                json={"title": "Example", "content": "Page body"},
            )

        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx.MockTransport(handler),
        )
        try:
            self.assertTrue(client.enabled)
            result = client.search("minimalcode", count=3)
            self.assertEqual(result["results"][0]["title"], "Result")
            self.assertEqual(client.fetch("https://example.com")["content"], "Page body")
        finally:
            client.close()
        self.assertEqual(
            [request.url.path for request in requests],
            ["/v1/tools/search", "/v1/tools/fetch"],
        )

    def test_disabled_provider_hides_and_blocks_tools(self) -> None:
        disabled = ProviderWebClient("custom", _web_config(enabled=False))
        enabled = ProviderWebClient("custom", _web_config())
        try:
            self.assertFalse(disabled.enabled)
            with self.assertRaisesRegex(ToolError, "not enabled"):
                disabled.search("blocked")

            enabled_schemas = available_tool_schemas(enabled)
            visible = {
                tool["function"]["name"] for tool in enabled_schemas
            }
            hidden = {
                tool["function"]["name"] for tool in available_tool_schemas()
            }
            count_rule = next(
                tool
                for tool in enabled_schemas
                if tool["function"]["name"] == "web_search"
            )["function"]["parameters"]["properties"]["count"]
            self.assertEqual(count_rule["maximum"], 5)
            self.assertIn("web_search", visible)
            self.assertIn("web_fetch", visible)
            self.assertNotIn("web_search", hidden)
            self.assertNotIn("web_fetch", hidden)
        finally:
            disabled.close()
            enabled.close()

    def test_tool_runtime_blocks_disabled_web_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            settings = load_settings(workspace / ".MinimalCode")
            blocked = ProviderWebClient("custom", _web_config(enabled=False))
            try:
                runtime = ToolRuntime(
                    settings,
                    workspace,
                    detect_shell(),
                    web_client=blocked,
                )
                self.assertFalse(runtime.web_enabled)
                with self.assertRaisesRegex(ToolError, "not enabled"):
                    runtime.dispatch("web_search", {"query": "blocked"})
            finally:
                blocked.close()

    def test_fetch_rejects_non_string_response_fields(self) -> None:
        for payload in (
            {"title": ["invalid"], "content": "body"},
            {"title": "title", "content": {"invalid": True}},
        ):
            with self.subTest(payload=payload):
                client = ProviderWebClient(
                    "custom",
                    _web_config(),
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                )
                try:
                    with self.assertRaisesRegex(ToolError, "must be a string"):
                        client.fetch("https://example.com")
                finally:
                    client.close()

    def test_fetch_accepts_null_title(self) -> None:
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"title": None, "content": "body"},
                )
            ),
        )
        try:
            result = client.fetch("https://example.com")
        finally:
            client.close()
        self.assertEqual(result["title"], "")
        self.assertEqual(result["content"], "body")

    def test_fetch_truncates_long_content(self) -> None:
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"title": "Example", "content": "x" * 150},
                )
            ),
        )
        try:
            result = client.fetch("https://example.com")
        finally:
            client.close()
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["content"]), 100 + len("\n<content truncated>"))

    def test_default_deepseek_web_settings_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = load_settings(Path(temp) / ".MinimalCode")
        _, provider = settings.provider_config("deepseek")
        web = provider["web_tools"]
        self.assertTrue(web["enabled"])
        self.assertEqual(web["base_url"], "https://api.deepseek.com/v1/tools")
        self.assertEqual(web["search_path"], "search")


if __name__ == "__main__":
    unittest.main()
