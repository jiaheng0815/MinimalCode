"""Fail-closed sandbox policy, backends, and bounded execution.

The package is split into policy, backend, execution, and runner modules. The
names in ``__all__`` are the stable surface. Pre-split module-level names are
served by a temporary alias table that warns on use; see
``docs/compatibility.md`` for replacement paths and the removal target.
"""

from __future__ import annotations

from typing import Any

from .._compat import AliasTable, deprecated_attribute
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

# Removed in 0.7.0. Tests that patch ``minimalcode.sandbox.shutil.which`` must
# switch to ``minimalcode.sandbox.backends.shutil.which``; the facade alias no
# longer reaches the backend's availability check.
_DEPRECATED_ATTRIBUTES: AliasTable = {
    "_Capture": ("minimalcode.sandbox.execution", "_Capture"),
    "_auto_backend": ("minimalcode.sandbox.backends", "_auto_backend"),
    "_child_env": ("minimalcode.sandbox.execution", "_child_env"),
    "_decode_output": ("minimalcode.sandbox.execution", "_decode_output"),
    "_docker_user": ("minimalcode.sandbox.backends", "_docker_user"),
    "_execute": ("minimalcode.sandbox.execution", "_execute"),
    "_finish_capture": ("minimalcode.sandbox.execution", "_finish_capture"),
    "_host_allowed": ("minimalcode.sandbox.network", "host_allowed"),
    "_is_within": ("minimalcode.sandbox.policy", "_is_within"),
    "_join_readers": ("minimalcode.sandbox.execution", "_join_readers"),
    "_number": ("minimalcode.sandbox.policy", "_number"),
    "_posix_limits": ("minimalcode.sandbox.execution", "_posix_limits"),
    "_pump": ("minimalcode.sandbox.execution", "_pump"),
    "_resource_limiter": ("minimalcode.sandbox.execution", "_resource_limiter"),
    "_set_limit": ("minimalcode.sandbox.execution", "_set_limit"),
    "_string_tuple": ("minimalcode.sandbox.policy", "_string_tuple"),
    "_terminate_tree": ("minimalcode.sandbox.execution", "_terminate_tree"),
    "shutil": ("shutil", None),
}


def __getattr__(name: str) -> Any:
    """Serve pre-split names with a deprecation warning."""
    return deprecated_attribute(__name__, name, _DEPRECATED_ATTRIBUTES)
