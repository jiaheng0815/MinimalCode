from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from minimalcode.config import load_settings
from minimalcode.errors import ToolError
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime, available_tool_schemas
from minimalcode.web import DeepSeekWebClient


class WebToolTests(unittest.TestCase):
    def test_deepseek_search_and_fetch(self) -> None:
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

        client = DeepSeekWebClient(
            "deepseek",
            {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "secret",
            },
            transport=httpx.MockTransport(handler),
        )
        try:
            self.assertTrue(client.enabled)
            self.assertEqual(client.search("minimalcode", count=3)["results"][0]["title"], "Result")
            self.assertEqual(client.fetch("https://example.com")["content"], "Page body")
        finally:
            client.close()
        self.assertEqual(
            [request.url.path for request in requests],
            ["/v1/tools/search", "/v1/tools/fetch"],
        )

    def test_non_deepseek_is_hidden_and_blocked(self) -> None:
        client = DeepSeekWebClient(
            "openai",
            {
                "base_url": "https://api.openai.com/v1",
                "api_key": "secret",
            },
        )
        try:
            self.assertFalse(client.enabled)
            with self.assertRaisesRegex(ToolError, "only available"):
                client.search("blocked")
        finally:
            client.close()

        visible = {
            tool["function"]["name"] for tool in available_tool_schemas(web_enabled=True)
        }
        hidden = {
            tool["function"]["name"] for tool in available_tool_schemas(web_enabled=False)
        }
        self.assertIn("web_search", visible)
        self.assertIn("web_fetch", visible)
        self.assertNotIn("web_search", hidden)
        self.assertNotIn("web_fetch", hidden)

    def test_tool_runtime_only_dispatches_web_tools_for_deepseek(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            settings = load_settings(workspace / ".MinimalCode")
            blocked = DeepSeekWebClient(
                "custom",
                {
                    "base_url": "https://provider.test/v1",
                    "api_key": "secret",
                },
            )
            try:
                runtime = ToolRuntime(
                    settings,
                    workspace,
                    detect_shell(),
                    web_client=blocked,
                )
                self.assertFalse(runtime.web_enabled)
                with self.assertRaisesRegex(ToolError, "only available"):
                    runtime.dispatch("web_search", {"query": "blocked"})
            finally:
                blocked.close()


if __name__ == "__main__":
    unittest.main()
