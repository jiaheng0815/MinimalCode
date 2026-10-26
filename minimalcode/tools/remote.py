"""Optional provider-backed tools: image understanding and web access."""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..vision import SUPPORTED_IMAGE_FORMATS, image_media_type


class RemoteToolsMixin:
    """Tools that delegate to a configured provider client."""

    def read_image(self, path: str, prompt: str | None = None) -> dict[str, Any]:
        """Describe a workspace image through the provider's vision model."""
        if not self.vision_enabled or self.vision_client is None:
            raise ToolError("read_image is not enabled for the current provider")
        if prompt is not None and not isinstance(prompt, str):
            raise ToolError("prompt must be a string or null")
        target = self._path(path, operation="read")
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ToolError(f"cannot read image: {target}: {exc}") from exc
        if size > self.vision_client.max_image_bytes:
            raise ToolError(
                f"image is too large: {size} bytes exceeds "
                f"vision.max_image_bytes ({self.vision_client.max_image_bytes})"
            )
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"cannot read image: {target}: {exc}") from exc
        media_type = image_media_type(data)
        if media_type is None:
            raise ToolError(
                f"unsupported image format; expected {SUPPORTED_IMAGE_FORMATS}"
            )
        description = self.vision_client.describe(
            media_type, data, question=prompt or None
        )
        return {
            "status": "success",
            "path": str(target),
            "media_type": media_type,
            "bytes": len(data),
            "description": description,
        }

    def web_search(
        self,
        query: str,
        count: int | None = None,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str | None = None,
    ) -> dict[str, Any]:
        """Search the web through the configured provider client."""
        if not self.web_enabled or self.web_client is None:
            raise ToolError("web tools are not enabled for the current provider")
        return self.web_client.search(
            query,
            count=count,
            need_content=need_content,
            ori_question=ori_question,
            localization=localization,
        )

    def web_fetch(self, url: str) -> dict[str, Any]:
        """Fetch one HTTP(S) page through the configured provider client."""
        if not self.web_enabled or self.web_client is None:
            raise ToolError("web tools are not enabled for the current provider")
        return self.web_client.fetch(url)
