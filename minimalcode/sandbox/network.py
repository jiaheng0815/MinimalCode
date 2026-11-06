"""Fail-closed network heuristics for shell commands.

The local backend has no network namespace, so a command string is inspected
before execution. Backends that can isolate the network do it for real; this
gate is what makes the default ``deny`` meaningful on every platform.
"""

from __future__ import annotations

import base64
import binascii
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
        # `npx`/`bunx` always resolve a package before running it, which reaches
        # the registry even though neither name looks like a download client.
        "npx",
        "bunx",
    }
)
# Programs whose subcommand, not name, decides whether they reach the network.
# Options before the subcommand are skipped, so `pip -q install x` and
# `git -C . clone x` are still recognized instead of hiding the subcommand.
_NETWORK_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({"clone", "fetch", "pull", "push", "ls-remote", "submodule"}),
    "pip": frozenset({"install", "download", "search", "index"}),
    "pip3": frozenset({"install", "download", "search", "index"}),
    "npm": frozenset({"install", "add", "i", "ci", "exec", "x"}),
    "pnpm": frozenset({"install", "add", "i", "dlx", "update"}),
    "yarn": frozenset({"install", "add", "dlx", "upgrade"}),
    "cargo": frozenset({"install", "add", "update"}),
    "gem": frozenset({"install", "update"}),
    "go": frozenset({"get", "install"}),
    "composer": frozenset({"install", "add", "require", "update"}),
    "dotnet": frozenset({"add", "restore", "tool"}),
    "apt": frozenset({"install", "update", "upgrade", "dist-upgrade"}),
    "apt-get": frozenset({"install", "update", "upgrade", "dist-upgrade"}),
    "brew": frozenset({"install", "update", "upgrade", "tap"}),
    "dnf": frozenset({"install", "update", "upgrade"}),
    "yum": frozenset({"install", "update", "upgrade"}),
    "apk": frozenset({"add", "update", "upgrade"}),
    "docker": frozenset({"pull", "push", "login", "run", "build"}),
    "uv": frozenset({"add", "sync", "install", "run"}),
    "poetry": frozenset({"add", "install", "update"}),
    "openssl": frozenset({"s_client", "s_server"}),
}
# Options that consume the following token. Only the common ones are listed;
# an unknown option is skipped alone, which is enough for the subcommand to
# surface after at most a few tokens.
_SUBCOMMAND_OPTION_VALUES: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "-C",
            "-c",
            "--git-dir",
            "--work-tree",
            "--namespace",
            "--exec-path",
            "--config-env",
        }
    ),
    "pip": frozenset(
        {
            "-r",
            "--requirement",
            "-e",
            "--editable",
            "-t",
            "--target",
            "-c",
            "--constraint",
            "-i",
            "--index-url",
            "--extra-index-url",
            "-f",
            "--find-links",
            "--trusted-host",
            "--python-version",
        }
    ),
    "npm": frozenset(
        {
            "--prefix",
            "--registry",
            "--cache",
            "--userconfig",
            "--globalconfig",
            "--workspace",
            "-w",
        }
    ),
    "pnpm": frozenset({"--dir", "-C", "--filter", "-F"}),
    "yarn": frozenset({"--cwd", "--modules-folder"}),
    "uv": frozenset({"--directory", "--project", "--cache-dir"}),
    "docker": frozenset({"-H", "--host", "--context", "-c"}),
}
_SUBCOMMAND_OPTION_VALUES["pip3"] = _SUBCOMMAND_OPTION_VALUES["pip"]
# Interpreter-only checks. `match` (not `search`) keeps `echo git clone` and
# `rg "pip install"` from matching.
_COMMAND_SEQUENCES = (
    re.compile(
        r"\bpython[0-9.]*\s+-m\s+pip\b[^|;&\n]*?\b(?:install|download|search|index)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bpython[0-9.]*\s+-m\s+(?:http\.server|requests)\b", re.IGNORECASE),
)
# Code that reaches the network from inside an inline `python -c`/`node -e`
# script. It is searched only in nested command strings, never in the outer
# command, so `rg "urllib.request"` is not misread as a network call.
_NESTED_NETWORK_API = re.compile(
    r"\b(?:requests|urllib3?|httpx|http\.client|ftplib|socket|urlopen|urlretrieve)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(
    r"\b(?:https?|ftps?)://(?:[^/@\s'\"`?#]*@)?"
    r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
# curl options that can redirect a request away from the URL host: the
# allowlist must see the resolver/proxy target as well, not just the URL.
_HOST_OPTION_RE = re.compile(
    r"(?:^|\s)(?P<flag>--resolve|--connect-to|--proxy|--preproxy|--socks5|"
    r"--socks5-hostname|--url|-x)(?:=|\s+)(?P<value>\S+)",
    re.IGNORECASE,
)
_POWERSHELL_KINDS = frozenset({"powershell", "pwsh"})
_FIND_COMMAND_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# Control operators that end a command. They are only separators outside quotes:
# splitting the raw string turned `rg 'nc|netcat'` into two commands and denied
# an ordinary source search.
_SEGMENT_CHARS = frozenset("|;&\n\r(){}`")
# Prefixes that do not change which program runs next: `sudo curl ...` still
# starts curl, so skipping them keeps the head token meaningful.
_PREFIX_WORDS = frozenset(
    {
        "!",
        "busybox",
        "command",
        "do",
        "doas",
        "elif",
        "else",
        "env",
        "exec",
        "for",
        "if",
        "iex",
        "invoke-expression",
        "caffeinate",
        "nohup",
        "setsid",
        "start",
        "start-process",
        "sudo",
        "then",
        "time",
        "toybox",
        "unbuffer",
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
        "runuser",
        "sh",
        "su",
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
    # Process launchers and tracers: the program they run is the real command.
    "watch": {"-n": 1, "--interval": 1, "-d": 0, "-q": 0, "-t": 0, "-b": 0, "-e": 0},
    "strace": {
        "-e": 1,
        "-o": 1,
        "-p": 1,
        "-s": 1,
        "-u": 1,
        "-a": 1,
        "-b": 1,
        "-I": 1,
        "-X": 1,
    },
    "ltrace": {"-e": 1, "-o": 1, "-p": 1, "-s": 1, "-u": 1, "-a": 1},
    "stdbuf": {"-i": 1, "-o": 1, "-e": 1},
    "systemd-run": {
        "--unit": 1,
        "--description": 1,
        "--property": 1,
        "-p": 1,
        "-E": 1,
    },
    "taskset": {"-c": 1, "-p": 1},
    "ionice": {"-c": 1, "-n": 1, "-p": 1},
}
_WRAPPERS = frozenset(_WRAPPER_ARGUMENTS)
_MAX_NESTED_COMMANDS = 8
_ESCAPE_CHARS = str.maketrans("", "", "\\`'\"^")


def _shell_words(segment: str) -> list[tuple[str, int, int]]:
    """Split one segment into words, removing quotes and concatenating parts.

    A shell treats ``c"url"`` and ``c\\url`` as the single command ``curl``.
    Tokenizing on alphanumeric runs split those into harmless pieces, so this
    small scanner keeps quoted fragments attached to their neighbours while
    still returning the source offsets used to slice an inline command.
    """
    words: list[tuple[str, int, int]] = []
    buffer: list[str] = []
    start: int | None = None
    quote: str | None = None
    for index, char in enumerate(segment):
        if quote is not None:
            if char == quote:
                quote = None
            else:
                buffer.append(char)
            continue
        if char in {"'", '"'}:
            if start is None:
                start = index
            quote = char
            continue
        if char.isspace():
            if start is not None:
                words.append(("".join(buffer), start, index))
                buffer = []
                start = None
            continue
        if start is None:
            start = index
        buffer.append(char)
    if start is not None:
        words.append(("".join(buffer), start, len(segment)))
    return words


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


def _is_encoded_command_flag(token: str) -> bool:
    """Return True for PowerShell's ``-EncodedCommand`` and its abbreviations."""
    if not token.startswith("-"):
        return False
    name = token.lower().lstrip("-")
    if not name:
        return False
    # pwsh accepts any prefix of `-EncodedCommand` (even `-e`) plus the
    # initialism `-ec`; every other spelling is left to the inline flags.
    return "encodedcommand".startswith(name) or name == "ec"


def _inline_command_text(
    segment: str, words: list[tuple[str, int, int]], position: int
) -> str:
    """Return one inline command argument, keeping its quoted body intact.

    The argument runs to the end of the segment because PowerShell's
    ``-Command`` takes the rest of the line, but the quoted head must be
    unquoted: slicing up to the closing quote left the quotes in place, so
    ``sh -c "iwr http://x" arg0`` reduced to the host ``x`` instead of the
    ``iwr`` head and the gate never saw the client.
    """
    _value, start, end = words[position]
    head = _strip_outer_quotes(segment[start:end])
    return (head + segment[end:]).strip()


def _segments(command: str) -> list[str]:
    """Split a command on unquoted control operators.

    Quoted operators are data: `rg -n 'nc|netcat'` searches for that alternation
    instead of running netcat, so the split must not create a new command head
    inside a quoted argument.
    """
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    for char in command:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char in _SEGMENT_CHARS:
            segments.append("".join(current))
            current = []
            continue
        current.append(char)
    segments.append("".join(current))
    return segments


_WINDOWS_SCRIPT_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")


def _head_name(token: str) -> str:
    """Return a bare, lowercase executable name from a command token."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in _WINDOWS_SCRIPT_SUFFIXES:
        if name.endswith(suffix):
            # `curl.cmd` and `wget.ps1` resolve to the same client as `curl`.
            return name[: -len(suffix)]
    return name


def _head_variants(token: str) -> list[str]:
    """Return the executable names a token can denote after shell escapes.

    Bash/PowerShell allow ``c\\url``, ``c"url"``, and ``c`url`` to invoke
    ``curl``. The raw spelling is kept first so ordinary Windows paths still
    resolve, and a quote/escape-free spelling is added when it differs.
    """
    raw = _head_name(token)
    name = _head_name(token.translate(_ESCAPE_CHARS))
    return [raw] if name == raw else [raw, name]


def _decode_powershell_command(value: str) -> str | None:
    """Decode a PowerShell -EncodedCommand value, or None when it is opaque."""
    compact = value.strip()
    if not compact:
        return None
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return None


def _scan_commands(command: str) -> tuple[list[str], list[str], bool]:
    """Return heads, nested inline command strings, and an opaque flag.

    A string nested in ``sh -c``/``pwsh -Command`` is sliced from the original
    segment instead of being rebuilt from tokens: joining tokens dropped the
    ``;``/``&`` separators, so ``sh -c 'true; curl x'`` looked like one
    harmless ``true`` command.
    """
    heads: list[str] = []
    inline: list[str] = []
    opaque = False
    for segment in _segments(command):
        words = _shell_words(segment)
        tokens = [word for word, _start, _end in words]
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
            if token.startswith("-"):
                # An option at command position belongs to a wrapper such as
                # Start-Process (``Start-Process -FilePath curl``).
                index += 1
                continue
            heads.append(token)
            program_names = list(_head_variants(token))
            program = program_names[0]
            if program == "find":
                # `find . -exec curl ... +` runs a command from a token that is
                # not at the start of the segment (a `\;` variant already splits
                # into its own segment).
                for position in range(index + 1, len(tokens) - 1):
                    if tokens[position].lower() not in _FIND_COMMAND_FLAGS:
                        continue
                    heads.append(tokens[position + 1])
                    inner = _inline_command_text(segment, words, position + 1)
                    if inner:
                        inline.append(inner)
                    break
            if any(name in _INLINE_COMMAND_SHELLS for name in program_names):
                is_powershell = any(name in _POWERSHELL_KINDS for name in program_names)
                for position in range(index + 1, len(tokens)):
                    flag = tokens[position].lower()
                    encoded = is_powershell and _is_encoded_command_flag(flag)
                    if not encoded and not _is_inline_flag(tokens[position]):
                        continue
                    if position + 1 >= len(tokens):
                        if encoded:
                            opaque = True
                        break
                    value = tokens[position + 1]
                    if encoded:
                        decoded = _decode_powershell_command(value)
                        if decoded is None:
                            opaque = True
                        elif decoded.strip():
                            inline.append(decoded)
                    else:
                        # The inline command is rescanned on its own, so adding
                        # its first token as a head only made `sh -c "rm ./curl"`
                        # look like a call to curl.
                        inner = _inline_command_text(segment, words, position + 1)
                        if inner:
                            inline.append(inner)
                    break
            break
    return heads, inline, opaque


def _strip_outer_quotes(text: str) -> str:
    """Drop one pair of quotes that wrapped the whole inline command."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        return stripped[1:-1]
    return stripped


def _command_tokens(segment: str) -> list[str]:
    """Return tokens starting at the command position of one segment."""
    tokens = [word for word, _start, _end in _shell_words(segment)]
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
        if token.startswith("-"):
            index += 1
            continue
        break
    return tokens[index:]


def _network_subcommand(program: str, arguments: list[str]) -> bool:
    """Return True when a package/git-style program runs a network subcommand."""
    subcommands = _NETWORK_SUBCOMMANDS.get(program)
    if not subcommands:
        return False
    valued = _SUBCOMMAND_OPTION_VALUES.get(program, frozenset())
    remaining = list(arguments)
    # A few option/value pairs can precede the subcommand (`git -C . clone`,
    # `npm --prefix x install`). Six probes cover them without scanning the
    # whole command line and mistaking a later argument for a subcommand.
    for _ in range(6):
        while remaining and remaining[0].startswith("-"):
            option = remaining.pop(0).lower()
            if option in valued and remaining:
                remaining.pop(0)
        if not remaining:
            return False
        token = remaining.pop(0).lower()
        if token in subcommands:
            return True
        if program == "uv" and token == "pip":
            continue
        return False
    return False


def _sequence_detected(command: str, depth: int = 0) -> bool:
    """Check network subcommands at command positions, including `sh -c`."""
    if depth > _MAX_NESTED_COMMANDS:
        # A command nested past the budget is opaque, not clean: failing open
        # here let one extra `sh -c` layer hide a download.
        return True
    for segment in _segments(command):
        tokens = _command_tokens(segment)
        if not tokens:
            continue
        programs = _head_variants(tokens[0])
        if any(_network_subcommand(program, tokens[1:]) for program in programs):
            return True
        for program in programs:
            candidate = " ".join([program, *tokens[1:]])
            if any(pattern.match(candidate) for pattern in _COMMAND_SEQUENCES):
                return True
    _heads, inline, opaque = _scan_commands(command)
    if opaque:
        return True
    for inner in inline:
        if _NESTED_NETWORK_API.search(inner):
            return True
        if _sequence_detected(inner, depth + 1):
            return True
    return False


def _all_command_heads(command: str, depth: int = 0) -> list[str]:
    """Return every program name the command starts, including inline shells.

    Only command positions count. Scanning every token (the earlier behaviour)
    made `git log --grep=curl` and `rg curl` look like downloads, so the gate
    refused ordinary source searches for the word "curl".
    """
    heads, inline, _opaque = _scan_commands(command)
    if depth >= _MAX_NESTED_COMMANDS:
        return heads
    for inner in inline:
        heads.extend(_all_command_heads(inner, depth + 1))
    return heads


def _split_host_option(value: str) -> list[str]:
    """Split a curl host option value, keeping bracketed IPv6 literals intact."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        if char == ":" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip("[]") for part in parts]


def _host_from_value(value: str) -> str:
    """Reduce a URL or host[:port] option value to a bare hostname."""
    text = value.strip().strip("'\"")
    if "://" in text:
        try:
            return (urlsplit(text).hostname or "").rstrip(".").lower()
        except ValueError:
            return ""
    authority = text.split("/", 1)[0].rsplit("@", 1)[-1]
    if authority.startswith("["):
        return authority[1 : authority.find("]")].lower() if "]" in authority else ""
    return authority.split(":", 1)[0].rstrip(".").lower()


def _network_hosts(command: str) -> list[str]:
    """Return every host a command can contact, including resolver overrides."""
    hosts: list[str] = []

    def add(candidate: str) -> None:
        host = candidate.rstrip(".").lower()
        if host and host not in hosts:
            hosts.append(host)

    for match in _URL_PATTERN.finditer(command):
        authority = match.group(1)
        if authority.startswith("["):
            add(authority[1:].split("]", 1)[0])
        else:
            add(authority.split(":", 1)[0])
    for match in _HOST_OPTION_RE.finditer(command):
        flag = match.group("flag").lower()
        value = match.group("value")
        if flag in {"--resolve", "--connect-to"}:
            parts = _split_host_option(value)
            # HOST:PORT:ADDR (--resolve) and HOST1:PORT1:HOST2:PORT2
            # (--connect-to) both put the real target in the third field.
            for candidate in parts[0:1] + parts[2:3]:
                add(candidate)
        else:
            add(_host_from_value(value))
    return hosts


def network_signals(command: str) -> tuple[bool, tuple[str, ...]]:
    """Detect obvious network use and the hosts named on the command line.

    The detection is intentionally conservative: a shell string is opaque, so
    this is a policy gate for the local baseline rather than a proof. Hard
    isolation is provided by the container backends.
    """
    # `cmd` escapes with `^` (`cu^rl`) and PowerShell uses a backtick for the
    # same purpose, so the analysis runs on an escape-free copy as well. Only
    # command positions are inspected, so a literal escape in a quoted pattern
    # stays harmless.
    normalized = (
        command.replace("^", "").replace("`", "")
        if "^" in command or "`" in command
        else command
    )
    hosts = _network_hosts(normalized)
    detected = False
    for head in _all_command_heads(normalized):
        # Compare bare executable names so `/usr/bin/curl`, `curl.exe`, and
        # `c\url` all match the same entry.
        if any(name in _NETWORK_COMMANDS for name in _head_variants(head)):
            detected = True
            break
    if not detected:
        detected = _sequence_detected(normalized)
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
