from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .errors import ConfigError, ToolError

SANDBOX_BACKENDS: tuple[str, ...] = (
    "auto",
    "local",
    "bwrap",
    "firejail",
    "sandbox-exec",
    "docker",
)
NETWORK_POLICIES: tuple[str, ...] = ("deny", "allowlist", "allow")

# Credentials, private keys, and cloud/CI secret stores. Only the cheap,
# unambiguous cases are listed: false positives make the agent unusable, and
# the list is a last line of defence rather than the only boundary.
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

# Fail-closed command heuristics for the local backend, which has no network
# namespace of its own. Backends that can isolate the network do it for real;
# this gate is what makes the default `deny` meaningful on every platform.
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
    re.compile(
        r"\bpython[0-9.]*\s+-m\s+(?:pip|http\.server|requests)\b", re.IGNORECASE
    ),
    re.compile(r"\b(?:requests|urllib|httpx|socket)\.", re.IGNORECASE),
)
_URL_PATTERN = re.compile(r"\b(?:https?|ftps?)://([^\s'\"`/?#]+)", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./:\\-]+")


def is_sensitive_path(path: Path) -> bool:
    """Return True for credential-like paths that the sandbox blocks by default."""
    parts = [part.lower() for part in path.parts]
    for part in parts:
        if part in SENSITIVE_DIRECTORY_NAMES:
            return True
    if not parts:
        return False
    name = parts[-1]
    if name in SAFE_ENV_TEMPLATES:
        return False
    if name in SENSITIVE_FILE_NAMES:
        return True
    if name.startswith((".env.", ".env-")):
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)


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
            host = authority[1:].split("]", 1)[0]
        else:
            host = authority.split(":")[0]
        host = host.rstrip(".").lower()
        if host and host not in hosts:
            hosts.append(host)
    detected = False
    for raw in _TOKEN_PATTERN.findall(command):
        token = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
        token = token.removesuffix(".exe")
        if token in _NETWORK_COMMANDS:
            detected = True
            break
    if not detected:
        detected = any(pattern.search(command) for pattern in _NETWORK_SEQUENCES)
    return detected, tuple(hosts)


