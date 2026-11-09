"""HTTP request construction and retry policy for provider calls."""

from __future__ import annotations

import math
import ssl
import time
from typing import Any

import httpx2

from .. import httpclient
from ..errors import ProviderError
from .common import iter_sse_data

# The policy itself lives in ``minimalcode.httpclient`` so the web and vision
# clients cannot drift from the protocol client. ``time`` stays imported here
# because backoff sleeps are patched at this module by the retry tests.
# Backoff runs 0.5, 1, 2, 4, then 8 seconds per attempt, so all 11 attempts span
# roughly a minute: long enough to ride out a provider hiccup, short enough that
# a dead endpoint still fails inside one agent turn.

# A TLS session can be reset after the handshake but before the response headers
# ("[SSL: UNEXPECTED_EOF_WHILE_READING]"), which httpcore surfaces as ReadError or
# as a bare ssl.SSLError. One-shot handshakes (session create, challenge fetch) are
# idempotent and cost nothing server-side, so they retry these; a streamed
# generation does not, because retrying it could duplicate output and billing.
HANDSHAKE_RETRY_ERRORS = (
    ssl.SSLError,
    httpx2.ReadError,
    httpx2.WriteError,
    httpx2.PoolTimeout,
    ConnectionError,
)


class TransportMixin:
    """Open a provider response with connection and HTTP-status retries."""

    # Attributes supplied by OpenAICompatibleProvider.
    _client: httpx2.Client
    notify: Any
    timeout_seconds: float

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _checked_lines(self, lines):
        """Yield SSE lines while enforcing the whole-stream deadline."""
        deadline = getattr(self, "_stream_deadline", None)
        for line in lines:
            if deadline is not None and time.monotonic() > deadline:
                raise ProviderError(
                    "provider stream exceeded timeout_seconds "
                    f"({self.timeout_seconds:g}s)"
                )
            yield line

    def _sse_events(self, lines):
        """Yield one payload per SSE event, joined across `data:` lines."""
        yield from iter_sse_data(self._checked_lines(lines))

    def _sse_lines(self, response: httpx2.Response):
        """Yield ``response.iter_lines()`` under the whole-stream deadline.

        Protocols that read raw SSE lines rather than whole events still have to
        inherit ``timeout_seconds``; calling ``iter_lines`` directly would let a
        server that trickles one byte hold the turn open indefinitely.
        """
        yield from self._checked_lines(response.iter_lines())

    def _read_body(self, response: httpx2.Response) -> bytes:
        """Read a response body under the same whole-request deadline.

        ``response.read()`` is bounded only by the per-socket read timeout, so a
        server that keeps trickling a byte would hold a non-streaming request
        open forever. Reading chunks and checking the deadline applies the
        configured ``timeout_seconds`` to the whole body.
        """
        deadline = getattr(self, "_stream_deadline", None)
        chunks: list[bytes] = []
        try:
            for chunk in response.iter_bytes():
                if deadline is not None and time.monotonic() > deadline:
                    raise ProviderError(
                        "provider response exceeded timeout_seconds "
                        f"({self.timeout_seconds:g}s)"
                    )
                chunks.append(chunk)
        except (httpx2.HTTPError, OSError) as exc:
            raise ProviderError(f"provider response read failed: {exc}") from exc
        return b"".join(chunks)

    def _post_json(self, url: str, payload: dict[str, Any]) -> bytes:
        """POST a small idempotent JSON request and return its body.

        Used for provider handshakes (session creation, challenge fetch) that sit
        outside the single wire-protocol call. Routed through ``_open_url`` so they
        inherit the connection retry policy, and given the extra TLS-reset
        retryable set because a dropped handshake costs nothing to repeat.
        """
        response = self._open_url(url, payload, retry_extra=HANDSHAKE_RETRY_ERRORS)
        try:
            return self._read_body(response)
        finally:
            response.close()

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        """POST ``payload`` to the protocol endpoint and return a response."""
        return self._open_url(self._endpoint(), payload)

    def _open_url(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        retry_extra: tuple[type[BaseException], ...] = (),
    ) -> httpx2.Response:
        """POST ``payload`` to ``url`` and return a successful response.

        Retries cover connection failures that occurred before a response and
        one 429/503 retry. Any other status is reported through ProviderError
        with the bounded response body. ``retry_extra`` lets an idempotent
        handshake treat TLS resets as retryable without loosening the policy for
        a streamed generation.
        """
        # Keep the transport streamed even for a `stream: false` request: the
        # parsers read either SSE lines or the whole body through `_read_body`,
        # which is the only path that enforces the whole-generation deadline.
        # Letting httpx2 read a non-streaming body internally would bypass it.
        stream = True
        last_error: ProviderError | None = None
        # Retries share the request's time budget: without it a 1s timeout could
        # still spend ~55s in connection backoff before giving up.
        deadline = time.monotonic() + self.timeout_seconds
        # Counts failures, not requests: the status retry below is charged outside
        # this budget, so one call issues at most httpclient.CONNECTION_ATTEMPTS + 1 requests.
        connection_failures = 0
        status_retried = False
        retryable_types = httpclient.RETRYABLE_CONNECTION_ERRORS + tuple(retry_extra)
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # The budget covers the attempts themselves, not only the
                    # backoff: with a 20s connect timeout a second attempt could
                    # overrun a 30s `timeout_seconds` and report much later.
                    last_error = ProviderError(
                        "provider request exceeded timeout_seconds "
                        f"({self.timeout_seconds:g}s)"
                    )
                    break
                request = self._client.build_request(
                    "POST",
                    url,
                    json=payload,
                    timeout=httpx2.Timeout(remaining, connect=min(20.0, remaining)),
                )
                response = self._client.send(request, stream=stream)
            except (httpx2.HTTPError, httpx2.InvalidURL, ValueError, OSError) as exc:
                last_error = ProviderError(f"provider network error: {exc}")
                connection_failures += 1
                if (
                    connection_failures >= httpclient.CONNECTION_ATTEMPTS
                    or not isinstance(exc, retryable_types)
                ):
                    break
                delay = min(
                    httpclient.connection_backoff(connection_failures - 1),
                    deadline - time.monotonic(),
                )
                if delay <= 0:
                    # The budget is spent; retrying now would exceed
                    # `timeout_seconds` instead of reporting the failure.
                    break
                if self.notify is not None:
                    self.notify(
                        "[retry] provider did not respond; attempt "
                        f"{connection_failures}/{httpclient.CONNECTION_ATTEMPTS - 1} "
                        f"in {delay:g}s ({exc})"
                    )
                time.sleep(delay)
                continue
            if 200 <= response.status_code < 300:
                # The per-read timeout only bounds a single socket read; this
                # deadline bounds the whole streamed generation.
                self._stream_deadline = time.monotonic() + self.timeout_seconds
                return response

            status_code = response.status_code
            if 300 <= status_code < 400:
                location = response.headers.get("Location", "")
                response.close()
                raise ProviderError(
                    "provider redirected the request to "
                    f"{location or 'another location'}; refusing to forward "
                    "credentials and the request body"
                )
            # Headers must be read before the body is consumed and closed.
            retry_after = response.headers.get("Retry-After", "1")
            try:
                body = response.read().decode("utf-8", errors="replace")[:2000]
            except httpx2.HTTPError as exc:
                body = ""
                last_error = ProviderError(
                    f"provider network error while reading response: {exc}",
                    status_code=status_code,
                )
            else:
                last_error = ProviderError(
                    f"provider returned HTTP {status_code}: {body}",
                    status_code=status_code,
                    body=body,
                )
            finally:
                response.close()
            # Rate limits and unavailable responses get one retry, after honouring
            # Retry-After; the flag is never reset, so a repeated 429/503 is
            # reported instead of looping. Other statuses are final, because the
            # provider may already have started (and billed) a generation.
            if status_code not in {429, 503} or status_retried:
                break
            status_retried = True
            try:
                # Retry-After is seconds in practice. The floor keeps "0" from
                # collapsing the single status retry into an immediate repeat;
                # the ceiling stops a provider from parking the turn for minutes.
                requested_delay = float(retry_after)
                if not math.isfinite(requested_delay):
                    requested_delay = 1.0
                delay = min(max(requested_delay, 0.2), 5.0)
            except ValueError:
                delay = 1.0
            if delay >= deadline - time.monotonic():
                # Honouring Retry-After is not worth exceeding the caller's
                # timeout; report the status instead of stalling.
                break
            time.sleep(delay)
        if last_error is None:  # pragma: no cover - defensive
            raise ProviderError("provider request failed")
        raise last_error
