from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import httpx2

from minimalcode.config import load_settings
from minimalcode.errors import ToolError
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime, available_tool_schemas
from minimalcode.vision import (
    ProviderVisionClient,
    image_media_type,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16


def _vision_config(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "base_url": "https://provider.test/v1",
        "api_key": "secret",
        "vision": {
            "enabled": enabled,
            "base_url": "https://provider.test/v1",
            "model": "vision-model",
            "timeout_seconds": 10,
            "max_image_bytes": 1024,
            "max_chars": 100,
            "detail": "low",
            "headers": {},
        },
    }


def _describe_client(handler) -> ProviderVisionClient:
    return ProviderVisionClient(
        "custom",
        _vision_config(),
        transport=httpx2.MockTransport(handler),
    )


class VisionClientTests(unittest.TestCase):
    def test_describe_posts_an_inline_image(self) -> None:
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "vision-model")
            content = body["messages"][0]["content"]
            self.assertEqual(content[0], {"type": "text", "text": "What is this?"})
            image = content[1]["image_url"]
            self.assertTrue(image["url"].startswith("data:image/png;base64,"))
            self.assertEqual(image["detail"], "low")
            encoded = image["url"].split(",", 1)[1]
            self.assertEqual(base64.b64decode(encoded), PNG_BYTES)
            return httpx2.Response(
                200,
                json={"choices": [{"message": {"content": "A tiny PNG."}}]},
            )

        client = _describe_client(handler)
        try:
            text = client.describe("image/png", PNG_BYTES, question="What is this?")
        finally:
            client.close()
        self.assertEqual(text, "A tiny PNG.")
        self.assertEqual(len(requests), 1)

    def test_describe_uses_a_default_question(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            self.assertIn("transcribe", body["messages"][0]["content"][0]["text"])
            return httpx2.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        client = _describe_client(handler)
        try:
            self.assertEqual(client.describe("image/jpeg", JPEG_BYTES), "ok")
        finally:
            client.close()

    def test_describe_rejects_invalid_provider_output(self) -> None:
        payloads = (
            {"choices": [{"message": {"content": ""}}]},
            {"choices": []},
            {"choices": [{"message": {}}]},
            {"error": {"message": "bad request"}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                client = _describe_client(
                    lambda request, payload=payload: httpx2.Response(200, json=payload)
                )
                try:
                    with self.assertRaisesRegex(ToolError, "provider"):
                        client.describe("image/png", PNG_BYTES)
                finally:
                    client.close()

    def test_describe_reports_http_errors(self) -> None:
        client = _describe_client(
            lambda request: httpx2.Response(400, text="unsupported image")
        )
        try:
            with self.assertRaisesRegex(ToolError, "HTTP 400"):
                client.describe("image/png", PNG_BYTES)
        finally:
            client.close()

    def test_describe_truncates_long_text(self) -> None:
        client = _describe_client(
            lambda request: httpx2.Response(
                200, json={"choices": [{"message": {"content": "x" * 250}}]}
            )
        )
        try:
            text = client.describe("image/png", PNG_BYTES)
        finally:
            client.close()
        self.assertTrue(text.endswith("<description truncated>"))
        self.assertLessEqual(len(text), 100 + len("\n<description truncated>"))

    def test_describe_retries_a_transient_connection_failure(self) -> None:
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx2.ConnectError("connection reset")
            return httpx2.Response(
                200, json={"choices": [{"message": {"content": "recovered"}}]}
            )

        client = _describe_client(handler)
        try:
            with mock.patch.object(time, "sleep"):
                text = client.describe("image/png", PNG_BYTES)
        finally:
            client.close()
        self.assertEqual(attempts, 2)
        self.assertEqual(text, "recovered")

    def test_invalid_vision_settings_are_rejected(self) -> None:
        cases = (
            {"vision": {"enabled": "yes"}},
            {"vision": {"enabled": True, "base_url": "ftp://example.com"}},
            {"vision": {"enabled": True, "base_url": "https://x.test", "model": ""}},
            {
                "vision": {
                    "enabled": True,
                    "base_url": "https://x.test",
                    "model": "m",
                    "timeout_seconds": float("inf"),
                }
            },
            {
                "vision": {
                    "enabled": True,
                    "base_url": "https://x.test",
                    "model": "m",
                    "detail": "huge",
                }
            },
        )
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ToolError):
                ProviderVisionClient("custom", config)

    def test_image_media_type_detection(self) -> None:
        self.assertEqual(image_media_type(PNG_BYTES), "image/png")
        self.assertEqual(image_media_type(JPEG_BYTES), "image/jpeg")
        self.assertEqual(image_media_type(GIF_BYTES), "image/gif")
        self.assertEqual(image_media_type(WEBP_BYTES), "image/webp")
        self.assertIsNone(image_media_type(b"plain text"))

    def test_default_deepseek_vision_settings_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = load_settings(Path(temp) / ".MinimalCode")
        _, provider = settings.provider_config("deepseek")
        vision = provider["vision"]
        self.assertTrue(vision["enabled"])
        self.assertEqual(vision["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(vision["model"], "deepseek-flash")


class ReadImageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.settings = load_settings(self.root / ".MinimalCode")
        self.image = self.workspace / "shot.png"
        self.image.write_bytes(PNG_BYTES)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _runtime(self, handler) -> ToolRuntime:
        client = _describe_client(handler)
        self.addCleanup(client.close)
        return ToolRuntime(
            self.settings,
            self.workspace,
            detect_shell(),
            vision_client=client,
        )

    def test_read_image_returns_the_description(self) -> None:
        runtime = self._runtime(
            lambda request: httpx2.Response(
                200, json={"choices": [{"message": {"content": "A screenshot."}}]}
            )
        )
        result = runtime.dispatch("read_image", {"path": "shot.png"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["description"], "A screenshot.")
        self.assertEqual(result["media_type"], "image/png")
        self.assertEqual(result["bytes"], len(PNG_BYTES))

    def test_read_image_stays_inside_the_workspace(self) -> None:
        outside = self.root / "outside.png"
        outside.write_bytes(PNG_BYTES)
        runtime = self._runtime(
            lambda request: httpx2.Response(
                200, json={"choices": [{"message": {"content": "seen"}}]}
            )
        )
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            runtime.read_image(str(outside))

        runtime.allow_outside = True
        self.assertEqual(runtime.read_image(str(outside))["description"], "seen")

    def test_read_image_rejects_unsupported_files(self) -> None:
        (self.workspace / "notes.png").write_text("not an image", encoding="utf-8")
        runtime = self._runtime(
            lambda request: self.fail("provider must not be called")
        )
        with self.assertRaisesRegex(ToolError, "unsupported image format"):
            runtime.read_image("notes.png")

    def test_read_image_rejects_oversized_files(self) -> None:
        (self.workspace / "big.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048)
        runtime = self._runtime(
            lambda request: self.fail("provider must not be called")
        )
        with self.assertRaisesRegex(ToolError, "too large"):
            runtime.read_image("big.png")

    def test_read_image_rejects_wrong_argument_types(self) -> None:
        runtime = self._runtime(
            lambda request: self.fail("provider must not be called")
        )
        with self.assertRaisesRegex(ToolError, "invalid arguments"):
            runtime.dispatch("read_image", {"path": "shot.png", "prompt": 5})

    def test_disabled_vision_hides_and_blocks_the_tool(self) -> None:
        disabled = ProviderVisionClient("custom", _vision_config(enabled=False))
        self.addCleanup(disabled.close)
        runtime = ToolRuntime(
            self.settings,
            self.workspace,
            detect_shell(),
            vision_client=disabled,
        )
        self.assertFalse(runtime.vision_enabled)
        with self.assertRaisesRegex(ToolError, "not enabled"):
            runtime.read_image("shot.png")

        visible_client = _describe_client(lambda request: httpx2.Response(200, json={}))
        self.addCleanup(visible_client.close)
        visible = {
            tool["function"]["name"]
            for tool in available_tool_schemas(None, visible_client)
        }
        hidden = {tool["function"]["name"] for tool in available_tool_schemas()}
        self.assertIn("read_image", visible)
        self.assertNotIn("read_image", hidden)


if __name__ == "__main__":
    unittest.main()
