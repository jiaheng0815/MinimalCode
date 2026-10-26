"""Fail-closed sandbox policy, backends, and bounded execution.

The package is split into policy, backend, execution, and runner modules.
``__all__`` is the complete public surface; private helpers moved out of the
pre-0.7 single ``sandbox`` module are imported from the submodule that defines
them (``minimalcode.sandbox.execution``, ``.backends``, ``.policy``,
``.network``, ``.sensitive``).
"""

from __future__ import annotations

from .audit import SandboxAudit, SandboxDenial
from .backends import (
    BwrapBackend,
    CommandRequest,
    DockerBackend,
    FirejailBackend,
    LocalSandboxBackend,
    MacSandboxExecBackend,
    SandboxBackend,
    SandboxUnavailable,
    available_backends,
    backend_for,
)
from .execution import truncate_text
from .network import host_allowed, network_signals
from .policy import NETWORK_POLICIES, SANDBOX_BACKENDS, SandboxPolicy
from .runner import SandboxRunner
from .sensitive import (
    SAFE_ENV_TEMPLATES,
    SENSITIVE_DIRECTORY_NAMES,
    SENSITIVE_FILE_NAMES,
    SENSITIVE_SEARCH_GLOBS,
    SENSITIVE_SUFFIXES,
    is_sensitive_path,
)

__all__ = [
    "NETWORK_POLICIES",
    "SAFE_ENV_TEMPLATES",
    "SANDBOX_BACKENDS",
    "SENSITIVE_DIRECTORY_NAMES",
    "SENSITIVE_FILE_NAMES",
    "SENSITIVE_SEARCH_GLOBS",
    "SENSITIVE_SUFFIXES",
    "BwrapBackend",
    "CommandRequest",
    "DockerBackend",
    "FirejailBackend",
    "LocalSandboxBackend",
    "MacSandboxExecBackend",
    "SandboxAudit",
    "SandboxBackend",
    "SandboxDenial",
    "SandboxPolicy",
    "SandboxRunner",
    "SandboxUnavailable",
    "available_backends",
    "backend_for",
    "host_allowed",
    "is_sensitive_path",
    "network_signals",
    "truncate_text",
]
