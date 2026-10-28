"""HTTP request construction and retry policy for provider calls."""

from __future__ import annotations

import time
from typing import Any

import httpx2

from .. import httpclient
from ..errors import ProviderError

# The policy itself lives in ``minimalcode.httpclient`` so the web and vision
# clients cannot drift from the protocol client. ``time`` stays imported here
# because backoff sleeps are patched at this module by the retry tests.
# Backoff runs 0.5, 1, 2, 4, then 8 seconds per attempt, so all 11 attempts span
# roughly a minute: long enough to ride out a provider hiccup, short enough that
# a dead endpoint still fails inside one agent turn.


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
        # Counts failures, not requests: the status retry below is charged outside
        # this budget, so one call issues at most httpclient.CONNECTION_ATTEMPTS + 1 requests.
        connection_failures = 0
        status_retried = False
        while True:
            try:
                request = self._client.build_request(
                    "POST", self._endpoint(), json=payload
                )
                response = self._client.send(request, stream=stream)
            except (httpx2.HTTPError, httpx2.InvalidURL, ValueError) as exc:
                last_error = ProviderError(f"provider network error: {exc}")
                connection_failures += 1
                if (
                    connection_failures >= httpclient.CONNECTION_ATTEMPTS
                    or not isinstance(exc, httpclient.RETRYABLE_CONNECTION_ERRORS)
                ):
                    break
                delay = httpclient.connection_backoff(connection_failures - 1)
                if self.notify is not None:
                    self.notify(
                        "[retry] provider did not respond; attempt "
                        f"{connection_failures}/{httpclient.CONNECTION_ATTEMPTS - 1} "
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
                delay = min(max(float(retry_after), 0.2), 5.0)
            except ValueError:
                delay = 1.0
            time.sleep(delay)
        if last_error is None:  # pragma: no cover - defensive
            raise ProviderError("provider request failed")
        raise last_error