def _host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        candidate = entry.strip().lower().lstrip(".")
        if not candidate:
            continue
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def _is_within(path: Path, root: Path) -> bool:
    """Case-insensitive containment check that works with already-resolved paths."""
    child = os.path.normcase(os.path.abspath(str(path)))
    parent = os.path.normcase(os.path.abspath(str(root))).rstrip(os.sep)
    if child == parent:
        return True
    return child.startswith(parent + os.sep)


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    enabled: bool
    backend: str
    allowed_roots: tuple[Path, ...]
    network_policy: str
    allowed_hosts: tuple[str, ...]
    timeout_seconds: float
    max_output_chars: int
    allow_sensitive_files: bool
    max_memory_mb: int
    max_processes: int
    docker_image: str
    workspace: Path

    @classmethod
    def disabled(cls, workspace: str | Path) -> SandboxPolicy:
        root = Path(workspace).resolve(strict=False)
        return cls(
            enabled=False,
            backend="local",
            allowed_roots=(root,),
            network_policy="allow",
            allowed_hosts=(),
            timeout_seconds=3600.0,
            max_output_chars=10_000_000,
            allow_sensitive_files=True,
            max_memory_mb=0,
            max_processes=0,
            docker_image="",
            workspace=root,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        workspace: str | Path,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> SandboxPolicy:
        """Build a policy from settings, treating malformed values as fatal.

        Configuration validation normally catches these cases, but the same
        builder is used by tests and embedders, so it must not silently widen
        the policy when a value is missing or wrong.
        """
        root = Path(workspace).resolve()
        raw = settings.get("sandbox", {}) if hasattr(settings, "get") else {}
        data: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        if overrides:
            data.update(
                {key: value for key, value in overrides.items() if value is not None}
            )

        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError("sandbox.enabled must be a boolean")
        backend = data.get("backend", "local")
        if not isinstance(backend, str) or backend not in SANDBOX_BACKENDS:
            raise ConfigError(
                f"sandbox.backend must be one of {', '.join(SANDBOX_BACKENDS)}"
            )
        network_policy = data.get("network_policy", "deny")
        if network_policy not in NETWORK_POLICIES:
            raise ConfigError(
                f"sandbox.network_policy must be one of {', '.join(NETWORK_POLICIES)}"
            )
        allowed_hosts = _string_tuple(
            data.get("allowed_hosts", ()), "sandbox.allowed_hosts"
        )
        if network_policy == "allowlist" and not allowed_hosts:
            raise ConfigError(
                "sandbox.network_policy is 'allowlist' but sandbox.allowed_hosts is empty"
            )

        roots: list[Path] = [root]
        for entry in _string_tuple(
            data.get("allowed_roots", ()), "sandbox.allowed_roots"
        ):
            try:
                candidate = Path(entry).expanduser()
                if not candidate.is_absolute():
                    candidate = root / candidate
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ConfigError(
                    f"invalid sandbox.allowed_roots entry: {entry!r}"
                ) from exc
            if resolved not in roots:
                roots.append(resolved)

        timeout = _number(data.get("timeout_seconds", 120), "sandbox.timeout_seconds")
        if not 1 <= timeout <= 3600:
            raise ConfigError("sandbox.timeout_seconds must be from 1 to 3600")
        max_output = data.get("max_output_chars", 60_000)
        if (
            isinstance(max_output, bool)
            or not isinstance(max_output, int)
            or max_output < 1
        ):
            raise ConfigError("sandbox.max_output_chars must be a positive integer")
        allow_sensitive = data.get("allow_sensitive_files", False)
        if not isinstance(allow_sensitive, bool):
            raise ConfigError("sandbox.allow_sensitive_files must be a boolean")
        max_memory = data.get("max_memory_mb", 2048)
        if (
            isinstance(max_memory, bool)
            or not isinstance(max_memory, int)
            or max_memory < 0
        ):
            raise ConfigError("sandbox.max_memory_mb must be a non-negative integer")
        max_processes = data.get("max_processes", 0)
        if (
            isinstance(max_processes, bool)
            or not isinstance(max_processes, int)
            or max_processes < 0
        ):
            raise ConfigError("sandbox.max_processes must be a non-negative integer")
        docker_image = data.get("docker_image", "alpine:3")
        if not isinstance(docker_image, str):
            raise ConfigError("sandbox.docker_image must be a string")

        return cls(
            enabled=enabled,
            backend=backend,
            allowed_roots=tuple(roots),
            network_policy=network_policy,
            allowed_hosts=allowed_hosts,
            timeout_seconds=timeout,
            max_output_chars=max_output,
            allow_sensitive_files=allow_sensitive,
            max_memory_mb=max_memory,
            max_processes=max_processes,
            docker_image=docker_image.strip(),
            workspace=root,
        )

    def contains(self, path: Path) -> bool:
        return any(_is_within(path, root) for root in self.allowed_roots)

    def write_roots(self, cwd: Path) -> tuple[Path, ...]:
        roots = list(self.allowed_roots)
        resolved_cwd = Path(cwd).resolve(strict=False)
        if not any(_is_within(resolved_cwd, root) for root in roots):
            # An explicit legacy opt-out still needs the working directory
            # mounted by filesystem-capable backends.
            roots.append(resolved_cwd)
        return tuple(roots)

    def network_denial(self, command: str) -> str | None:
        """Return a denial reason, or None when the command may run."""
        if not self.enabled or self.network_policy == "allow":
            return None
        detected, hosts = network_signals(command)
        if not detected:
            return None
        if self.network_policy == "deny":
            return (
                "network access is disabled by sandbox.network_policy=deny "
                "(rejected command resembles a network client)"
            )
        if not hosts:
            return (
                "sandbox.network_policy=allowlist cannot verify the target host "
                "for this command"
            )
        for host in hosts:
            if not _host_allowed(host, self.allowed_hosts):
                return f"host {host!r} is not in sandbox.allowed_hosts"
        return None


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{label} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{label} must contain non-empty strings")
        result.append(item.strip())
    return tuple(result)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigError(f"{label} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ConfigError(f"{label} must be a finite number")
    return numeric


@dataclass(frozen=True, slots=True)
class SandboxDenial:
    operation: str
    target: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "target": self.target,
            "reason": self.reason,
        }


class SandboxAudit:
    """Bounded in-memory audit trail of refused operations and notes."""

    def __init__(self, max_entries: int = 200) -> None:
        self._lock = threading.Lock()
        self._denials: deque[SandboxDenial] = deque(maxlen=max_entries)
        self._notes: deque[str] = deque(maxlen=max_entries)
        self.total_denials = 0

    def deny(self, operation: str, target: str, reason: str) -> None:
        with self._lock:
            self._denials.append(
                SandboxDenial(operation=operation, target=str(target), reason=reason)
            )
            self.total_denials += 1

    def note(self, message: str) -> None:
        with self._lock:
            self._notes.append(str(message))

    def denials(self) -> list[SandboxDenial]:
        with self._lock:
            return list(self._denials)

    def notes(self) -> list[str]:
        with self._lock:
            return list(self._notes)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_denials": self.total_denials,
                "recent": [denial.as_dict() for denial in self._denials],
                "notes": list(self._notes),
            }


