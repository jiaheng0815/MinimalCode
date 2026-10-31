"""Fail-closed network heuristics for shell commands.

The local backend has no network namespace, so a command string is inspected
before execution. Backends that can isolate the network do it for real; this
gate is what makes the default ``deny`` meaningful on every platform.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_NETWORK_COMMANDS = frozenset(
    {
        "curl",
        "wget",
        "aria2c",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "ftp",
        "telnet",
        "ping",
        "dig",
        "nslookup",
        "tracert",
        "traceroute",
        "iwr",
        "irm",
        "invoke-webrequest",
        "invoke-restmethod",
        "start-bitstransfer",
        "bitsadmin",
    }
)
_NETWORK_SEQUENCES = (
    re.compile(
        r"\bgit\s+(?:clone|fetch|pull|push|ls-remote|submodule)\b", re.IGNORECASE
    ),
    re.compile(r"\bpip[0-9.]*\s+(?:install|download|search|index)\b", re.IGNORECASE),
    re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:install|add|i|ci)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:cargo|gem|go|composer|dotnet)\s+(?:install|get|add|restore)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bapt(?:-get)?\s+(?:install|update|upgrade)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:brew|dnf|yum|apk|pacman)\s+(?:install|add|upgrade)\b", re.IGNORECASE
    ),
    re.compile(r"\bdocker\s+(?:pull|push|login|run)\b", re.IGNORECASE),
    # `python -m pip show/list` and friends are read-only; only the subcommands
    # that actually reach an index are treated as network use.
    re.compile(
        r"\bpython[0-9.]*\s+-m\s+pip\b[^|;&\n]*?\b(?:install|download|search|index)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bpython[0-9.]*\s+-m\s+(?:http\.server|requests)\b", re.IGNORECASE),
    re.compile(r"\b(?:requests|urllib|httpx|socket)\.", re.IGNORECASE),
)
_URL_PATTERN = re.compile(r"\b(?:https?|ftps?)://([^\s'\"`/?#]+)", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./:\\=-]+")
_SEGMENT_PATTERN = re.compile(r"\|\||&&|[|;&\n\r(){}`]")
# Prefixes that do not change which program runs next: `sudo curl ...` still
# starts curl, so skipping them keeps the head token meaningful.
_PREFIX_WORDS = frozenset(
    {
        "!",
        "command",
        "do",
        "doas",
        "elif",
        "else",
        "env",
        "exec",
        "for",
        "if",
        "nohup",
        "sudo",
        "then",
        "time",
        "until",
        "while",
    }
)
# Shells take the command as a quoted argument instead of as a head token;
# without this, `pwsh -Command "iwr https://x"` would look harmless. Only real
# interpreters count: `grep -c curl` must not be read as running curl.
_INLINE_COMMAND_FLAGS = frozenset(
    {"-c", "-e", "-command", "-commandwithargs", "/c", "/k", "--command"}
)
_INLINE_COMMAND_SHELLS = frozenset(
    {
        "bash",
        "cmd",
        "dash",
        "ksh",
        "node",
        "perl",
        "powershell",
        "pwsh",
        "python",
        "python3",
        "ruby",
        "sh",
        "zsh",
    }
)
# Wrappers run another program. The options that take a separate value must be
# skipped with that value; otherwise `sudo -u root curl ...` treats `-u` as the
# command and never sees curl.
_WRAPPER_ARGUMENTS: dict[str, dict[str, int]] = {
    "sudo": {"-u": 1, "-g": 1, "-p": 1, "-C": 1, "-h": 1, "-U": 1, "-r": 1},
    "timeout": {"-s": 1, "-k": 1, "--signal": 1, "--kill-after": 1},
    "env": {"-u": 1, "-C": 1, "-S": 1, "--unset": 1, "--chdir": 1},
    "nice": {"-n": 1, "--adjustment": 1},
    "xargs": {"-I": 1, "-n": 1, "-P": 1, "-d": 1, "-E": 1, "-L": 1, "-s": 1},
}
_WRAPPERS = frozenset(_WRAPPER_ARGUMENTS)
_QUOTED_TEXT = re.compile(r"'[^']*'|\"[^\"]*\"")
_MAX_NESTED_COMMANDS = 8


def _skip_wrapper(tokens: list[str], index: int, wrapper: str) -> int:
    """Advance past a wrapper and its options to the command it wraps."""
    index += 1
    arguments = _WRAPPER_ARGUMENTS[wrapper]
    seen_duration = False
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            count = arguments.get(token, 0)
            index += 1 + count
            continue
        if wrapper == "timeout" and not seen_duration:
            # The first positional argument is the duration, not a command.
            seen_duration = True
            index += 1
            continue
        return index
    return index


def _is_inline_flag(token: str) -> bool:
    lowered = token.lower()
    if lowered in _INLINE_COMMAND_FLAGS:
        return True
    # Combined short flags such as `-lc`, `-ec`, or `-ic` still end in `c`,
    # which is what makes the interpreter read the next argument as a command.
    return (
        lowered.startswith("-")
        and not lowered.startswith("--")
        and len(lowered) > 1
        and lowered.endswith("c")
    )


def _scan_commands(command: str) -> tuple[list[str], list[str]]:
    """Return command-position heads and nested inline command strings."""
    heads: list[str] = []
    inline: list[str] = []
    for segment in _SEGMENT_PATTERN.split(command):
        tokens = _TOKEN_PATTERN.findall(segment)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            lowered = token.lower()
            if "=" in token and not token.startswith("-"):
                index += 1  # VAR=value prefix
                continue
            if lowered in _WRAPPERS:
                index = _skip_wrapper(tokens, index, lowered)
                continue
            if lowered in _PREFIX_WORDS:
                index += 1
                continue
            heads.append(token)
            head = token.replace("\\", "/").rsplit("/", 1)[-1]
            if head.lower().removesuffix(".exe") in _INLINE_COMMAND_SHELLS:
                for position in range(index + 1, len(tokens) - 1):
                    if _is_inline_flag(tokens[position]):
                        heads.append(tokens[position + 1])
                        inner = " ".join(tokens[position + 1 :])
                        if inner:
                            inline.append(inner)
                        break
            break
    return heads, inline


def _command_heads(command: str) -> list[str]:
    """Return the program names this command string would start.

    Only command positions are inspected. Scanning every token (the earlier
    behaviour) made `git log --grep=curl` and `rg curl` look like downloads, so
    the gate refused ordinary source searches for the word "curl".
    """
    return _scan_commands(command)[0]


def _command_position_text(segment: str) -> str:
    """Return the part of one segment that starts a command."""
    tokens = _TOKEN_PATTERN.findall(_QUOTED_TEXT.sub(" ", segment))
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if "=" in token and not token.startswith("-"):
            index += 1
            continue
        if lowered in _WRAPPERS:
            index = _skip_wrapper(tokens, index, lowered)
            continue
        if lowered in _PREFIX_WORDS:
            index += 1
            continue
        break
    return " ".join(tokens[index:])


def _sequence_detected(command: str, depth: int = 0) -> bool:
    """Check network subcommands at command positions, including `sh -c`."""
    if depth > _MAX_NESTED_COMMANDS:
        return False
    for segment in _SEGMENT_PATTERN.split(command):
        # ``match`` (not ``search``) keeps `echo git clone` and
        # `rg "pip install"` from looking like a download.
        candidate = _command_position_text(segment)
        if candidate and any(
            pattern.match(candidate) for pattern in _NETWORK_SEQUENCES
        ):
            return True
    return any(
        _sequence_detected(inner, depth + 1) for inner in _scan_commands(command)[1]
    )


def network_signals(command: str) -> tuple[bool, tuple[str, ...]]:
    """Detect obvious network use and the hosts named on the command line.

    The detection is intentionally conservative: a shell string is opaque, so
    this is a policy gate for the local baseline rather than a proof. Hard
    isolation is provided by the container backends.
    """
    hosts: list[str] = []
    for match in _URL_PATTERN.finditer(command):
        authority = match.group(1).split("@")[-1]
        if authority.startswith("["):
            # IPv6 literals are bracketed and may contain colons.
            host = authority[1:].split("]", 1)[0]
        else:
            host = authority.split(":")[0]
        host = host.rstrip(".").lower()
        if host and host not in hosts:
            hosts.append(host)
    detected = False
    for head in _command_heads(command):
        # Compare bare executable names so `/usr/bin/curl` and `curl.exe`
        # match the same entry.
        token = head.replace("\\", "/").rsplit("/", 1)[-1].lower()
        token = token.removesuffix(".exe")
        if token in _NETWORK_COMMANDS:
            detected = True
            break
    if not detected:
        detected = _sequence_detected(command)
    return detected, tuple(hosts)


def _normalize_host(value: str) -> str:
    """Reduce a configured entry to a bare lowercase hostname."""
    text = value.strip().lower()
    if "://" in text:
        try:
            text = (urlsplit(text).hostname or "").lower()
        except ValueError:
            return ""
    text = text.lstrip(".")
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:
        # host:port, but leave bare IPv6 literals alone.
        text = text.split(":", 1)[0]
    return text.rstrip(".")


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """Return True when ``host`` matches an allowlist entry or its subdomain."""
    normalized_host = _normalize_host(host)
    for entry in allowed:
        candidate = _normalize_host(entry)
        if not candidate:
            continue
        if normalized_host == candidate or normalized_host.endswith("." + candidate):
            return True
    return False
