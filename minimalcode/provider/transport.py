"""HTTP request construction and retry policy for provider calls."""

from __future__ import annotations

import time
from typing import Any

import httpx2

from ..errors import ProviderError

# A request that never produced a response is safe to repeat: the provider did
# not start a billable generation, so a dropped keep-alive or a proxy reset is
# worth retrying. Failures after the body started streaming are not retried,
# because the answer may already have been shown or billed.
_RETRYABLE_CONNECTION_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.RemoteProtocolError,
)
_CONNECTION_ATTEMPTS = 11  # the first try plus 10 retries
_CONNECTION_BACKOFF_SECONDS = 0.5
_CONNECTION_BACKOFF_CAP_SECONDS = 8.0


def connection_backoff(attempt: int) -> float:
    """Return capped exponential backoff for a zero-based retry attempt."""
    delay = _CONNECTION_BACKOFF_SECONDS * (2**attempt)
    return min(delay, _CONNECTION_BACKOFF_CAP_SECONDS)


class TransportMixin:
    """Open a provider response with connection and HTTP-status retries."""

    # Attributes supplied by OpenAICompatibleProvider.
    _client: httpx2.Client
    notify: Any

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _open(self, payload: dict[str, Any]) -> httpx2.Response:
        """POST ``payload`` and return a successful response.

        Retries cover connection failures that occurred before a response and
        one 429/503 retry. Any other status is reported through ProviderError
        with the bounded response body.
        """
        stream = bool(payload.get("stream"))
        last_error: ProviderError | None = None
        connection_attempt = 0
        status_retried = False
        while True:
            try:
                request = self._client.build_request(
                    "POST", self._endpoint(), json=payload
                )
                response = self._client.send(request, stream=stream)
            except (httpx2.HTTPError, httpx2.InvalidURL, ValueError) as exc:
                last_error = ProviderError(f"provider network error: {exc}")
                connection_attempt += 1
                if connection_attempt >= _CONNECTION_ATTEMPTS or not isinstance(
                    exc, _RETRYABLE_CONNECTION_ERRORS
                ):
                    break
                delay = connection_backoff(connection_attempt - 1)
                if self.notify is not None:
                    self.notify(
                        "[retry] provider did not respond; attempt "
                        f"{connection_attempt}/{_CONNECTION_ATTEMPTS - 1} "
                        f"in {delay:g}s ({exc})"
                    )
                time.sleep(delay)
                continue
            if response.status_code < 400:
                return response

            status_code = response.status_code
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
            if status_code not in {429, 503} or status_retried:
                break
            status_retried = True
            try:
                delay = min(max(float(retry_after), 0.2), 5.0)
            except ValueError:
                delay = 1.0
            time.sleep(delay)
        if last_error is None:  # pragma: no cover - defensive
            raise ProviderError("provider request failed")
        raise last_error
