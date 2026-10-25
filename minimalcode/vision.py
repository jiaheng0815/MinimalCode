from __future__ import annotations

import base64
import math
from typing import Any
from urllib.parse import urlsplit

import httpx2

from . import __version__
from .config import provider_api_key
from .errors import ToolError

# The provider judges formats by content, not by file name, so the tool does the
# same and labels the data URL with the detected type.
SUPPORTED_IMAGE_FORMATS = "JPEG, PNG, GIF, or WebP"
_DETAIL_LEVELS = {"low", "high", "original", "auto"}


def image_media_type(data: bytes) -> str | None:
    """Return the MIME type of a supported image, detected from its bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


class ProviderVisionClient:
    """Provider-hosted image understanding over an OpenAI-compatible endpoint."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        model: str | None = None,
        transport: httpx2.BaseTransport | None = None,
    ):
        self.name = name
        self.enabled = False
        self.model = ""
        self.detail = "auto"
        self.max_image_bytes = 0
        self.max_chars = 0
        self._endpoint = ""
        self._client: httpx2.Client | None = None

        vision = config.get("vision")
        if not isinstance(vision, dict):
            return
        enabled = vision.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ToolError(f"provider {name!r} vision.enabled must be a boolean")
        if not enabled:
            return

        base_url = str(vision.get("base_url") or config.get("base_url") or "").rstrip(
            "/"
        )
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ToolError(
                f"provider {name!r} has an invalid vision.base_url: {exc}"
            ) from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise ToolError(f"provider {name!r} has an invalid vision.base_url")

        chosen_model = str(vision.get("model") or model or config.get("model") or "")
        if not chosen_model:
            raise ToolError(f"provider {name!r} vision requires a model")

        try:
            timeout = float(vision.get("timeout_seconds", 120))
            self.max_image_bytes = int(vision.get("max_image_bytes", 32 * 1024 * 1024))
            self.max_chars = int(vision.get("max_chars", 8000))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ToolError(
                f"provider {name!r} has invalid vision numeric settings"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ToolError(f"provider {name!r} has an invalid vision timeout")
        if self.max_image_bytes < 1 or self.max_chars < 1:
            raise ToolError(f"provider {name!r} has invalid vision limits")

        detail = str(vision.get("detail", "auto"))
        if detail not in _DETAIL_LEVELS:
            raise ToolError(
                f"provider {name!r} vision.detail must be one of "
                f"{', '.join(sorted(_DETAIL_LEVELS))}"
            )

        api_key = provider_api_key(vision) or provider_api_key(config)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"MinimalCode/{__version__}",
        }
        custom_headers = vision.get("headers")
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
        self._endpoint = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        self.model = chosen_model
        self.detail = detail
        self.enabled = True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def describe(
        self,
        media_type: str,
        data: bytes,
        *,
        question: str | None = None,
    ) -> str:
        if not self.enabled or self._client is None:
            raise ToolError(f"read_image is not enabled for provider {self.name!r}")
        if not isinstance(data, bytes) or not data:
            raise ToolError("image data must be non-empty bytes")
        prompt = (question or "").strip() or (
            "Describe this image in detail and transcribe all visible text verbatim."
        )
        encoded = base64.b64encode(data).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{encoded}",
                                "detail": self.detail,
                            },
                        },
                    ],
                }
            ],
        }
        response: httpx2.Response | None = None
        try:
            response = self._client.post(self._endpoint, json=payload)
            if response.status_code >= 400:
                detail = response.text[:2000]
                raise ToolError(
                    f"provider vision request failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
            body = response.json()
        except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
            raise ToolError(f"provider vision request failed: {exc}") from exc
        except ValueError as exc:
            raise ToolError("provider vision request returned invalid JSON") from exc
        finally:
            if response is not None:
                response.close()

        text = self._parse_text(body)
        if not text.strip():
            raise ToolError("provider returned an empty image description")
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n<description truncated>"
        return text

    @staticmethod
    def _parse_text(body: Any) -> str:
        if not isinstance(body, dict):
            raise ToolError("provider vision response must be an object")
        if body.get("error"):
            raise ToolError(f"provider vision response failed: {body['error']}")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ToolError("provider vision response has no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ToolError("provider vision response choice must be an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ToolError("provider vision response has no message")
        return _message_text(message.get("content"))


__all__ = [
    "SUPPORTED_IMAGE_FORMATS",
    "ProviderVisionClient",
    "image_media_type",
]
