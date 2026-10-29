from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from minimalcode.cli import _apply_sandbox_overrides, _parser, run
from minimalcode.config import ConfigError, ensure_config, load_settings
from minimalcode.errors import ToolError
from minimalcode.sandbox import (
    BwrapBackend,
    CommandRequest,
    DockerBackend,
    LocalSandboxBackend,
    SandboxPolicy,
    SandboxRunner,
    available_backends,
    is_sensitive_path,
)
from minimalcode.sandbox.execution import _posix_limits
from minimalcode.shell import detect_shell, run_shell
from minimalcode.tools import ToolRuntime


class _RecordingBackend(LocalSandboxBackend):
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    def wrap(self, request: CommandRequest) -> list[str]:
        self.requests.append(request)
        return super().wrap(request)


class SandboxTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / ".MinimalCode"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _settings(self, **sandbox: object):
        settings = load_settings(self.home)
        # The sandbox is off by default; these tests are about the sandbox, so
        # they opt in unless a case says otherwise.
        if "enabled" not in sandbox:
            settings.data["sandbox"]["enabled"] = True
        settings.data["sandbox"].update(sandbox)
        return settings

    def _tools(self, **sandbox: object) -> ToolRuntime:
        return ToolRuntime(self._settings(**sandbox), self.workspace, detect_shell())


