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
        # direnv exports real tokens from `.envrc`, one character away from the
        # `.env` that is already blocked.
        ".envrc",
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
# Credential files are just as often YAML/TOML (`secrets.yaml`,
# `credentials.toml`); matching only the bare and `.json` spellings left the
# most common compose/Ansible layout readable.
SENSITIVE_FILE_STEMS = ("credentials", "secrets")
SENSITIVE_STEM_SUFFIXES = (".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg", ".env")
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
# `.git/config` often carries a remote URL with an embedded token, while the
# rest of `.git/` (HEAD, refs) is harmless and useful to read.
SENSITIVE_PATH_SUFFIXES = ((".git", "config"),)
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
    "!**/.envrc",
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
    # Listed individually: `credentials.*` would also hide `credentials.py`,
    # which `is_sensitive_path` allows and `read` can still open.
    "!**/credentials.yaml",
    "!**/credentials.yml",
    "!**/credentials.toml",
    "!**/secrets",
    "!**/secrets.json",
    "!**/secrets.yaml",
    "!**/secrets.yml",
    "!**/secrets.toml",
    "!**/.git/config",
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
    "!**/.password-store/**",
)
# Word separators and quote handling for `sensitive_command_paths`. The shell
# gate only sees a string, so it needs its own minimal scanner.
_COMMAND_WORD_BREAK = frozenset(" \t\r\n|;&(){}<>`")
_COMMAND_QUOTES = frozenset("'\"")
_COMMAND_WORD_TRIM = "=,:;|&" + "\"'`"
# A quoted word after one of these is a command, not data: `cmd /c "type .env"`
# and `pwsh -Command "Get-Content .env"` must be inspected like a bare command.
_INLINE_FLAGS = frozenset(
    {
        "-c",
        "/c",
        "/k",
        "-command",
        "--command",
        "-commandwithargs",
        "-lc",
        "-ic",
    }
)
_MAX_INLINE_DEPTH = 3


# The sandbox only sees a string and cannot know whether a POSIX shell (`\"`)
# or PowerShell (`` `" ``) will run it. Ignoring escapes inverted quote parity:
# `printf "a\"b"; cat .env` made the whole tail look quoted, so the `cat`
# returned the credential the bare command is refused for. Each dialect is
# analysed with its escaped quote masked out and the findings are unioned, so
# an escape spelling can only add hits.
_MASKED_QUOTE = "\ue000"


def _join_continuations(command: str) -> str:
    """Remove the line continuations a shell removes before it tokenizes."""
    for escape in ("\\", "`", "^"):
        command = command.replace(escape + "\r\n", "").replace(escape + "\n", "")
    return command


def _escape_variants(command: str) -> tuple[str, ...]:
    """Return the command plus one spelling per quote-escape dialect."""
    joined = _join_continuations(command)
    variants = [joined]
    for escape in ("\\", "`"):
        if escape + '"' in joined or escape + "'" in joined:
            variants.append(
                joined.replace(escape + '"', _MASKED_QUOTE).replace(
                    escape + "'", _MASKED_QUOTE
                )
            )
    return tuple(variants)


def _looks_sensitive_token(token: str) -> bool:
    """Return True when one shell word names a sensitive path."""
    candidate = token.split("=", 1)[-1] if "=" in token else token
    candidate = candidate.strip(_COMMAND_WORD_TRIM)
    if candidate.startswith("-"):
        # PowerShell attaches an option value with a colon (`-Path:.env`), so a
        # word starting with `-` can still carry the path; only a word that is
        # a bare option name is data-free.
        _name, separator, value = candidate.partition(":")
        if not separator:
            return False
        candidate = value.strip(_COMMAND_WORD_TRIM)
    if not candidate:
        return False
    try:
        return is_sensitive_path(Path(candidate).expanduser())
    except (OSError, RuntimeError, ValueError):
        return False


def _command_words(command: str) -> list[tuple[str, bool]]:
    """Split a command line into ``(word, quoted)`` pairs."""
    words: list[tuple[str, bool]] = []
    word: list[str] = []
    quote = ""
    quoted = False
    for char in command:
        if quote:
            if char == quote:
                quote = ""
            else:
                word.append(char)
            continue
        if char in _COMMAND_QUOTES:
            quote = char
            quoted = True
            continue
        if char in _COMMAND_WORD_BREAK:
            if word:
                words.append(("".join(word), quoted))
            word = []
            quoted = False
            continue
        word.append(char)
    if word:
        words.append(("".join(word), quoted))
    return words


def sensitive_command_paths(command: str, depth: int = 0) -> tuple[str, ...]:
    """Return words in a shell command that name a sensitive path.

    `read` and `write` refuse `.env` / `~/.ssh/id_rsa`, but the shell only took
    a command string, so `type .env` and `cat ~/.ssh/id_rsa` returned the same
    credentials the tools refuse. A word inside quotes is usually data
    (`git commit -m "document .env"`), except when it is the command handed to
    `sh -c` / `cmd /c` / `pwsh -Command`, which is scanned in turn.
    """
    hits: list[str] = []
    for variant in _escape_variants(command):
        for hit in _scan_command_paths(variant, depth):
            if hit not in hits:
                hits.append(hit)
    return tuple(hits)


def _scan_command_paths(command: str, depth: int) -> tuple[str, ...]:
    """Scan one dialect spelling of ``command`` for sensitive words."""
    words = _command_words(command)
    hits: list[str] = []
    previous = ""
    for token, quoted in words:
        if quoted and previous.lower() in _INLINE_FLAGS and depth < _MAX_INLINE_DEPTH:
            for hit in sensitive_command_paths(token, depth + 1):
                if hit not in hits:
                    hits.append(hit)
            previous = token
            continue
        if (
            _looks_sensitive_token(token)
            and token not in hits
            and not _directory_option_value(token, previous)
        ):
            hits.append(token)
        previous = token
    return tuple(hits)


def _directory_option_value(token: str, option: str) -> bool:
    """Return True for a directory-valued long option such as `--home`.

    The agent itself runs `minimalcode --home <dir>`, and a config directory
    named `.MinimalCode` matched the sensitive-directory rule, so every nested
    agent was refused. Naming a directory is not reading a credential: the
    exemption needs a `--long` option *and* a bare directory name, so
    `Get-Content -Path "C:\\x\\.ssh\\id_rsa"` is still denied.
    """
    if not option.startswith("--"):
        return False
    candidate = token.strip(_COMMAND_WORD_TRIM)
    if not candidate:
        return False
    try:
        parts = _normalized_parts(Path(candidate).expanduser())
    except (OSError, RuntimeError, ValueError):
        return False
    if not parts:
        return False
    name = parts[-1]
    return name in SENSITIVE_DIRECTORY_NAMES and name not in SENSITIVE_FILE_NAMES


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
    for stem in SENSITIVE_FILE_STEMS:
        if name == stem or (
            name.startswith(stem + ".") and name.endswith(SENSITIVE_STEM_SUFFIXES)
        ):
            return True
    if tuple(parts[-2:]) in SENSITIVE_PATH_SUFFIXES:
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