class SandboxUnavailable(ToolError):
    """Raised when the selected backend cannot execute the requested input."""


@dataclass(frozen=True, slots=True)
class CommandRequest:
    argv: tuple[str, ...]
    command: str | None
    shell_kind: str
    shell_label: str
    cwd: Path
    temp_dir: Path | None
    policy: SandboxPolicy


class SandboxBackend:
    name = "base"
    capabilities: frozenset[str] = frozenset()

    def available(self) -> bool:
        return True

    def wrap(self, request: CommandRequest) -> list[str]:
        return list(request.argv)

    def temp_path(self, request: CommandRequest) -> str | None:
        """Path the child should use for TMPDIR, or None to disable the override."""
        return str(request.temp_dir) if request.temp_dir is not None else None


class LocalSandboxBackend(SandboxBackend):
    """Cross-platform baseline: policy layer plus subprocess limits."""

    name = "local"
    capabilities = frozenset({"argv", "shell", "resources"})


class BwrapBackend(SandboxBackend):
    name = "bwrap"
    capabilities = frozenset({"argv", "shell", "filesystem", "network"})

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    def wrap(self, request: CommandRequest) -> list[str]:
        argv = [
            shutil.which("bwrap") or "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
        ]
        if request.policy.network_policy != "deny":
            argv.append("--share-net")
        for root in request.policy.write_roots(request.cwd):
            argv.extend(["--bind", str(root), str(root)])
        if request.temp_dir is not None:
            argv.extend(["--bind", str(request.temp_dir), str(request.temp_dir)])
        argv.extend(["--chdir", str(request.cwd), "--", *request.argv])
        return argv


class FirejailBackend(SandboxBackend):
    name = "firejail"
    capabilities = frozenset({"argv", "shell", "network"})

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("firejail") is not None

    def wrap(self, request: CommandRequest) -> list[str]:
        argv = [
            shutil.which("firejail") or "firejail",
            "--quiet",
            "--private-tmp",
            "--nosound",
            "--no3d",
        ]
        if request.policy.network_policy == "deny":
            argv.append("--net=none")
        argv.extend(["--", *request.argv])
        return argv

    def temp_path(self, request: CommandRequest) -> str | None:
        # --private-tmp mounts an empty /tmp, so the host temp path is gone.
        return "/tmp" if request.temp_dir is not None else None


class MacSandboxExecBackend(SandboxBackend):
    name = "sandbox-exec"
    capabilities = frozenset({"argv", "shell", "network"})

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def wrap(self, request: CommandRequest) -> list[str]:
        profile = "(version 1)\n(allow default)\n"
        if request.policy.network_policy == "deny":
            profile += "(deny network*)\n"
        return [
            shutil.which("sandbox-exec") or "sandbox-exec",
            "-p",
            profile,
            *request.argv,
        ]