class PathPolicyTests(SandboxTestBase):
    def test_relative_and_absolute_escapes_are_refused(self) -> None:
        target = self.outside / "blocked.txt"
        tools = self._tools()
        cases = (
            ("read", "../outside/blocked.txt"),
            ("read", str(target)),
            ("write", "../../outside/new.txt"),
        )
        for tool, path in cases:
            with (
                self.subTest(tool=tool, path=path),
                self.assertRaisesRegex(ToolError, "outside workspace"),
            ):
                if tool == "read":
                    tools.read(path)
                else:
                    tools.write(path, "nope")
        self.assertFalse(target.exists())
        reasons = [denial.reason for denial in tools.sandbox.audit.denials()]
        self.assertTrue(any("allowed roots" in reason for reason in reasons))
        self.assertGreaterEqual(tools.sandbox.audit.total_denials, len(cases))

    def test_symlink_escape_is_refused(self) -> None:
        secret = self.outside / "secret.txt"
        secret.write_text("outside-secret\n", encoding="utf-8")
        link = self.workspace / "link"
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available in this environment")
        tools = self._tools()
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            tools.read("link/secret.txt")
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            tools.write("link/new.txt", "nope")
        self.assertFalse((self.outside / "new.txt").exists())

    def test_extra_allowed_root_is_the_only_addition(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()
        tools = self._tools(allowed_roots=[str(shared)])
        tools.write(str(shared / "ok.txt"), "allowed\n")
        self.assertEqual((shared / "ok.txt").read_text(encoding="utf-8"), "allowed\n")
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            tools.write(str(self.root / "elsewhere.txt"), "nope")

    def test_relative_allowed_root_is_resolved_against_workspace(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()
        tools = self._tools(allowed_roots=["../shared"])
        tools.write(str(shared / "ok.txt"), "ok\n")
        self.assertTrue((shared / "ok.txt").exists())

    def test_disabled_sandbox_keeps_the_workspace_boundary(self) -> None:
        # `sandbox.enabled = false` disables command wrapping, not the path
        # policy: otherwise --no-sandbox silently unlocks the whole filesystem.
        tools = self._tools(enabled=False)
        self.assertFalse(tools.sandbox.policy.enabled)
        target = self.outside / "legacy.txt"
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            tools.write(str(target), "legacy\n")
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(ToolError, "sensitive path"):
            tools.read(".env")
        tools.write("inside.txt", "ok\n")
        self.assertEqual(
            (self.workspace / "inside.txt").read_text(encoding="utf-8"), "ok\n"
        )

    def test_disabled_sandbox_still_honors_allow_outside_workspace(self) -> None:
        settings = self._settings(enabled=False)
        settings.data["tools"]["allow_outside_workspace"] = True
        tools = ToolRuntime(settings, self.workspace, detect_shell())
        target = self.outside / "explicit.txt"
        tools.write(str(target), "explicit\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "explicit\n")

    def test_sensitive_paths_are_blocked_unless_enabled(self) -> None:
        (self.workspace / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        (self.workspace / "key.pem").write_text("pem\n", encoding="utf-8")
        (self.workspace / ".ssh").mkdir()
        (self.workspace / ".ssh" / "id_rsa").write_text("key\n", encoding="utf-8")
        (self.workspace / ".env.example").write_text("TOKEN=\n", encoding="utf-8")

        tools = self._tools()
        for path in (".env", "key.pem", ".ssh/id_rsa", ".ssh"):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ToolError, "sensitive path"),
            ):
                if path == ".ssh":
                    tools.grep("x", path=path)
                else:
                    tools.read(path)
        self.assertIn("TOKEN", tools.read(".env.example")["content"])

        allowed = self._tools(allow_sensitive_files=True)
        self.assertIn("TOKEN=1", allowed.read(".env")["content"])
        self.assertTrue(is_sensitive_path(Path("/home/user/.ssh/id_rsa")))
        self.assertFalse(is_sensitive_path(Path("/home/user/project/settings.json")))


class NetworkPolicyTests(SandboxTestBase):
    def test_default_deny_blocks_network_commands(self) -> None:
        tools = self._tools()
        result = tools.shell("curl https://example.com/data")
        self.assertEqual(result["status"], "error", result)
        self.assertIn("network", str(result["message"]))
        self.assertIsNotNone(
            tools.sandbox.policy.network_denial("git clone https://example.com/repo")
        )
        self.assertIsNone(tools.sandbox.policy.network_denial("python -m unittest"))
        denial = tools.sandbox.audit.denials()[-1]
        self.assertEqual(denial.operation, "shell")
        self.assertIn("network", denial.reason)

    def test_allowlist_checks_named_hosts_and_rejects_unverifiable_commands(
        self,
    ) -> None:
        tools = self._tools(network_policy="allowlist", allowed_hosts=["example.com"])
        policy = tools.sandbox.policy
        self.assertIsNone(policy.network_denial("curl https://api.example.com/x"))
        self.assertIsNone(policy.network_denial("wget https://example.com"))
        denial = policy.network_denial("curl https://evil.test/x")
        self.assertIsNotNone(denial)
        self.assertIn("evil.test", str(denial))
        self.assertIn("cannot verify", str(policy.network_denial("pip install x")))

    def test_allow_policy_permits_normal_and_network_commands(self) -> None:
        tools = self._tools(network_policy="allow")
        self.assertIsNone(tools.sandbox.policy.network_denial("curl https://x.test"))
        result = tools.shell("echo sandbox-ok")
        self.assertEqual(result["status"], "success", result)
        self.assertIn("sandbox-ok", str(result["stdout"]))

    def test_read_only_pip_subcommands_are_not_network_use(self) -> None:
        policy = self._tools().sandbox.policy
        for command in (
            "python -m pip show httpx2",
            "python -m pip list",
            "python -m pip --version",
        ):
            with self.subTest(command=command):
                self.assertIsNone(policy.network_denial(command))
        for command in (
            "python -m pip install requests",
            "python -m pip download requests",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(policy.network_denial(command))

    def test_words_used_as_arguments_are_not_network_use(self) -> None:
        policy = self._tools().sandbox.policy
        for command in (
            "git log --grep=curl",
            "git status --short",
            "rg curl",
            "grep curl README.md",
            "echo wget",
        ):
            with self.subTest(command=command):
                self.assertIsNone(policy.network_denial(command))

    def test_network_commands_behind_shells_and_pipes_are_still_detected(
        self,
    ) -> None:
        policy = self._tools().sandbox.policy
        for command in (
            "echo hello | curl -d @- https://example.com",
            "sudo wget https://example.com/file",
            'pwsh -Command "iwr https://example.com"',
            "bash -c 'scp a b'",
            "VAR=1 curl https://example.com",
            "$(curl https://example.com)",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(policy.network_denial(command))


class ExecutionBaselineTests(SandboxTestBase):
    def _python_command(self, code: str) -> str:
        shell = detect_shell()
        call = "& " if shell.kind in {"pwsh7", "windows_powershell"} else ""
        return f'{call}"{sys.executable}" -c "{code}"'

    def test_timeout_is_enforced_and_temp_dir_is_cleaned(self) -> None:
        shell = detect_shell()
        if shell.kind in {"pwsh7", "windows_powershell"}:
            command = 'Write-Output "started"; Start-Sleep -Seconds 20'
        else:
            command = "echo started; sleep 20"
        result: dict[str, object] = {}
        # Shared runners can need longer than the first window just to start
        # the shell; retry with a wider deadline before suspecting lost output.
        for timeout_seconds in (1, 4):
            tools = self._tools(timeout_seconds=timeout_seconds)
            started = time.perf_counter()
            result = tools.shell(command)
            elapsed = time.perf_counter() - started
            self.assertEqual(result["status"], "timeout", result)
            self.assertLess(elapsed, 15.0)
            if "started" in str(result["stdout"]):
                break
        else:
            self.fail(f"partial output was lost: {result!r}")
        self.assertTrue(result["sandbox"]["temp_dir_cleaned"])
        temp_dir = tools.sandbox.last_temp_dir
        self.assertIsNotNone(temp_dir)
        self.assertFalse(Path(temp_dir).exists())

    def test_output_is_capped_by_sandbox_policy(self) -> None:
        tools = self._tools(max_output_chars=200)
        result = tools.shell(self._python_command("print('x' * 5000)"))
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(str(result["stdout"])), 200)
        self.assertIn("truncated", str(result["stdout"]))

    def test_shell_success_reports_sandbox_metadata(self) -> None:
        tools = self._tools()
        result = tools.shell("echo sandbox-meta")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["sandbox"]["backend"], "local")
        self.assertEqual(result["sandbox"]["network_policy"], "deny")
        self.assertTrue(result["sandbox"]["temp_dir_cleaned"])

    def test_unavailable_backend_refuses_to_execute(self) -> None:
        tools = self._tools(backend="bwrap")
        with patch("minimalcode.sandbox.backends.shutil.which", return_value=None):
            result = tools.shell("echo should-not-run")
        self.assertEqual(result["status"], "error", result)
        self.assertIn("unavailable", str(result["message"]))
        self.assertIn("refusing", str(result["message"]))
        self.assertIn("unavailable", tools.sandbox.audit.denials()[-1].reason)
        # File tools do not depend on the external backend and keep working.
        tools.write("still-works.txt", "ok\n")
        self.assertTrue((self.workspace / "still-works.txt").exists())

    def test_recording_backend_receives_every_shell_invocation(self) -> None:
        policy = SandboxPolicy.from_settings(self._settings(), self.workspace)
        backend = _RecordingBackend()
        runner = SandboxRunner(policy, backend=backend, audit=None)
        result = runner.run_argv(
            [sys.executable, "-c", "print('through-runner')"],
            cwd=self.workspace,
            timeout=30,
            max_output_chars=1000,
        )
        self.assertEqual(result["status"], "success", result)
        self.assertIn("through-runner", str(result["stdout"]))
        self.assertEqual(len(backend.requests), 1)
        self.assertIsNone(backend.requests[0].command)

    def test_run_shell_uses_the_injected_sandbox(self) -> None:
        policy = SandboxPolicy.from_settings(self._settings(), self.workspace)
        backend = _RecordingBackend()
        runner = SandboxRunner(policy, backend=backend)
        result = run_shell(
            "echo delegated",
            shell=detect_shell(),
            cwd=self.workspace,
            timeout=30,
            max_output_chars=1000,
            sandbox=runner,
        )
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(backend.requests)


class ResourceLimitTests(SandboxTestBase):
    def test_default_policy_does_not_cap_address_space(self) -> None:
        # RLIMIT_AS caps reserved address space, not RSS: a 2 GB default broke
        # .NET/pwsh children on Linux with "Out of memory" before they ran.
        policy = SandboxPolicy.from_settings(self._settings(), self.workspace)
        self.assertEqual(policy.max_memory_mb, 0)
        names = [name for name, _ in _posix_limits(policy, 10, linux=True)]
        self.assertNotIn("RLIMIT_AS", names)
        self.assertIn("RLIMIT_CPU", names)
        self.assertIn("RLIMIT_CORE", names)

    def test_address_space_limit_is_applied_only_when_configured(self) -> None:
        policy = SandboxPolicy.from_settings(
            self._settings(max_memory_mb=512, max_processes=32), self.workspace
        )
        limits = dict(_posix_limits(policy, 10, linux=True))
        self.assertEqual(limits["RLIMIT_AS"], 512 * 1024 * 1024)
        self.assertEqual(limits["RLIMIT_NPROC"], 32)

    def test_non_linux_hosts_only_get_cpu_and_core_limits(self) -> None:
        policy = SandboxPolicy.from_settings(
            self._settings(max_memory_mb=512, max_processes=32), self.workspace
        )
        names = [name for name, _ in _posix_limits(policy, 10, linux=False)]
        self.assertEqual(names, ["RLIMIT_CORE", "RLIMIT_CPU"])


class BackendTests(SandboxTestBase):
    def test_bwrap_wraps_with_isolation_and_root_binds(self) -> None:
        policy = SandboxPolicy.from_settings(
            self._settings(backend="bwrap"), self.workspace
        )
        request = CommandRequest(
            argv=("/bin/sh", "-c", "true"),
            command="true",
            shell_kind="sh",
            shell_label="sh",
            cwd=self.workspace,
            temp_dir=self.root / "sandbox-tmp",
            policy=policy,
        )
        wrapped = BwrapBackend().wrap(request)
        self.assertIn("--unshare-all", wrapped)
        self.assertNotIn("--share-net", wrapped)
        self.assertEqual(wrapped.count("--bind"), 2)
        self.assertIn(str(self.workspace), wrapped)
        self.assertIn(str(self.root / "sandbox-tmp"), wrapped)
        self.assertEqual(
            wrapped[-6:],
            [
                "--chdir",
                str(self.workspace),
                "--",
                "/bin/sh",
                "-c",
                "true",
            ],
        )

    def test_bwrap_shares_network_only_when_policy_allows(self) -> None:
        policy = SandboxPolicy.from_settings(
            self._settings(backend="bwrap", network_policy="allow"),
            self.workspace,
        )
        request = CommandRequest(
            argv=("true",),
            command="true",
            shell_kind="sh",
            shell_label="sh",
            cwd=self.workspace,
            temp_dir=self.root,
            policy=policy,
        )
        self.assertIn("--share-net", BwrapBackend().wrap(request))

    def test_available_backends_includes_local(self) -> None:
        backends = available_backends()
        self.assertTrue(backends["local"])
        self.assertIn("bwrap", backends)

    def test_docker_backend_is_not_advertised_on_windows(self) -> None:
        # Docker Desktop on Windows cannot consume the `C:\ws:C:\ws` mount and
        # workdir this backend builds, so availability must match the platform.
        backend = DockerBackend()
        if os.name == "nt":
            self.assertFalse(backend.available())
        else:
            self.assertEqual(backend.available(), shutil.which("docker") is not None)

    def test_docker_wrap_runs_as_the_invoking_user(self) -> None:
        policy = SandboxPolicy.from_settings(
            self._settings(backend="docker"), self.workspace
        )
        request = CommandRequest(
            argv=("true",),
            command="true",
            shell_kind="sh",
            shell_label="sh",
            cwd=self.workspace,
            temp_dir=None,
            policy=policy,
        )
        wrapped = DockerBackend().wrap(request)
        if hasattr(os, "getuid"):
            self.assertEqual(
                wrapped[wrapped.index("--user") + 1], f"{os.getuid()}:{os.getgid()}"
            )
        self.assertIn("none", wrapped)


class SearchLeakTests(SandboxTestBase):
    def test_sensitive_files_are_not_returned_by_search(self) -> None:
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "app.py").write_text("needle = 1\n", encoding="utf-8")
        (self.workspace / ".env").write_text("needle=secret\n", encoding="utf-8")
        tools = self._tools()
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            grep_result = tools.grep("needle")
            find_result = tools.find("*")
        self.assertTrue(any("app.py" in line for line in grep_result["matches"]))
        self.assertFalse(any(".env" in line for line in grep_result["matches"]))
        self.assertFalse(any(path.endswith(".env") for path in find_result["files"]))

    def test_ripgrep_receives_sensitive_globs(self) -> None:
        tools = self._tools()
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                return_value=(0, [], False, ""),
            ) as run,
        ):
            tools.grep("needle")
            grep_command = run.call_args.args[0]
            tools.find("*")
            find_command = run.call_args.args[0]
        for command in (grep_command, find_command):
            self.assertIn("!**/.env", command)
            self.assertIn("!**/.ssh/**", command)
        self.assertEqual(run.call_args.kwargs["sandbox"], tools.sandbox)


