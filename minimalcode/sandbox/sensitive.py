"""Credential-like path detection and search exclusions.

The list is intentionally narrow: false positives make the agent unusable, so
this is a last line of defence rather than the only sandbox boundary.
"""

from __future__ import annotations

from pathlib import Path

SENSITIVE_DIRECTORY_NAMES = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".minimalcode",
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
        "credentials",
        "secrets",
        "secrets.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
SAFE_ENV_TEMPLATES = frozenset(
    {".env.example", ".env.sample", ".env.template", ".env.dist"}
)

# ripgrep globs that keep bulk searches from echoing credentials. They are
# exclusion-only: a positive re-include would widen a `find` pattern.
SENSITIVE_SEARCH_GLOBS: tuple[str, ...] = (
    "!**/.env",
    "!**/.env.*",
    "!**/.netrc",
    "!**/.npmrc",
    "!**/.pgpass",
    "!**/.git-credentials",
    "!**/.htpasswd",
    "!**/credentials",
    "!**/secrets",
    "!**/secrets.json",
    "!**/id_rsa",
    "!**/id_dsa",
    "!**/id_ecdsa",
    "!**/id_ed25519",
    "!**/*.pem",
    "!**/*.key",
    "!**/*.p12",
    "!**/*.pfx",
    "!**/*.keystore",
    "!**/*.jks",
    "!**/.ssh/**",
    "!**/.aws/**",
    "!**/.azure/**",
    "!**/.gnupg/**",
    "!**/.kube/**",
    "!**/.minimalcode/**",
)


def is_sensitive_path(path: Path) -> bool:
    """Return True for credential-like paths that the sandbox blocks by default."""
    parts = [part.lower() for part in path.parts]
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
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
