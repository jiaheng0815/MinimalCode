"""Pluggable execution backends and backend selection."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError, ToolError
from .policy import SandboxPolicy


class SandboxUnavailable(ToolError):
    """Raised when the selected backend cannot execute the requested input."""


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """One prepared invocation handed to a backend's ``wrap`` method."""

    argv: tuple[str, ...]
    command: str | None
    shell_kind: str
    shell_label: str
    cwd: Path
    temp_dir: Path | None
    policy: SandboxPolicy


class SandboxBackend:
    """Base backend that runs the command unchanged."""

    name = "base"
    capabilities: frozenset[str] = frozenset()

    def available(self) -> bool:
        """Return True when the backend's external dependency is usable."""
        return True

    def wrap(self, request: CommandRequest) -> list[str]:
        """Return the final argv passed to the operating system."""
        return list(request.argv)

    def temp_path(self, request: CommandRequest) -> str | None:
        """Path the child should use for TMPDIR, or None to disable the override."""
        return str(request.temp_dir) if request.temp_dir is not None else None

    def cleanup(self, request: CommandRequest) -> None:
        """Release resources that outlive the wrapped client process, if any."""


class LocalSandboxBackend(SandboxBackend):
    """Cross-platform baseline: policy layer plus subprocess limits."""

    name = "local"
    capabilities = frozenset({"argv", "shell", "resources"})


class BwrapBackend(SandboxBackend):
    """Linux bubblewrap backend with filesystem and network namespaces."""

    name = "bwrap"
    capabilities = frozenset({"argv", "shell", "filesystem", "network"})

    def available(self) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        cached = getattr(self, "_available", None)
        if cached is not None:
            return bool(cached)
        executable = shutil.which("bwrap")
        usable = False
        if executable is not None:
            try:
                probe = subprocess.run(
                    [
                        executable,
                        "--unshare-all",
                        "--ro-bind",
                        "/",
                        "/",
                        "true",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                usable = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                usable = False
        self._available = usable
        return usable

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
    """Linux firejail backend for environments where bwrap is unavailable."""

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
    """macOS ``sandbox-exec`` backend with an inline network profile."""

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
    """Docker backend for shell commands with a mounted workspace."""

    name = "docker"
    capabilities = frozenset({"shell", "filesystem", "network"})

    def available(self) -> bool:
        # Docker Desktop on Windows cannot mount drive-letter paths the way this
        # backend builds them (`C:\ws:C:\ws`), so claiming availability there
        # would produce a confusing failure after the policy said it was usable.
        if os.name == "nt":
            return False
        cached = getattr(self, "_available", None)
        if cached is not None:
            return bool(cached)
        executable = shutil.which("docker")
        usable = False
        if executable is not None:
            try:
                probe = subprocess.run(
                    [executable, "info", "--format", "{{.ServerVersion}}"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                usable = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                usable = False
        self._available = usable
        return usable

    @staticmethod
    def _container_name(request: CommandRequest) -> str | None:
        if request.temp_dir is None:
            return None
        return f"minimalcode-{request.temp_dir.name}"

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
        name = self._container_name(request)
        if name is not None:
            # `--rm` cleans up after a normal exit, but the container belongs to
            # the daemon; killing the docker client leaves it running. A name is
            # what lets cleanup() stop it after a timeout or Ctrl-C.
            argv.extend(["--name", name])
        user = _docker_user()
        if user is not None:
            # Containers run as root by default, which would leave root-owned
            # files in the mounted workspace.
            argv.extend(["--user", user])
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

    def cleanup(self, request: CommandRequest) -> None:
        """Stop and remove a container whose docker client was killed."""
        name = self._container_name(request)
        if name is None:
            return
        executable = shutil.which("docker")
        if executable is None:
            return
        for arguments in (["kill", name], ["rm", "-f", name]):
            try:
                subprocess.run(
                    [executable, *arguments],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue


def _docker_user() -> str | None:
    """Return the ``uid:gid`` the container should write files as, if known."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:  # pragma: no cover - Windows
        return None
    try:
        return f"{getuid()}:{getgid()}"
    except OSError:  # pragma: no cover - defensive
        return None


def _auto_backend() -> SandboxBackend:
    """Pick the first genuinely available isolating backend, else local."""
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
    """Instantiate the backend named by configuration."""
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
    """Return a platform capability report for the CLI."""
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
