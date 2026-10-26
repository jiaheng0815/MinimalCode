"""Fail-closed network heuristics for shell commands.

The local backend has no network namespace, so a command string is inspected
before execution. Backends that can isolate the network do it for real; this
gate is what makes the default ``deny`` meaningful on every platform.
"""

from __future__ import annotations

import re

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
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./:\\-]+")


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
    for raw in _TOKEN_PATTERN.findall(command):
        # Compare bare executable names so `/usr/bin/curl` and `curl.exe`
        # match the same entry.
        token = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
        token = token.removesuffix(".exe")
        if token in _NETWORK_COMMANDS:
            detected = True
            break
    if not detected:
        detected = any(pattern.search(command) for pattern in _NETWORK_SEQUENCES)
    return detected, tuple(hosts)


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """Return True when ``host`` matches an allowlist entry or its subdomain."""
    for entry in allowed:
        candidate = entry.strip().lower().lstrip(".")
        if not candidate:
            continue
        if host == candidate or host.endswith("." + candidate):
            return True
    return False