class ConfigAndCliTests(SandboxTestBase):
    def test_cli_overrides_are_applied_in_memory(self) -> None:
        settings = load_settings(self.home)
        args = _parser().parse_args(
            [
                "--no-sandbox",
                "--sandbox-backend",
                "local",
                "--sandbox-network",
                "allowlist",
                "--sandbox-allow-host",
                "example.com",
                "--sandbox-root",
                str(self.outside),
                "--sandbox-timeout",
                "5",
                "--sandbox-max-output",
                "1234",
                "--sandbox-allow-sensitive",
            ]
        )
        _apply_sandbox_overrides(settings, args)
        sandbox = settings.data["sandbox"]
        self.assertFalse(sandbox["enabled"])
        self.assertEqual(sandbox["backend"], "local")
        self.assertEqual(sandbox["network_policy"], "allowlist")
        self.assertEqual(sandbox["allowed_hosts"], ["example.com"])
        self.assertEqual(sandbox["allowed_roots"], [str(self.outside)])
        self.assertEqual(sandbox["timeout_seconds"], 5)
        self.assertEqual(sandbox["max_output_chars"], 1234)
        self.assertTrue(sandbox["allow_sensitive_files"])

    def test_allowlist_without_hosts_is_rejected(self) -> None:
        settings = load_settings(self.home)
        args = _parser().parse_args(["--sandbox-network", "allowlist"])
        with self.assertRaisesRegex(ConfigError, "allowlist requires at least one"):
            _apply_sandbox_overrides(settings, args)

    def test_sandbox_command_reports_policy_and_backends(self) -> None:
        ensure_config(self.home)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = run(
                ["sandbox", "--home", str(self.home), "--cwd", str(self.workspace)]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        # The execution layer is off unless settings or --sandbox enable it.
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["network_policy"], "deny")
        self.assertIn("local", payload["available_backends"])
        self.assertEqual(payload["allowed_roots"], [str(self.workspace.resolve())])

    def test_sandbox_defaults_to_off_and_flags_opt_in(self) -> None:
        settings = load_settings(self.home)
        self.assertFalse(settings.get("sandbox.enabled"))
        args = _parser().parse_args([])
        _apply_sandbox_overrides(settings, args)
        self.assertFalse(settings.data["sandbox"]["enabled"])

        args = _parser().parse_args(["--sandbox"])
        _apply_sandbox_overrides(settings, args)
        self.assertTrue(settings.data["sandbox"]["enabled"])

        settings = load_settings(self.home)
        args = _parser().parse_args(["--sandbox-timeout", "5"])
        _apply_sandbox_overrides(settings, args)
        self.assertTrue(settings.data["sandbox"]["enabled"])

        settings = load_settings(self.home)
        args = _parser().parse_args(["--no-sandbox", "--sandbox-timeout", "5"])
        _apply_sandbox_overrides(settings, args)
        self.assertFalse(settings.data["sandbox"]["enabled"])

    def test_status_exposes_sandbox_summary(self) -> None:
        from minimalcode.agent import Agent
        from minimalcode.session import SessionStore

        settings = self._settings()
        session = SessionStore(settings).create(
            self.workspace, provider="fake", model="fake-model", save=False
        )
        provider = _FakeProvider()
        agent = Agent(
            settings=settings,
            provider=provider,
            session=session,
            tool_runtime=ToolRuntime(settings, self.workspace, detect_shell()),
            shell=detect_shell(),
        )
        status = agent.status()
        self.assertTrue(status["sandbox"]["enabled"])
        self.assertEqual(status["sandbox"]["backend"], "local")


class _FakeProvider:
    name = "fake"
    model = "fake-model"
    thinking = "none"
    context_window = 1000
    config: ClassVar[dict] = {}

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        raise AssertionError("not used")

    def complete_text(self, messages):
        raise AssertionError("not used")

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
