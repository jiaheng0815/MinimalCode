from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import httpx2

from minimalcode import __version__
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
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            if request.url.path.endswith("/search"):
                body = json.loads(request.content)
                self.assertEqual(body["query"], "minimalcode")
                self.assertEqual(body["count"], 3)
                return httpx2.Response(
                    200,
                    json={
                        "results": [{"title": "Result", "url": "https://example.com"}]
                    },
                )
            body = json.loads(request.content)
            self.assertEqual(body["url"], "https://example.com")
            return httpx2.Response(
                200,
                json={"title": "Example", "content": "Page body"},
            )

        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(handler),
        )
        try:
            self.assertTrue(client.enabled)
            result = client.search("minimalcode", count=3)
            self.assertEqual(result["results"][0]["title"], "Result")
            self.assertEqual(
                client.fetch("https://example.com")["content"], "Page body"
            )
        finally:
            client.close()
        self.assertEqual(
            [request.url.path for request in requests],
            ["/v1/tools/search", "/v1/tools/fetch"],
        )

    def test_search_retries_a_transient_tls_failure(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx2.ConnectError(
                    "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation "
                    "of protocol"
                )
            return httpx2.Response(
                200,
                json={
                    "results": [{"title": "Recovered", "url": "https://example.com"}]
                },
            )

        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(handler),
        )
        try:
            with mock.patch.object(time, "sleep") as sleep:
                result = client.search("minimalcode")
        finally:
            client.close()

        self.assertEqual(attempts, 2)
        self.assertEqual(result["results"][0]["title"], "Recovered")
        sleep.assert_called_once()

    def test_search_stops_after_the_connection_retry_budget(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            raise httpx2.ConnectError("connection reset")

        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(handler),
        )
        try:
            with (
                mock.patch.object(time, "sleep"),
                self.assertRaisesRegex(ToolError, "after 11 attempts"),
            ):
                client.search("minimalcode")
        finally:
            client.close()

        self.assertEqual(attempts, 11)

    def test_disabled_provider_hides_and_blocks_tools(self) -> None:
        disabled = ProviderWebClient("custom", _web_config(enabled=False))
        enabled = ProviderWebClient("custom", _web_config())
        try:
            self.assertFalse(disabled.enabled)
            with self.assertRaisesRegex(ToolError, "not enabled"):
                disabled.search("blocked")

            enabled_schemas = available_tool_schemas(enabled)
            visible = {tool["function"]["name"] for tool in enabled_schemas}
            hidden = {tool["function"]["name"] for tool in available_tool_schemas()}
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

    def test_fetch_rejects_malformed_urls(self) -> None:
        client = ProviderWebClient("custom", _web_config())
        try:
            with self.assertRaisesRegex(ToolError, "HTTP or HTTPS"):
                client.fetch("http://[::1")
            with self.assertRaisesRegex(ToolError, "HTTP or HTTPS"):
                client.fetch("http://example.com:bad/")
            with self.assertRaisesRegex(ToolError, "integer"):
                client.search("query", count="3")  # type: ignore[arg-type]
        finally:
            client.close()

    def test_fetch_rejects_non_string_response_fields(self) -> None:
        for payload in (
            {"title": ["invalid"], "content": "body"},
            {"title": "title", "content": {"invalid": True}},
        ):
            with self.subTest(payload=payload):
                client = ProviderWebClient(
                    "custom",
                    _web_config(),
                    transport=httpx2.MockTransport(
                        lambda request, payload=payload: httpx2.Response(
                            200, json=payload
                        )
                    ),
                )
                try:
                    with self.assertRaisesRegex(ToolError, "must be a string"):
                        client.fetch("https://example.com")
                finally:
                    client.close()

    def test_non_finite_web_settings_are_rejected(self) -> None:
        for key in ("default_count", "timeout_seconds"):
            with self.subTest(key=key):
                config = _web_config()
                config["web_tools"][key] = float("inf")
                with self.assertRaisesRegex(ToolError, "web_tools"):
                    ProviderWebClient("custom", config)

    def test_search_bounds_provider_output(self) -> None:
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(
                200,
                json={
                    "results": [
                        "junk",
                        {"title": "long", "content": "x" * 250},
                        {"title": "short", "content": "ok"},
                    ]
                },
            )

        client = ProviderWebClient(
            "custom", _web_config(), transport=httpx2.MockTransport(handler)
        )
        try:
            result = client.search("query")
        finally:
            client.close()

        self.assertEqual(
            [item["title"] for item in result["results"]], ["long", "short"]
        )
        content = result["results"][0]["content"]
        self.assertTrue(content.endswith("<content truncated>"))
        self.assertLessEqual(len(content), 100 + len("\n<content truncated>"))
        self.assertEqual(
            requests[0].headers["user-agent"], f"MinimalCode/{__version__}"
        )

    def test_search_limits_the_number_of_results(self) -> None:
        payload = {"results": [{"title": str(index)} for index in range(20)]}
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload)
            ),
        )
        try:
            result = client.search("query")
        finally:
            client.close()
        self.assertEqual(len(result["results"]), 5)

    def test_search_respects_the_requested_default_count(self) -> None:
        payload = {"results": [{"title": str(index)} for index in range(5)]}
        config = _web_config()
        config["web_tools"]["default_count"] = 2
        client = ProviderWebClient(
            "custom",
            config,
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload)
            ),
        )
        try:
            result = client.search("query")
        finally:
            client.close()
        self.assertEqual([item["title"] for item in result["results"]], ["0", "1"])

    def test_search_bounds_every_string_field(self) -> None:
        payload = {
            "results": [
                {
                    "title": "t" * 250,
                    "url": "https://example.com/" + "u" * 250,
                    "nested": {"text": "n" * 250},
                }
            ]
        }
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload)
            ),
        )
        try:
            result = client.search("query")
        finally:
            client.close()
        item = result["results"][0]
        self.assertLessEqual(len(item["title"]), 100 + len("\n<content truncated>"))
        self.assertLessEqual(len(item["url"]), 100 + len("\n<content truncated>"))
        self.assertLessEqual(
            len(item["nested"]["text"]), 100 + len("\n<content truncated>")
        )

    def test_fetch_accepts_null_title(self) -> None:
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
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

    def test_fetch_truncates_a_long_title(self) -> None:
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    json={"title": "t" * 250, "content": "body"},
                )
            ),
        )
        try:
            result = client.fetch("https://example.com")
        finally:
            client.close()
        self.assertTrue(result["truncated"])
        self.assertTrue(result["title"].endswith("<title truncated>"))

    def test_fetch_truncates_long_content(self) -> None:
        client = ProviderWebClient(
            "custom",
            _web_config(),
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
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
