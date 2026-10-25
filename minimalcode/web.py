from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

import httpx2

from . import __version__
from .config import provider_api_key
from .errors import ToolError


class ProviderWebClient:
    """Configurable provider-hosted web tools."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        transport: httpx2.BaseTransport | None = None,
    ):
        self.name = name
        self.enabled = False
        self.default_count = 0
        self.max_count = 0
        self.max_content_chars = 0
        self._search_path = ""
        self._fetch_path = ""
        self._client: httpx2.Client | None = None

        web = config.get("web_tools")
        if not isinstance(web, dict):
            return
        enabled = web.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ToolError(f"provider {name!r} web_tools.enabled must be a boolean")
        if not enabled:
            return

        base_url = str(web.get("base_url") or "").rstrip("/")
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ToolError(
                f"provider {name!r} has an invalid web_tools.base_url: {exc}"
            ) from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
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

        try:
            timeout = float(web["timeout_seconds"])
            self.default_count = int(web["default_count"])
            self.max_count = int(web["max_count"])
            self.max_content_chars = int(web["max_content_chars"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ToolError(
                f"provider {name!r} has invalid web_tools numeric settings"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ToolError(f"provider {name!r} has invalid web_tools timeout")
        if self.default_count < 1 or self.max_count < self.default_count:
            raise ToolError(f"provider {name!r} has invalid web result limits")
        if self.max_content_chars < 1:
            raise ToolError(f"provider {name!r} has invalid max_content_chars")

        api_key = provider_api_key(web) or provider_api_key(config)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"MinimalCode/{__version__}",
        }
        custom_headers = web.get("headers")
        if isinstance(custom_headers, dict):
            headers.update(
                {str(key): str(value) for key, value in custom_headers.items()}
            )
        if api_key and not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx2.Client(
            timeout=timeout,
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )
        self._endpoint = base_url
        self._search_path = str(web["search_path"]).strip("/")
        self._fetch_path = str(web["fetch_path"]).strip("/")
        self.enabled = True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise ToolError(f"web tools are not enabled for provider {self.name!r}")
        response: httpx2.Response | None = None
        try:
            response = self._client.post(f"{self._endpoint}/{path}", json=payload)
            if response.status_code >= 400:
                detail = response.text[:2000]
                raise ToolError(
                    f"provider web request failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
            data = response.json()
        except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
            raise ToolError(f"provider web request failed: {exc}") from exc
        except ValueError as exc:
            raise ToolError("provider web request returned invalid JSON") from exc
        finally:
            if response is not None:
                response.close()
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
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool)
        ):
            raise ToolError("count must be an integer")
        if not isinstance(need_content, bool):
            raise ToolError("need_content must be a boolean")
        if ori_question is not None and not isinstance(ori_question, str):
            raise ToolError("ori_question must be a string or null")
        if localization is not None and not isinstance(localization, str):
            raise ToolError("localization must be a string or null")
        effective_count = self.default_count if count is None else count
        if not 1 <= effective_count <= self.max_count:
            raise ToolError(f"count must be from 1 to {self.max_count}")
        payload: dict[str, Any] = {
            "query": query.strip(),
            "count": effective_count,
            "need_content": need_content,
        }
        if ori_question:
            payload["ori_question"] = ori_question
        if localization:
            payload["localization"] = localization
        data = self._post(self._search_path, payload)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ToolError("provider web search results must be a list")
        # Provider output is untrusted: keep only object results, cap the count,
        # and bound every text field so one search cannot flood the context.
        objects = [item for item in results if isinstance(item, dict)]
        normalized = [
            self._normalize_result(item)
            for item in objects[: min(effective_count, self.max_count)]
        ]
        return {"status": "success", "results": normalized}

    def _normalize_result(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._normalize_value(value) for key, value in item.items()}

    def _normalize_value(self, value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            if len(value) > self.max_content_chars:
                return value[: self.max_content_chars] + "\n<content truncated>"
            return value
        if depth >= 32:
            return "<nested value omitted>"
        if isinstance(value, dict):
            return {
                key: self._normalize_value(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._normalize_value(item, depth + 1) for item in value]
        return value

    def fetch(self, url: str) -> dict[str, Any]:
        if not self.enabled:
            raise ToolError(f"web tools are not enabled for provider {self.name!r}")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            hostname = parsed.hostname if parsed is not None else None
            if parsed is not None:
                _ = parsed.port
        except ValueError as exc:
            raise ToolError(
                f"url must be an absolute HTTP or HTTPS URL: {exc}"
            ) from exc
        if parsed is None or parsed.scheme not in {"http", "https"} or not hostname:
            raise ToolError("url must be an absolute HTTP or HTTPS URL")
        data = self._post(self._fetch_path, {"url": url})
        title = data.get("title")
        if title is None:
            title = ""
        content = data.get("content", "")
        if not isinstance(title, str):
            raise ToolError("provider web fetch title must be a string")
        if not isinstance(content, str):
            raise ToolError("provider web fetch content must be a string")
        title_truncated = len(title) > self.max_content_chars
        if title_truncated:
            title = title[: self.max_content_chars] + "...<title truncated>"
        truncated = len(content) > self.max_content_chars
        if truncated:
            content = content[: self.max_content_chars] + "\n<content truncated>"
        return {
            "status": "success",
            "title": title,
            "content": content,
            "truncated": truncated or title_truncated,
        }
