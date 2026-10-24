from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import provider_api_key
from .errors import ToolError


class DeepSeekWebClient:
    """DeepSeek-hosted search and fetch tools, disabled for every other API."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        base_url = str(config.get("base_url") or "")
        parsed = urlsplit(base_url)
        self.enabled = (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "api.deepseek.com"
        )
        self.name = name
        self._client: httpx.Client | None = None
        if not self.enabled:
            return

        api_key = provider_api_key(config)
        if not api_key:
            raise ToolError("DeepSeek web tools require DEEPSEEK_API_KEY")
        self._endpoint = f"{parsed.scheme}://{parsed.netloc}/v1/tools"
        timeout = min(float(config.get("timeout_seconds", 120)), 60.0)
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _post(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise ToolError("web tools are only available with the DeepSeek API")
        try:
            response = self._client.post(f"{self._endpoint}/{action}", json=payload)
            if response.status_code >= 400:
                detail = response.text[:2000]
                raise ToolError(
                    f"DeepSeek web {action} failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
            data = response.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"DeepSeek web {action} failed: {exc}") from exc
        except ValueError as exc:
            raise ToolError(f"DeepSeek web {action} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ToolError(f"DeepSeek web {action} returned an invalid response")
        return data

    def search(
        self,
        query: str,
        count: int = 10,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 128:
            raise ToolError("query must contain 1-128 characters")
        if not 1 <= int(count) <= 50:
            raise ToolError("count must be from 1 to 50")
        payload: dict[str, Any] = {
            "query": query.strip(),
            "count": int(count),
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = str(ori_question)
        if localization:
            payload["localization"] = str(localization)
        data = self._post("search", payload)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ToolError("DeepSeek web search results must be a list")
        return {"status": "success", "results": results}

    def fetch(self, url: str) -> dict[str, Any]:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
        ):
            raise ToolError("url must be an absolute HTTP or HTTPS URL")
        data = self._post("fetch", {"url": url})
        return {
            "status": "success",
            "title": data.get("title", ""),
            "content": data.get("content", ""),
        }
