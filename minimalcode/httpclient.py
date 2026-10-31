"""Shared HTTP client construction and connection-retry policy.

The provider, web, and vision clients all speak HTTP to a configured endpoint,
so they share two decisions: which proxy settings apply, and how a request that
never produced a response is retried. Both used to be copied per client, which
is how a proxy bug could exist in one copy and not the others.
"""

from __future__ import annotations

import ipaddress
import time
from typing import Any
from urllib.parse import urlsplit

import httpx2

from . import __version__
from .errors import ToolError

# A request that never produced a response is safe to repeat: the provider did
# not start a billable generation, so a dropped keep-alive or a proxy reset is
# worth retrying. Failures after streaming starts are handled by the callers and
# are never retried, because the answer may already have been shown or billed.
CONNECTION_ATTEMPTS = 11  # the first try plus 10 retries
CONNECTION_BACKOFF_SECONDS = 0.5
CONNECTION_BACKOFF_CAP_SECONDS = 8.0
RETRYABLE_CONNECTION_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.RemoteProtocolError,
)


def connection_backoff(attempt: int) -> float:
    """Return capped exponential backoff for a zero-based retry attempt."""
    delay = CONNECTION_BACKOFF_SECONDS * (2**attempt)
    return min(delay, CONNECTION_BACKOFF_CAP_SECONDS)


def is_loopback_url(url: str) -> bool:
    """Return True when ``url`` targets this machine.

    Environment proxies are routinely configured for remote traffic (Clash on
    ``127.0.0.1:7897``, corporate MITM proxies, and so on). Sending a request for
    ``http://127.0.0.1:11434`` to such a proxy cannot work: a stopped local proxy
    turns every local-provider call into a 502, and a running one has no route to
    the caller's loopback interface. Loopback endpoints therefore opt out of
    environment proxies unconditionally, which is also why the network-failure
    tests no longer depend on the host having no proxy configured.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_headers(api_key: str | None, custom: Any) -> dict[str, str]:
    """Build request headers, honouring a caller-supplied Authorization."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"MinimalCode/{__version__}",
    }
    if isinstance(custom, dict):
        headers.update({str(key): str(value) for key, value in custom.items()})
    if api_key and not any(key.lower() == "authorization" for key in headers):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def resolve_trust_env(
    base_url: str,
    *,
    transport: httpx2.BaseTransport | None,
    configured: Any,
) -> bool:
    """Decide whether environment proxy settings apply to this client.

    An explicit transport means the caller injected the connection (tests and
    embedders); proxy mounts would silently shadow it, so they stay off unless
    asked for. Otherwise loopback targets ignore the environment, and everything
    else keeps the previous default of honouring it.
    """
    if isinstance(configured, bool):
        return configured
    if transport is not None:
        return False
    return not is_loopback_url(base_url)


def build_client(
    *,
    base_url: str,
    headers: dict[str, str],
    timeout: float,
    transport: httpx2.BaseTransport | None = None,
    trust_env: Any = None,
) -> httpx2.Client:
    """Build one pooled client with the shared timeout and proxy policy.

    Redirects are never followed automatically for JSON API calls. A 307/308
    replays the complete request body (conversation, tool output, image data)
    at the new origin, and httpx2 only strips ``Authorization`` on a cross
    origin redirect; ``x-api-key`` and custom credential headers survive.
    Callers surface a 3xx response as a provider error instead.
    """
    # The scalar timeout bounds a whole generation; capping the connect phase
    # keeps an unreachable host from spending that entire budget on a SYN.
    return httpx2.Client(
        timeout=httpx2.Timeout(timeout, connect=min(20.0, timeout)),
        headers=headers,
        transport=transport,
        follow_redirects=False,
        trust_env=resolve_trust_env(
            base_url, transport=transport, configured=trust_env
        ),
    )


def post_json(
    client: httpx2.Client,
    url: str,
    payload: dict[str, Any],
    *,
    label: str,
    timeout: float,
    deadline: float | None = None,
) -> dict[str, Any]:
    """POST a JSON body and return the parsed object, retrying connect failures.

    ``deadline`` is an absolute ``time.monotonic()`` value bounding the whole
    call, including backoff sleeps; it defaults to one ``timeout`` window. Only
    failures raised before a response arrives are retried.
    """
    budget_end = deadline if deadline is not None else time.monotonic() + timeout
    attempts = 0
    last_error: httpx2.HTTPError | None = None
    status_retried = False
    while attempts < CONNECTION_ATTEMPTS:
        remaining = budget_end - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        response: httpx2.Response | None = None
        try:
            response = client.post(
                url,
                json=payload,
                timeout=httpx2.Timeout(remaining, connect=min(20.0, remaining)),
            )
            if response.status_code in {429, 503} and not status_retried:
                status_retried = True
                status_code = response.status_code
                try:
                    delay = min(
                        max(float(response.headers.get("Retry-After", "1")), 0.2),
                        5.0,
                    )
                except ValueError:
                    delay = 1.0
                response.close()
                response = None
                remaining = budget_end - time.monotonic()
                if remaining <= 0:
                    # The single status retry needs time to run; without it the
                    # final message would report a connection error that never
                    # happened ("after 1 attempts: None") and drop the status.
                    raise ToolError(
                        f"provider {label} request failed with HTTP {status_code} "
                        "and no time was left to retry"
                    )
                time.sleep(min(delay, remaining))
                continue
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location", "")
                raise ToolError(
                    f"provider {label} request was redirected to "
                    f"{location or 'another location'}; refusing to forward "
                    "credentials and request data"
                )
            if response.status_code >= 400:
                detail = response.text[:2000]
                raise ToolError(
                    f"provider {label} request failed with HTTP "
                    f"{response.status_code}: {detail}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ToolError(
                    f"provider {label} request returned invalid JSON"
                ) from exc
        except RETRYABLE_CONNECTION_ERRORS as exc:
            last_error = exc
            if attempts >= CONNECTION_ATTEMPTS:
                break
            remaining = budget_end - time.monotonic()
            if remaining <= 0:
                break
            delay = min(connection_backoff(attempts - 1), remaining)
            if delay > 0:
                time.sleep(delay)
            continue
        except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
            raise ToolError(f"provider {label} request failed: {exc}") from exc
        finally:
            if response is not None:
                response.close()
        if not isinstance(data, dict):
            raise ToolError(f"provider {label} request returned an invalid response")
        return data
    raise ToolError(
        f"provider {label} request failed after {attempts} attempts: {last_error}"
    ) from last_error


__all__ = [
    "CONNECTION_ATTEMPTS",
    "CONNECTION_BACKOFF_CAP_SECONDS",
    "CONNECTION_BACKOFF_SECONDS",
    "RETRYABLE_CONNECTION_ERRORS",
    "build_client",
    "build_headers",
    "connection_backoff",
    "is_loopback_url",
    "post_json",
    "resolve_trust_env",
]
