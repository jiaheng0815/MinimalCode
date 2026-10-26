"""Bounded audit records for sandbox denials and operational notes."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any


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
                SandboxDenial(operation=operation, target=str(target), reason=reason)
            )
            self.total_denials += 1

    def note(self, message: str) -> None:
        """Record an operational note such as a retry or timeout."""
        with self._lock:
            self._notes.append(str(message))

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