class DockerBackend(SandboxBackend):
    name = "docker"
    capabilities = frozenset({"shell", "filesystem", "network"})

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def wrap(self, request: CommandRequest) -> list[str]:
        if not request.command:
            raise SandboxUnavailable(
                "the docker sandbox backend can only run shell commands"
            )
        image = request.policy.docker_image
        if not image:
            raise SandboxUnavailable("sandbox.docker_image must not be empty")
        network = "none" if request.policy.network_policy == "deny" else "bridge"
        argv = [
            shutil.which("docker") or "docker",
            "run",
            "--rm",
            "--interactive",
            "--network",
            network,
        ]
        mounted: set[str] = set()
        for root in request.policy.write_roots(request.cwd):
            text = str(root)
            if text in mounted:
                continue
            mounted.add(text)
            argv.extend(["--volume", f"{text}:{text}"])
        argv.extend(
            [
                "--workdir",
                str(request.cwd),
                image,
                "sh",
                "-lc",
                request.command,
            ]
        )
        return argv

    def temp_path(self, request: CommandRequest) -> str | None:
        return "/tmp" if request.temp_dir is not None else None


def _auto_backend() -> SandboxBackend:
    candidates: tuple[SandboxBackend, ...] = (
        BwrapBackend(),
        FirejailBackend(),
        MacSandboxExecBackend(),
    )
    for backend in candidates:
        if backend.available():
            return backend
    return LocalSandboxBackend()


def backend_for(name: str) -> SandboxBackend:
    if name == "auto":
        return _auto_backend()
    factories: dict[str, type[SandboxBackend]] = {
        "local": LocalSandboxBackend,
        "bwrap": BwrapBackend,
        "firejail": FirejailBackend,
        "sandbox-exec": MacSandboxExecBackend,
        "docker": DockerBackend,
    }
    factory = factories.get(name)
    if factory is None:
        raise ConfigError(f"unknown sandbox backend: {name}")
    return factory()


def available_backends() -> dict[str, bool]:
    return {
        name: factory().available()
        for name, factory in (
            ("local", LocalSandboxBackend),
            ("bwrap", BwrapBackend),
            ("firejail", FirejailBackend),
            ("sandbox-exec", MacSandboxExecBackend),
            ("docker", DockerBackend),
        )
    }


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return ("", bool(text)) if text else ("", False)
    if len(text) <= limit:
        return text, False
    marker = f"\n... <truncated {len(text) - limit} chars> ...\n"
    if limit <= len(marker):
        return text[:limit], True
    available = limit - len(marker)
    head = int(available * 0.4)
    tail = available - head
    return text[:head] + marker + text[-tail:], True


class _Capture:
    """Keep a bounded head and tail of a stream instead of all of it."""

    def __init__(self, max_chars: int) -> None:
        # Four bytes is the widest UTF-8 encoding of one character.
        self.limit_bytes = max(64, max_chars * 4 + 16)
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = False

    def consume(self, chunk: bytes) -> None:
        head_limit = max(1, self.limit_bytes * 3 // 5)
        if len(self.head) < head_limit:
            take = min(head_limit - len(self.head), len(chunk))
            self.head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            self.tail.extend(chunk)
            tail_limit = max(0, self.limit_bytes - len(self.head))
            if len(self.tail) > tail_limit:
                del self.tail[: len(self.tail) - tail_limit]
                self.dropped = True

    def text(self) -> str:
        return _decode_output(bytes(self.head) + bytes(self.tail))


def _pump(stream: IO[bytes], capture: _Capture) -> None:
    try:
        read = getattr(stream, "read1", stream.read)
        while True:
            chunk = read(8192)
            if not chunk:
                break
            capture.consume(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _join_readers(threads: list[threading.Thread], timeout: float) -> None:
    for thread in threads:
        thread.join(timeout=timeout)


def _finish_capture(capture: _Capture, limit: int) -> tuple[str, bool]:
    text, truncated = truncate_text(capture.text(), limit)
    if not capture.dropped:
        return text, truncated
    if not truncated:
        marker = "\n... <truncated> ...\n"
        if limit <= len(marker):
            text = text[:limit]
        else:
            text = text[: limit - len(marker)] + marker
    return text, True


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill a timed-out command and every process it spawned."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _child_env(
    user_env: Mapping[str, str] | None,
    temp_path: str | None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "PYTHONIOENCODING": "utf-8",
            "LANG": env.get("LANG", "C.UTF-8"),
        }
    )
    if temp_path:
        env["TMPDIR"] = temp_path
        env["TMP"] = temp_path
        env["TEMP"] = temp_path
        env["MINIMALCODE_SANDBOX_TMPDIR"] = temp_path
    if user_env:
        env.update({str(key): str(value) for key, value in user_env.items()})
    return env


