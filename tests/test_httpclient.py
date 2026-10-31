"""Regression tests for the shared HTTP policy.

The bug these lock in: every client used to inherit environment proxies, so a
local provider (`http://127.0.0.1:...`) was routed through whatever proxy the
machine had configured. With Clash or a corporate proxy running, every local
request became a 502; with the proxy stopped, the connection was refused. The
end-to-end tests only passed on CI because CI has no proxy.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import httpx2

from minimalcode.httpclient import (
    build_client,
    build_headers,
    connection_backoff,
    is_loopback_url,
    resolve_trust_env,
)


class _JsonHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        return


class LoopbackDetectionTests(unittest.TestCase):
    def test_loopback_and_private_spellings(self) -> None:
        for url in (
            "http://127.0.0.1:11434/v1",
            "http://localhost/v1",
            "https://app.localhost/v1",
            "http://[::1]:8080/v1",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_loopback_url(url))
        for url in (
            "https://api.deepseek.com/v1",
            "http://192.168.1.10:8080/v1",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_loopback_url(url))

    def test_env_proxies_apply_only_to_remote_targets(self) -> None:
        self.assertFalse(
            resolve_trust_env(
                "http://127.0.0.1:1234/v1", transport=None, configured=None
            )
        )
        self.assertTrue(
            resolve_trust_env(
                "https://api.deepseek.com/v1", transport=None, configured=None
            )
        )
        # An injected transport must not be shadowed by proxy mounts.
        transport = httpx2.MockTransport(lambda request: httpx2.Response(200))
        self.assertFalse(
            resolve_trust_env(
                "https://api.deepseek.com/v1", transport=transport, configured=None
            )
        )
        # An explicit setting always wins.
        self.assertTrue(
            resolve_trust_env(
                "http://127.0.0.1:1234/v1", transport=None, configured=True
            )
        )

    def test_loopback_request_ignores_a_dead_environment_proxy(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat"
            # Port 9 (discard) is closed on every supported platform, so any
            # request routed through this proxy fails instead of succeeding.
            env = {
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
            }
            with mock.patch.dict(os.environ, env):
                client = build_client(
                    base_url=url, headers=build_headers(None, None), timeout=5
                )
                try:
                    response = client.post(url, json={"ping": True})
                finally:
                    client.close()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class RetryPolicyTests(unittest.TestCase):
    def test_json_clients_do_not_follow_redirects(self) -> None:
        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request.url.path)
            if request.url.path.endswith("/redirect"):
                return httpx2.Response(
                    307, headers={"Location": "https://evil.test/final"}
                )
            return httpx2.Response(200, json={"ok": True})

        client = build_client(
            base_url="https://provider.test/v1",
            headers={"x-api-key": "secret"},
            timeout=5,
            transport=httpx2.MockTransport(handler),
        )
        try:
            self.assertFalse(client.follow_redirects)
            response = client.post("https://provider.test/v1/redirect", json={"x": 1})
            self.assertEqual(response.status_code, 307)
        finally:
            client.close()
        self.assertEqual(seen, ["/v1/redirect"])

    def test_backoff_is_capped(self) -> None:
        self.assertEqual(connection_backoff(0), 0.5)
        self.assertEqual(connection_backoff(1), 1.0)
        self.assertEqual(connection_backoff(2), 2.0)
        self.assertEqual(connection_backoff(5), 8.0)
        self.assertEqual(connection_backoff(20), 8.0)


if __name__ == "__main__":
    unittest.main()
