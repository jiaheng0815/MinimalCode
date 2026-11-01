"""Credential-like path detection and search exclusions.

The list is intentionally narrow: false positives make the agent unusable, so
this is a last line of defence rather than the only sandbox boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

SENSITIVE_DIRECTORY_NAMES = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".minimalcode",
        ".docker",
        ".password-store",
    }
)
SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".git-credentials",
        ".htpasswd",
        ".pypirc",
        ".vault-token",
        ".dockercfg",
        ".yarnrc.yml",
        "credentials",
        "credentials.json",
        "secrets",
        "secrets.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_ed25519_sk",
    }
)
SENSITIVE_KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SENSITIVE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".ppk",
    ".p8",
    ".kdbx",
)
SAFE_ENV_TEMPLATES = frozenset(
    {".env.example", ".env.sample", ".env.template", ".env.dist"}
)

# ripgrep globs that keep bulk searches from echoing credentials. They are
# exclusion-only: a positive re-include would widen a `find` pattern. The
# globs cannot express every policy rule (case-insensitive suffixes, and the
# difference between `.env.example` and `.env.local`), so result paths are
# filtered through `SandboxRunner.is_allowed` as a second line of defence.
SENSITIVE_SEARCH_GLOBS: tuple[str, ...] = (
    "!**/.env",
    "!**/.env-*",
    "!**/.netrc",
    "!**/.npmrc",
    "!**/.pgpass",
    "!**/.git-credentials",
    "!**/.htpasswd",
    "!**/.pypirc",
    "!**/.vault-token",
    "!**/.dockercfg",
    "!**/.yarnrc.yml",
    "!**/credentials",
    "!**/credentials.json",
    "!**/secrets",
    "!**/secrets.json",
    "!**/id_rsa*",
    "!**/id_dsa*",
    "!**/id_ecdsa*",
    "!**/id_ed25519*",
    "!**/*.pem",
    "!**/*.key",
    "!**/*.p12",
    "!**/*.pfx",
    "!**/*.keystore",
    "!**/*.jks",
    "!**/*.ppk",
    "!**/*.p8",
    "!**/*.kdbx",
    "!**/.ssh/**",
    "!**/.aws/**",
    "!**/.azure/**",
    "!**/.gnupg/**",
    "!**/.kube/**",
    "!**/.minimalcode/**",
    "!**/.docker/**",
    "!**/.password-store/**",
)


def _normalized_parts(path: Path) -> list[str]:
    """Lowercase path components, folding Windows name quirks when present."""
    parts = [part.lower() for part in path.parts]
    if os.name == "nt":
        # NTFS ignores trailing dots/spaces on lookup, and `name:stream` opens
        # an alternate data stream of `name`; neither spelling should dodge
        # the name rules.
        normalized: list[str] = []
        for part in parts:
            part = part.rstrip(" .")
            if part and part[-1] != "\\":
                part = part.split(":", 1)[0]
            normalized.append(part)
        parts = normalized
    return parts


def is_sensitive_path(path: Path) -> bool:
    """Return True for credential-like paths that the sandbox blocks by default."""
    parts = _normalized_parts(path)
    for part in parts:
        if part in SENSITIVE_DIRECTORY_NAMES:
            return True
    if not parts:
        return False
    name = parts[-1]
    # Checked before the generic name rules so `.env.example` stays readable.
    if name in SAFE_ENV_TEMPLATES:
        return False
    if name in SENSITIVE_FILE_NAMES:
        return True
    if name.startswith((".env.", ".env-")):
        return True
    if name.startswith(SENSITIVE_KEY_PREFIXES) and not name.endswith(".pub"):
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