def _set_limit(resource: Any, kind: int, value: int) -> None:
    try:
        _, hard = resource.getrlimit(kind)
        limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(kind, (limit, hard))
    except (OSError, ValueError):
        pass


def _resource_limiter(
    policy: SandboxPolicy, timeout: float
) -> Callable[[], None] | None:
    """Return a POSIX pre-exec hook that caps CPU and (on Linux) memory/procs."""
    if os.name == "nt":
        return None
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX always ships resource
        return None

    def apply() -> None:
        _set_limit(resource, resource.RLIMIT_CORE, 0)
        _set_limit(resource, resource.RLIMIT_CPU, max(1, math.ceil(timeout) + 1))
        if sys.platform.startswith("linux"):
            if policy.max_memory_mb:
                _set_limit(
                    resource, resource.RLIMIT_AS, policy.max_memory_mb * 1024 * 1024
                )
            if policy.max_processes:
                _set_limit(resource, resource.RLIMIT_NPROC, policy.max_processes)

    return apply


def _execute(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    env: Mapping[str, str],
    stdin_text: str | None,
    shell_label: str,
    preexec: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run one prepared command with bounded captures and tree-wide timeout."""
    tree_options: dict[str, object] = (
        {} if os.name == "nt" else {"start_new_session": True}
    )
    try:
        with tempfile.TemporaryFile() as in_file:
            if stdin_text is None:
                stdin_source: object = subprocess.DEVNULL
            else:
                in_file.write(str(stdin_text).encode("utf-8"))
                in_file.seek(0)
                stdin_source = in_file
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=dict(env),
                    stdin=stdin_source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    # Limits must be inherited by exec; there is no portable
                    # alternative in the standard library.
                    preexec_fn=preexec,  # noqa: PLW1509
                    **tree_options,
                )
            except (OSError, ValueError) as exc:
                return {
                    "status": "error",
                    "shell": shell_label,
                    "message": str(exc),
                }

            out_capture = _Capture(max_output_chars)
            err_capture = _Capture(max_output_chars)
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(
                    target=_pump,
                    args=(process.stdout, out_capture),
                    daemon=True,
                ),
                threading.Thread(
                    target=_pump,
                    args=(process.stderr, err_capture),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate_tree(process)
                _join_readers(readers, 2.0)
                stdout, stdout_truncated = _finish_capture(
                    out_capture, max_output_chars
                )
                stderr, stderr_truncated = _finish_capture(
                    err_capture, max_output_chars
                )
                return {
                    "status": "timeout",
                    "shell": shell_label,
                    "timeout_seconds": timeout,
                    "stdout": stdout.rstrip(),
                    "stderr": stderr.rstrip(),
                    "truncated": stdout_truncated or stderr_truncated,
                }
            except BaseException:
                # Ctrl-C must not leave the command's process tree running.
                _terminate_tree(process)
                _join_readers(readers, 2.0)
                raise
            _join_readers(readers, 5.0)
    except (OSError, ValueError) as exc:
        return {
            "status": "error",
            "shell": shell_label,
            "message": str(exc),
        }

    stdout, stdout_truncated = _finish_capture(out_capture, max_output_chars)
    stderr, stderr_truncated = _finish_capture(err_capture, max_output_chars)
    return {
        "status": "success" if returncode == 0 else "error",
        "shell": shell_label,
        "exit_code": returncode,
        "stdout": stdout.rstrip(),
        "stderr": stderr.rstrip(),
        "truncated": stdout_truncated or stderr_truncated,
    }


class SandboxRunner:
    """Single entry point for sandboxed paths and child processes."""

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        backend: SandboxBackend | None = None,
        audit: SandboxAudit | None = None,
    ) -> None:
        self.policy = policy
        self.backend = backend if backend is not None else backend_for(policy.backend)
        self.audit = audit or SandboxAudit()
        self.last_temp_dir: Path | None = None
        self.last_temp_cleaned: bool | None = None

    @classmethod
    def disabled(cls, workspace: str | Path) -> SandboxRunner:
        return cls(
            SandboxPolicy.disabled(workspace),
            backend=LocalSandboxBackend(),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        workspace: str | Path,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> SandboxRunner:
        return cls(
            SandboxPolicy.from_settings(settings, workspace, overrides=overrides)
        )

    def resolve_path(
        self,
        raw: str | Path,
        *,
        cwd: Path,
        operation: str = "read",
        allow_outside: bool = False,
    ) -> Path:
        """Resolve a tool path and enforce roots and sensitive-file policy."""
        if not isinstance(raw, (str, Path)) or (
            isinstance(raw, str) and not raw.strip()
        ):
            raise ToolError("path must be a non-empty string")
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(cwd) / path
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolError(f"invalid path: {raw}") from exc
        if not self.policy.enabled:
            return resolved
        if not allow_outside and not self.policy.contains(resolved):
            reason = "path escapes the sandbox allowed roots"
            self.audit.deny(operation, str(resolved), reason)
            raise ToolError(
                f"path is outside workspace: {resolved} (sandbox denied: {reason})"
            )
        if not self.policy.allow_sensitive_files and is_sensitive_path(resolved):
            reason = "sensitive file or directory"
            self.audit.deny(operation, str(resolved), reason)
            raise ToolError(
                f"sensitive path is blocked by sandbox policy: {resolved} "
                "(set sandbox.allow_sensitive_files=true to allow)"
            )
        return resolved

    def is_allowed(
        self,
        path: Path,
        *,
        operation: str = "read",
        allow_outside: bool = False,
    ) -> bool:
        """Non-raising variant used to filter bulk search results."""
        if not self.policy.enabled:
            return True
        try:
            resolved = Path(path).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False
        if not allow_outside and not self.policy.contains(resolved):
            return False
        return self.policy.allow_sensitive_files or not is_sensitive_path(resolved)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.policy.enabled,
            "backend": self.backend.name,
            "backend_requested": self.policy.backend,
            "backend_available": self.backend.available(),
            "network_policy": self.policy.network_policy,
            "allowed_hosts": list(self.policy.allowed_hosts),
            "allowed_roots": [str(root) for root in self.policy.allowed_roots],
            "allow_sensitive_files": self.policy.allow_sensitive_files,
            "timeout_seconds": self.policy.timeout_seconds,
            "max_output_chars": self.policy.max_output_chars,
            "max_memory_mb": self.policy.max_memory_mb,
            "max_processes": self.policy.max_processes,
            "total_denials": self.audit.total_denials,
            "recent_denials": [
                denial.as_dict() for denial in self.audit.denials()[-5:]
            ],
            "last_temp_cleaned": self.last_temp_cleaned,
        }

    def _sandbox_metadata(self, cleaned: bool | None) -> dict[str, Any]:
        return {
            "backend": self.backend.name,
            "network_policy": self.policy.network_policy,
            "temp_dir_cleaned": cleaned,
        }

    def _denied_result(
        self,
        *,
        shell_label: str,
        message: str,
        cleaned: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "shell": shell_label,
            "message": message,
            "sandbox": self._sandbox_metadata(cleaned),
        }

    def run_argv(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command: str | None = None,
        shell_kind: str = "",
        shell_label: str = "",
        cwd: Path,
        timeout: float,
        max_output_chars: int,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> dict[str, Any]:
        """Execute argv through the configured backend and policy."""
        if not argv:
            return {
                "status": "error",
                "shell": shell_label,
                "message": "command is empty",
            }
        try:
            requested_timeout = float(timeout)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "shell": shell_label,
                "message": "timeout must be a number",
            }
        if math.isnan(requested_timeout):
            return {
                "status": "error",
                "shell": shell_label,
                "message": "timeout must be a number",
            }
        try:
            requested_output = int(max_output_chars)
        except (TypeError, ValueError):
            requested_output = 1
        requested_output = max(1, requested_output)

        if not self.policy.enabled:
            return _execute(
                list(argv),
                cwd=cwd,
                timeout=max(1.0, min(requested_timeout, 3600.0)),
                max_output_chars=requested_output,
                env=_child_env(env, None),
                stdin_text=stdin_text,
                shell_label=shell_label,
            )

        effective_timeout = max(
            1.0, min(requested_timeout, self.policy.timeout_seconds)
        )
        effective_output = (
            min(requested_output, self.policy.max_output_chars)
            if command is not None
            else requested_output
        )

        if command is not None:
            denial = self.policy.network_denial(command)
            if denial is not None:
                self.audit.deny("shell", command, denial)
                return self._denied_result(
                    shell_label=shell_label,
                    message=f"network access denied by sandbox policy: {denial}",
                )

        if not self.backend.available():
            reason = (
                f"sandbox backend {self.backend.name!r} is unavailable; "
                "refusing to execute (fail-closed)"
            )
            self.audit.deny("shell", command or " ".join(argv[:3]), reason)
            return self._denied_result(shell_label=shell_label, message=reason)

        temp_dir: Path | None = None
        cleaned: bool | None = None
        failure: str | None = None
        try:
            try:
                temp_dir = Path(tempfile.mkdtemp(prefix="minimalcode-sandbox-"))
            except OSError as exc:
                reason = f"cannot create sandbox temporary directory: {exc}"
                self.audit.deny("shell", command or " ".join(argv[:3]), reason)
                return self._denied_result(shell_label=shell_label, message=reason)
            self.last_temp_dir = temp_dir
            request = CommandRequest(
                argv=tuple(argv),
                command=command,
                shell_kind=shell_kind,
                shell_label=shell_label,
                cwd=Path(cwd),
                temp_dir=temp_dir,
                policy=self.policy,
            )
            try:
                prepared = self.backend.wrap(request)
            except SandboxUnavailable as exc:
                failure = str(exc)
                self.audit.deny("shell", command or " ".join(argv[:3]), failure)
                result: dict[str, Any] = {}
            if failure is None:
                result = _execute(
                    prepared,
                    cwd=Path(cwd),
                    timeout=effective_timeout,
                    max_output_chars=effective_output,
                    env=_child_env(env, self.backend.temp_path(request)),
                    stdin_text=stdin_text,
                    shell_label=shell_label,
                    preexec=_resource_limiter(self.policy, effective_timeout),
                )
        finally:
            cleaned = True
            if temp_dir is not None:
                try:
                    shutil.rmtree(temp_dir)
                except OSError as exc:
                    cleaned = False
                    self.audit.note(
                        f"temporary directory cleanup failed: {temp_dir}: {exc}"
                    )
            self.last_temp_cleaned = cleaned

        if failure is not None:
            return self._denied_result(
                shell_label=shell_label, message=failure, cleaned=cleaned
            )
        if result.get("status") == "timeout":
            self.audit.note(
                f"command timed out after {effective_timeout:g}s; process tree killed"
            )
        if result.get("truncated"):
            self.audit.note(
                f"command output truncated at {effective_output} characters"
            )
        result["sandbox"] = self._sandbox_metadata(cleaned)
        return result
