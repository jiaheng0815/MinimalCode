"""DeepSeek tools API clients: OCR, speech, and document parsing.

Four capabilities share one pooled HTTP client because they share one host,
one API key, and one trust policy:

- ``/v1/tools/ocr``    page-by-page text recognition for PDF, Office, images
- ``/v1/tools/asr``    full-audio transcription (speech input)
- ``/v1/tools/tts``    speech synthesis (speech output)
- ``/files``           upload + parse, referenced later by ``file_id``

Every request here is billable (per page, per audio second, per character), so
none of them is retried automatically: a read timeout after the server started
processing could bill the same document twice. Only the caller decides that.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any
from urllib.parse import urlsplit

import httpx2

from .config import provider_api_key
from .errors import ToolError
from .httpclient import build_client, build_headers, join_url_path, merge_headers

# Uploaded as `purpose`. `text_recognition` drops the images of a document,
# `rich_layout_recognition` keeps them in place next to the text, and
# `user_data` is the no-parse path for plain text, spreadsheets, and ebooks.
OCR_PURPOSES = frozenset({"text_recognition", "rich_layout_recognition"})
DOCUMENT_PURPOSES = frozenset(
    {"text_recognition", "rich_layout_recognition", "user_data"}
)
SPEECH_FORMATS = frozenset({"wav", "pcm"})
VOICE_IDS = frozenset({"stella", "tide"})
# Documented ceilings, kept as defaults so a local limit can only tighten them.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _clip(text: str, limit: int) -> str:
    """Bound one text field so a huge document cannot flood the context."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n<content truncated>"


