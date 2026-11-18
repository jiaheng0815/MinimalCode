"""Provider-backed document, audio, and speech tools.

The three tools here all read or write a workspace file and then hand the bytes
to the provider's tools API, so they resolve every path through the same sandbox
policy as the file tools and never touch a path outside the workspace.
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError

# Upload ceilings from the API documentation, applied before a file leaves the
# machine so a 300 MiB log is rejected without being read into memory twice.
OCR_MAX_BYTES = 100 * 1024 * 1024
AUDIO_MAX_BYTES = 8 * 1024 * 1024


class MediaToolsMixin:
    """Tools that delegate to a configured provider media client."""

    media_client: Any

    def _media(self, capability: str) -> Any:
        client = getattr(self, "media_client", None)
        if client is None or not getattr(client, "enabled", False):
            raise ToolError(f"{capability} is not enabled for the current provider")
        if not getattr(client, f"{capability}_enabled", False):
            raise ToolError(f"{capability} is not enabled for the current provider")
        return client

    def attach_document(self, path: str, purpose: str | None = None) -> dict[str, Any]:
        """Upload a workspace document through the Files API and return its id.

        Deliberately not a model tool: a ``file_id`` only means something once a
        user message references it, so the caller decides what to attach.
        """
        client = self._media("documents")
        if purpose is not None and not isinstance(purpose, str):
            raise ToolError("purpose must be a string or null")
        target, data = self._read_media_file(
            path, limit=OCR_MAX_BYTES, label="document"
        )
        result = client.upload(target.name, data, purpose)
        return {**result, "path": str(target)}

    def _read_media_file(
        self, path: str, *, limit: int, label: str
    ) -> tuple[Any, bytes]:
        target = self._path(path, operation="read")
        if not target.is_file():
            raise ToolError(f"file not found: {target}")
        try:
            # Read one byte past the limit instead of trusting stat(): the file
            # can grow between the check and the read.
            with target.open("rb") as handle:
                data = handle.read(limit + 1)
        except OSError as exc:
            raise ToolError(f"cannot read {label}: {target}: {exc}") from exc
        if len(data) > limit:
            raise ToolError(f"{label} is too large: more than {limit} bytes")
        if not data:
            raise ToolError(f"{label} is empty: {target}")
        return target, data

    def ocr(self, path: str, purpose: str | None = None) -> dict[str, Any]:
        """Extract text from a PDF, Office document, or image, page by page."""
        client = self._media("ocr")
        if purpose is not None and not isinstance(purpose, str):
            raise ToolError("purpose must be a string or null")
        target, data = self._read_media_file(
            path, limit=OCR_MAX_BYTES, label="document"
        )
        result = client.ocr(target.name, data, purpose)
        return {**result, "path": str(target)}

    def transcribe(self, path: str) -> dict[str, Any]:
        """Transcribe an audio file (16-bit PCM WAV or Ogg Opus) into text."""
        client = self._media("asr")
        target, data = self._read_media_file(
            path, limit=AUDIO_MAX_BYTES, label="audio file"
        )
        result = client.transcribe(target.name, data)
        return {**result, "path": str(target)}

    def speech(
        self,
        text: str,
        path: str,
        voice_id: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize speech from text and write the audio into the workspace."""
        client = self._media("tts")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("text must be a non-empty string")
        if voice_id is not None and not isinstance(voice_id, str):
            raise ToolError("voice_id must be a string or null")
        target = self._path(path, operation="write")
        result = client.speech(text, voice_id=voice_id)
        audio = result.get("audio")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ToolError("provider speech synthesis returned no audio")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                handle.write(audio)
        except OSError as exc:
            raise ToolError(f"cannot write audio: {target}: {exc}") from exc
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(audio),
            "format": result.get("format"),
            "voice_id": result.get("voice_id"),
        }
