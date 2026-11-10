"""Bounded audit records for sandbox denials and operational notes."""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

# A command that was refused on network grounds is the one most likely to
# carry a credential: `curl -H "Authorization: Bearer ..."` is exactly what the
# policy stops. Records must not become a second plaintext copy of the key.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(authorization\s*:\s*)(?!bearer)[^\s'\"]+"),
    re.compile(r"(?i)\b(x-api-key\s*:\s*)[^\s'\"]+"),
    re.compile(
        # `access_token=...` and `client-secret: ...` are as sensitive as a
        # bare `token=...`, so allow a name prefix before the keyword and a
        # name suffix after it: `AWS_SECRET_ACCESS_KEY=...` carries the value
        # after the suffix, not right after `secret`.
        r"(?i)\b([\w-]*(?:api[_-]?key|token|password|passwd|secret)[\w-]*\s*[=:]\s*)"
        r"[^\s'\"]+"
    ),
    re.compile(r"(?i)(--(?:password|token|api-key|header)\s+)[^\s'\"]+"),
    re.compile(
        # The value may be attached (`-uadmin:pw`), `=`-joined
        # (`--user=admin:pw`), quoted (`-b "session=abc"`), or a separate
        # argument. The quoted branch matters: `[^\s'\"]` could never match a
        # value that starts with a quote, so the credential was recorded in
        # plaintext.
        r"(?i)(\s(?:-u|--user|--password|-b|--cookie|-H|--header)"
        r"(?:\s+|=\s*|(?=[^-])))"
        r"(?:['\"][^'\"]*['\"]|[^\s'\"]+)"
    ),
    # `mysql -phunter2` only attaches its password; a bare `-p` flag such as
    # `docker run -p 8080:80` must keep its argument readable.
    re.compile(r"(?i)(\s-p(?=[^\s-]))[^\s'\"]+"),
    # `https://user:password@host` puts the credential in the URL itself, which
    # none of the option-shaped patterns above reach.
    re.compile(r"(?i)(://[^/\s:@'\"]+:)[^/\s@'\"]+(?=@)"),
    # A personal access token is often the whole userinfo
    # (`https://ghp_...@github.com/...`), with no `:` for the pattern above.
    re.compile(r"(?i)(://)[^/\s:@'\"]+(?=@)"),
    # `net use \\server\share /user:name password` passes the secret as a
    # separate, positionally paired argument.
    re.compile(r"(?i)(/user:\S+\s+)[^\s'\"]+"),
)
_MAX_TARGET_CHARS = 500


def redact_command(text: str) -> str:
    """Mask credential-shaped values and bound the recorded text."""
    redacted = str(text)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(1) + "<redacted>", redacted)
    if len(redacted) > _MAX_TARGET_CHARS:
        redacted = redacted[:_MAX_TARGET_CHARS] + " ...<truncated>"
    return redacted


@dataclass(frozen=True, slots=True)
class SandboxDenial:
    """One refused operation and the policy reason behind it."""

    operation: str
    target: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""
        return {
            "operation": self.operation,
            "target": self.target,
            "reason": self.reason,
        }


class SandboxAudit:
    """Bounded in-memory audit trail of refused operations and notes."""

    def __init__(self, max_entries: int = 200) -> None:
        # Both deques are bounded and share one lock so summary snapshots are
        # internally consistent without retaining unbounded history.
        self._lock = threading.Lock()
        self._denials: deque[SandboxDenial] = deque(maxlen=max_entries)
        self._notes: deque[str] = deque(maxlen=max_entries)
        self.total_denials = 0

    def deny(self, operation: str, target: str, reason: str) -> None:
        """Record one denial and increment the lifetime counter."""
        with self._lock:
            self._denials.append(
                SandboxDenial(
                    operation=str(operation),
                    target=redact_command(str(target)),
                    reason=str(reason),
                )
            )
            self.total_denials += 1

    def note(self, message: str) -> None:
        """Record an operational note such as a retry or timeout."""
        with self._lock:
            self._notes.append(redact_command(str(message)))

    def denials(self) -> list[SandboxDenial]:
        """Return a snapshot of the recent denial ring buffer."""
        with self._lock:
            return list(self._denials)

    def notes(self) -> list[str]:
        """Return a snapshot of the recent note ring buffer."""
        with self._lock:
            return list(self._notes)

    def summary(self) -> dict[str, Any]:
        """Return counters and recent history for CLI/status output."""
        with self._lock:
            return {
                "total_denials": self.total_denials,
                "recent": [denial.as_dict() for denial in self._denials],
                "notes": list(self._notes),
            }
