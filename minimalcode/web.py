from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import ToolError


class ProviderWebClient:
    """Configurable provider-hosted web tools."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.name = name
        self.enabled = False
        self.default_count = 0
        self.max_count = 0
        self.max_content_chars = 0
        self._search_path = ""
        self._fetch_path = ""
        self._client: httpx.Client | None = None

        web = config.get("web_tools")
        if not isinstance(web, dict) or not web.get("enabled", False):
            return

        base_url = str(web.get("base_url") or "").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ToolError(f"provider {name!r} has an invalid web_tools.base_url")
        required = (
            web.get("search_path"),
            web.get("fetch_path"),
            web.get("timeout_seconds"),
            web.get("default_count"),
            web.get("max_count"),
            web.get("max_content_chars"),
        )
        if any(value in (None, "") for value in required):
            raise ToolError(
                f"provider {name!r} web_tools requires paths, timeout, "
                "counts, and max_content_chars"
            )

        self.default_count = int(web["default_count"])
        self.max_count = int(web["max_count"])
        self.max_content_chars = int(web["max_content_chars"])
        if self.default_count < 1 or self.max_count < self.default_count:
            raise ToolError(f"provider {name!r} has invalid web result limits")
        if self.max_content_chars < 1:
            raise ToolError(f"provider {name!r} has invalid max_content_chars")

        api_key = self._api_key(web, config)
        headers = {"Content-Type": "application/json"}
        custom_headers = web.get("headers")
        if isinstance(custom_headers, dict):
            headers.update({str(key): str(value) for key, value in custom_headers.items()})
        if api_key and not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            timeout=float(web["timeout_seconds"]),
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )
        self._endpoint = base_url
        self._search_path = str(web["search_path"]).strip("/")
        self._fetch_path = str(web["fetch_path"]).strip("/")
        self.enabled = True

    @staticmethod
    def _api_key(web: dict[str, Any], provider: dict[str, Any]) -> str | None:
        direct = web.get("api_key")
        if isinstance(direct, str) and direct:
            return direct
        env_name = web.get("api_key_env")
        if isinstance(env_name, str) and env_name:
            key = os.environ.get(env_name)
            if key:
                return key
        direct = provider.get("api_key")
        if isinstance(direct, str) and direct:
            return direct
        env_name = provider.get("api_key_env")
        if isinstance(env_name, str):
            return os.environ.get(env_name)
        return None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise ToolError(f"web tools are not enabled for provider {self.name!r}")
        try:
            response = self._client.post(f"{self._endpoint}/{path}", json=payload)
            if response.status_code >= 400:
                detail = response.text[:2000]
                raise ToolError(
                    f"provider web request failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
            data = response.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"provider web request failed: {exc}") from exc
        except ValueError as exc:
            raise ToolError("provider web request returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ToolError("provider web request returned an invalid response")
        return data

    def search(
        self,
        query: str,
        count: int | None = None,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise ToolError(f"web tools are not enabled for provider {self.name!r}")
        if not isinstance(query, str) or not query.strip() or len(query) > 128:
            raise ToolError("query must contain 1-128 characters")
        effective_count = self.default_count if count is None else int(count)
        if not 1 <= effective_count <= self.max_count:
            raise ToolError(f"count must be from 1 to {self.max_count}")
        payload: dict[str, Any] = {
            "query": query.strip(),
            "count": effective_count,
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = str(ori_question)
        if localization:
            payload["localization"] = str(localization)
        data = self._post(self._search_path, payload)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ToolError("provider web search results must be a list")
        return {"status": "success", "results": results}

    def fetch(self, url: str) -> dict[str, Any]:
        if not self.enabled:
            raise ToolError(f"web tools are not enabled for provider {self.name!r}")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
        ):
            raise ToolError("url must be an absolute HTTP or HTTPS URL")
        data = self._post(self._fetch_path, {"url": url})
        content = data.get("content", "")
        truncated = isinstance(content, str) and len(content) > self.max_content_chars
        if truncated:
            content = content[: self.max_content_chars] + "\n<content truncated>"
        return {
            "status": "success",
            "title": data.get("title", ""),
            "content": content,
            "truncated": truncated,
        }