def _error_message(body: Any) -> str | None:
    """Return the provider error message carried by a 200 response, if any."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type")
        return str(message) if message else "unknown error"
    if isinstance(error, str) and error:
        return error
    return None


class ProviderMediaClient:
    """Provider-hosted OCR, speech, and document parsing over one HTTP client."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        transport: httpx2.BaseTransport | None = None,
    ):
        self.name = name
        self.enabled = False
        self.ocr_enabled = False
        self.asr_enabled = False
        self.tts_enabled = False
        self.documents_enabled = False
        self.max_upload_bytes = _MAX_UPLOAD_BYTES
        self.max_audio_bytes = _MAX_AUDIO_BYTES
        self.max_chars = 120_000
        self.max_pages = 1000
        self.voice_id = "stella"
        self.format = "wav"
        self.purpose = "text_recognition"
        self._ocr_path = ""
        self._asr_path = ""
        self._tts_path = ""
        self._files_path = ""
        self._endpoint = ""
        self._file_ids: dict[str, str] = {}
        self._client: httpx2.Client | None = None

        media = config.get("media")
        if not isinstance(media, dict):
            return
        enabled = media.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ToolError(f"provider {name!r} media.enabled must be a boolean")
        if not enabled:
            return

        base_url = str(media.get("base_url") or config.get("base_url") or "").rstrip(
            "/"
        )
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ToolError(
                f"provider {name!r} has an invalid media.base_url: {exc}"
            ) from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise ToolError(f"provider {name!r} has an invalid media.base_url")

        # A capability is enabled by naming its path; the tools API lives under
        # /v1/tools while the Files API does not, so both spellings are kept.
        self._ocr_path = str(media.get("ocr_path") or "").strip("/")
        self._asr_path = str(media.get("asr_path") or "").strip("/")
        self._tts_path = str(media.get("tts_path") or "").strip("/")
        self._files_path = str(media.get("files_path") or "").strip("/")
        if not any((self._ocr_path, self._asr_path, self._tts_path, self._files_path)):
            raise ToolError(
                f"provider {name!r} media requires at least one of ocr_path, "
                "asr_path, tts_path, or files_path"
            )

        try:
            timeout = float(media.get("timeout_seconds", 180))
            self.max_upload_bytes = int(
                media.get("max_upload_bytes", _MAX_UPLOAD_BYTES)
            )
            self.max_audio_bytes = int(media.get("max_audio_bytes", _MAX_AUDIO_BYTES))
            self.max_chars = int(media.get("max_chars", 120_000))
            self.max_pages = int(media.get("max_pages", 1000))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ToolError(
                f"provider {name!r} has invalid media numeric settings"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ToolError(f"provider {name!r} has an invalid media timeout")
        if self.max_upload_bytes < 1 or self.max_audio_bytes < 1:
            raise ToolError(f"provider {name!r} has invalid media size limits")
        if self.max_chars < 1 or self.max_pages < 1:
            raise ToolError(f"provider {name!r} has invalid media limits")

        voice = str(media.get("voice_id", "stella"))
        if voice not in VOICE_IDS:
            raise ToolError(
                f"provider {name!r} media.voice_id must be one of "
                f"{', '.join(sorted(VOICE_IDS))}"
            )
        audio_format = str(media.get("format", "wav"))
        if audio_format not in SPEECH_FORMATS:
            raise ToolError(
                f"provider {name!r} media.format must be one of "
                f"{', '.join(sorted(SPEECH_FORMATS))}"
            )
        purpose = str(media.get("purpose", "text_recognition"))
        if purpose not in DOCUMENT_PURPOSES:
            raise ToolError(
                f"provider {name!r} media.purpose must be one of "
                f"{', '.join(sorted(DOCUMENT_PURPOSES))}"
            )

        api_key = provider_api_key(media) or provider_api_key(config)
        self._client = build_client(
            base_url=base_url,
            headers=build_headers(
                api_key, merge_headers(config.get("headers"), media.get("headers"))
            ),
            timeout=timeout,
            transport=transport,
            trust_env=media.get("trust_env", config.get("trust_env")),
        )
        self._timeout_seconds = timeout
        self._endpoint = base_url
        self.voice_id = voice
        self.format = audio_format
        self.purpose = purpose
        self.ocr_enabled = bool(self._ocr_path)
        self.asr_enabled = bool(self._asr_path)
        self.tts_enabled = bool(self._tts_path)
        self.documents_enabled = bool(self._files_path)
        self.enabled = True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # -- transport ---------------------------------------------------------

    def _send(
        self,
        path: str,
        *,
        as_json: bool,
        files: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | bytes:
        """POST one request without retrying a billable call."""
        if not self.enabled or self._client is None:
            raise ToolError(f"media tools are not enabled for provider {self.name!r}")
        url = join_url_path(self._endpoint, path)
        timeout = httpx2.Timeout(
            self._timeout_seconds, connect=min(20.0, self._timeout_seconds)
        )
        try:
            response = self._client.post(
                url, data=form, files=files, json=payload, timeout=timeout
            )
        except httpx2.HTTPError as exc:
            raise ToolError(f"provider media request failed: {exc}") from exc
        try:
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location", "")
                raise ToolError(
                    "provider media request was redirected to "
                    f"{location or 'another location'}; refusing to forward "
                    "credentials and request data"
                )
            body = response.content
            if response.status_code >= 400:
                detail = body.decode("utf-8", errors="replace")[:2000]
                raise ToolError(
                    "provider media request failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
        finally:
            response.close()
        if not as_json:
            if not body:
                raise ToolError("provider media request returned an empty body")
            if limit is not None and len(body) > limit:
                raise ToolError(
                    f"provider media request returned more than {limit} bytes"
                )
            return body
        parsed = self._parse_json(body)
        message = _error_message(parsed)
        if message is not None:
            # OCR answers 200 and only then reports a failure in the body.
            raise ToolError(f"provider media request failed: {message}")
        return parsed

    @staticmethod
    def _parse_json(body: bytes) -> dict[str, Any]:
        """Parse a JSON body, tolerating the keep-alive newlines before it."""
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise ToolError("provider media response was not JSON") from exc
        if not isinstance(parsed, dict):
            raise ToolError("provider media response must be an object")
        return parsed

    def _upload_bytes(
        self,
        path: str,
        filename: str,
        data: bytes,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        """POST a multipart ``file`` + ``purpose`` pair and return the object."""
        body = self._send(
            path,
            as_json=True,
            files={"file": (filename, data)},
            form={"purpose": purpose},
        )
        assert isinstance(body, dict)
        return body

    # -- capabilities ------------------------------------------------------

    def ocr(
        self,
        filename: str,
        data: bytes,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Recognize text page by page. The file is not stored anywhere."""
        if not self.ocr_enabled:
            raise ToolError(f"OCR is not enabled for provider {self.name!r}")
        chosen = self._check_purpose(purpose, allowed=OCR_PURPOSES)
        self._check_upload(filename, data, limit=self.max_upload_bytes)
        body = self._upload_bytes(self._ocr_path, filename, data, purpose=chosen)
        pages = body.get("pages")
        entries: list[dict[str, Any]] = []
        if isinstance(pages, list):
            for raw in pages[: self.max_pages]:
                if not isinstance(raw, dict):
                    continue
                text = raw.get("text")
                entries.append(
                    {
                        "index": raw.get("index"),
                        "text": text if isinstance(text, str) else "",
                        "finish_reason": raw.get("finish_reason"),
                    }
                )
        joined = "\n\n".join(entry["text"] for entry in entries)
        truncated = len(joined) > self.max_chars
        if truncated:
            joined = _clip(joined, self.max_chars)
        clipped = any(entry["finish_reason"] == "length" for entry in entries)
        return {
            "status": "success",
            "filename": filename,
            "purpose": chosen,
            "page_count": len(entries),
            "pages": entries,
            "text": joined,
            "truncated": truncated or clipped,
        }

    def transcribe(self, filename: str, data: bytes) -> dict[str, Any]:
        """Transcribe one complete audio file (speech input)."""
        if not self.asr_enabled:
            raise ToolError(
                f"speech recognition is not enabled for provider {self.name!r}"
            )
        self._check_upload(filename, data, limit=self.max_audio_bytes)
        body = self._send(
            self._asr_path,
            as_json=True,
            files={"file": (filename, data)},
        )
        assert isinstance(body, dict)
        text = body.get("text")
        if not isinstance(text, str):
            raise ToolError("provider speech recognition response has no text")
        truncated = len(text) > self.max_chars
        if truncated:
            text = _clip(text, self.max_chars)
        return {
            "status": "success",
            "filename": filename,
            "text": text,
            "duration_ms": body.get("duration_ms"),
            "request_id": body.get("request_id"),
            "truncated": truncated,
        }

    def speech(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        audio_format: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize ``text`` and return the audio bytes (speech output)."""
        if not self.tts_enabled:
            raise ToolError(
                f"speech synthesis is not enabled for provider {self.name!r}"
            )
        if not isinstance(text, str) or not text.strip():
            raise ToolError("text must contain at least one non-whitespace character")
        chosen_voice = voice_id or self.voice_id
        if chosen_voice not in VOICE_IDS:
            raise ToolError(f"voice_id must be one of {', '.join(sorted(VOICE_IDS))}")
        chosen_format = audio_format or self.format
        if chosen_format not in SPEECH_FORMATS:
            raise ToolError(
                f"format must be one of {', '.join(sorted(SPEECH_FORMATS))}"
            )
        audio = self._send(
            self._tts_path,
            as_json=False,
            payload={
                "text": text,
                "voice_id": chosen_voice,
                "format": chosen_format,
            },
            limit=self.max_upload_bytes,
        )
        assert isinstance(audio, bytes)
        return {
            "status": "success",
            "audio": audio,
            "format": chosen_format,
            "voice_id": chosen_voice,
            "bytes": len(audio),
        }

    def upload(
        self,
        filename: str,
        data: bytes,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Upload and parse a document, returning the reusable ``file_id``."""
        if not self.documents_enabled:
            raise ToolError(
                f"document parsing is not enabled for provider {self.name!r}"
            )
        chosen = self._check_purpose(purpose, allowed=DOCUMENT_PURPOSES)
        self._check_upload(filename, data, limit=self.max_upload_bytes)
        # The same bytes never need a second paid upload: the id is reusable, so
        # re-attaching a file costs one hash instead of a full parse.
        cache_key = f"{filename}:{hashlib.sha256(data).hexdigest()}"
        cached = self._file_ids.get(cache_key)
        if cached is not None:
            return {
                "status": "success",
                "file_id": cached,
                "filename": filename,
                "purpose": chosen,
                "bytes": len(data),
                "cached": True,
            }
        body = self._upload_bytes(self._files_path, filename, data, purpose=chosen)
        file_id = body.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ToolError("provider document upload response has no id")
        self._file_ids[cache_key] = file_id
        return {
            "status": "success",
            "file_id": file_id,
            "filename": filename,
            "purpose": chosen,
            "bytes": len(data),
            "cached": False,
        }

    # -- helpers -----------------------------------------------------------

    def _check_purpose(self, purpose: str | None, *, allowed: frozenset[str]) -> str:
        chosen = purpose or self.purpose
        if chosen not in allowed:
            raise ToolError(f"purpose must be one of {', '.join(sorted(allowed))}")
        return chosen

    @staticmethod
    def _check_upload(filename: str, data: bytes, *, limit: int) -> None:
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ToolError("file data must be non-empty bytes")
        if not isinstance(filename, str) or not filename.strip():
            raise ToolError("filename must be a non-empty string")
        if len(data) > limit:
            raise ToolError(f"file is too large: {len(data)} bytes exceeds {limit}")


__all__ = [
    "DOCUMENT_PURPOSES",
    "OCR_PURPOSES",
    "SPEECH_FORMATS",
    "VOICE_IDS",
    "ProviderMediaClient",
]
