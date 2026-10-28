from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from minimalcode.errors import ShellError
from minimalcode.shell import (
    _PROBE_CACHE,
    ShellSpec,
    detect_shell,
    probe_shell,
    run_shell,
    select_shell,
    truncate_text,
)


class ShellTests(unittest.TestCase):
    def test_shell_detection_and_execution(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        command = 'Write-Output "hello"' if powershell else 'printf "hello"'
        with tempfile.TemporaryDirectory() as temp:
            result = run_shell(
                command,
                shell=shell,
                cwd=Path(temp),
                timeout=30,
                max_output_chars=10_000,
            )
        self.assertEqual(result["status"], "success", result)
        self.assertIn("hello", str(result["stdout"]))

    def test_invalid_timeout_returns_an_error(self) -> None:
        shell = detect_shell()
        with tempfile.TemporaryDirectory() as temp:
            result = run_shell(
                "echo hello",
                shell=shell,
                cwd=Path(temp),
                timeout=float("nan"),
                max_output_chars=1000,
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("timeout", str(result["message"]))

    def test_truncation_keeps_head_and_tail(self) -> None:
        text = "A" * 100 + "B" * 100
        result, truncated = truncate_text(text, 80)
        self.assertTrue(truncated)
        self.assertLessEqual(len(result), 80)
        self.assertIn("truncated", result)
        self.assertTrue(result.startswith("A"))
        self.assertTrue(result.endswith("B"))

    def test_timeout_returns_partial_output_and_does_not_hang(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        if powershell:
            command = 'Write-Output "started"; Start-Sleep -Seconds 20'
        else:
            command = "echo started; sleep 20"
        result: dict[str, object] = {}
        # A shared runner can spend more than a second starting pwsh, before
        # the command has written anything at all. That is startup latency, not
        # lost output, so widen the window instead of flaking.
        for timeout in (1, 4):
            with tempfile.TemporaryDirectory() as temp:
                started = time.perf_counter()
                result = run_shell(
                    command,
                    shell=shell,
                    cwd=Path(temp),
                    timeout=timeout,
                    max_output_chars=10_000,
                )
                elapsed = time.perf_counter() - started
            self.assertEqual(result["status"], "timeout", result)
            self.assertLess(elapsed, 15.0)
            if "started" in str(result["stdout"]):
                break
        else:
            self.fail(f"partial output was lost: {result!r}")

    def test_stdin_text_reaches_the_command(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        call = "& " if powershell else ""
        command = (
            f'{call}"{sys.executable}" -c '
            "'import sys; sys.stdout.write(sys.stdin.read().upper())'"
        )
        with tempfile.TemporaryDirectory() as temp:
            result = run_shell(
                command,
                shell=shell,
                cwd=Path(temp),
                timeout=30,
                max_output_chars=10_000,
                stdin_text="prompt text",
            )
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(str(result["stdout"]).strip(), "PROMPT TEXT")

    def test_timeout_kills_the_process_tree(self) -> None:
        # A timed-out shell used to orphan its grandchildren: they survived the
        # kill and kept burning CPU for hours, so the whole tree must go.
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        call = "& " if powershell else ""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "marker.txt"
            sleeper = root / "sleeper.py"
            sleeper.write_text(
                "import pathlib, time\n"
                "time.sleep(2)\n"
                f"pathlib.Path({str(marker)!r}).write_text('survived')\n",
                encoding="utf-8",
            )
            parent = root / "parent.py"
            parent.write_text(
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, {str(sleeper)!r}])\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            command = f'{call}"{sys.executable}" "{parent}"'
            result = run_shell(
                command,
                shell=shell,
                cwd=root,
                timeout=1,
                max_output_chars=10_000,
            )
            self.assertEqual(result["status"], "timeout", result)
            deadline = time.perf_counter() + 4
            while time.perf_counter() < deadline and not marker.exists():
                time.sleep(0.1)
            self.assertFalse(marker.exists(), "the grandchild outlived the timeout")

    def test_zero_budget_truncation_drops_the_text(self) -> None:
        self.assertEqual(truncate_text("abc", 0), ("", True))
        self.assertEqual(truncate_text("", 0), ("", False))

    def test_invalid_configured_shell_path_is_reported(self) -> None:
        with (
            patch(
                "minimalcode.shell.Path.expanduser",
                side_effect=RuntimeError("no user"),
            ),
            self.assertRaisesRegex(ShellError, "invalid configured shell"),
        ):
            detect_shell("~missing/pwsh")

    def test_platform_fallback_order(self) -> None:
        windows = select_shell(
            True,
            lambda name: "C:/Windows/powershell.exe" if name == "powershell" else None,
        )
        self.assertEqual(windows.kind, "windows_powershell")

        posix = select_shell(
            False,
            lambda name: "/usr/bin/bash" if name == "bash" else None,
        )
        self.assertEqual(posix.kind, "bash")

        powershell_on_posix = select_shell(
            False,
            lambda name: "/usr/bin/pwsh" if name == "pwsh" else None,
        )
        self.assertEqual(powershell_on_posix.kind, "pwsh7")

    def test_unusable_candidate_falls_through_to_the_next_shell(self) -> None:
        # A pwsh that aborts before reading its command (missing ICU, no memory,
        # wrong architecture) must not be selected just because it exists.
        found = {
            "pwsh": "/usr/bin/pwsh",
            "powershell": "C:/Windows/powershell.exe",
            "bash": "/usr/bin/bash",
        }

        def probe(shell: ShellSpec) -> str | None:
            if shell.kind == "pwsh7":
                return "Process terminated. Couldn't find a valid ICU package"
            return None

        shell = select_shell(False, found.get, probe=probe)
        self.assertEqual(shell.kind, "bash")
        self.assertEqual(shell.executable, "/usr/bin/bash")

    def test_detect_shell_falls_back_past_an_unusable_pwsh(self) -> None:
        found = {
            "pwsh": "/usr/bin/pwsh",
            "powershell": "C:/Windows/powershell.exe",
            "bash": "/usr/bin/bash",
            "sh": "/bin/sh",
        }

        def probe(shell: ShellSpec) -> str | None:
            return "Out of memory" if shell.kind == "pwsh7" else None

        with (
            patch(
                "minimalcode.shell.shutil.which",
                side_effect=lambda name: found.get(name),
            ),
            patch("minimalcode.shell.probe_shell", side_effect=probe),
        ):
            shell = detect_shell()
        expected = "windows_powershell" if os.name == "nt" else "bash"
        self.assertEqual(shell.kind, expected)

    def test_every_unusable_candidate_is_reported(self) -> None:
        found = {"pwsh": "/usr/bin/pwsh", "bash": "/usr/bin/bash", "sh": "/bin/sh"}
        with self.assertRaises(ShellError) as context:
            select_shell(
                False,
                found.get,
                probe=lambda shell: "Couldn't find a valid ICU package",
            )
        message = str(context.exception)
        self.assertIn("no supported shell found", message)
        self.assertIn("pwsh: Couldn't find a valid ICU package", message)
        self.assertIn("bash:", message)

    def test_broken_configured_shell_is_reported(self) -> None:
        with (
            patch(
                "minimalcode.shell.probe_shell",
                return_value="Process terminated: libicuuc.so not found",
            ),
            self.assertRaisesRegex(ShellError, "configured shell failed to start"),
        ):
            detect_shell(sys.executable)

    def test_probe_result_is_cached_per_executable(self) -> None:
        shell = ShellSpec(kind="sh", executable="/bin/sh", label="sh", is_windows=False)
        _PROBE_CACHE.clear()
        self.addCleanup(_PROBE_CACHE.clear)
        completed = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with patch("minimalcode.shell.subprocess.run", return_value=completed) as run:
            self.assertIsNone(probe_shell(shell))
            self.assertIsNone(probe_shell(shell))
        self.assertEqual(run.call_count, 1)

    def test_probe_reports_the_first_stderr_line(self) -> None:
        shell = ShellSpec(kind="sh", executable="/bin/sh", label="sh", is_windows=False)
        _PROBE_CACHE.clear()
        self.addCleanup(_PROBE_CACHE.clear)
        completed = SimpleNamespace(
            returncode=134,
            stdout=b"",
            stderr=b"Process terminated. Couldn't find a valid ICU package\nstack\n",
        )
        with patch("minimalcode.shell.subprocess.run", return_value=completed):
            reason = probe_shell(shell)
        self.assertEqual(
            reason,
            "Process terminated. Couldn't find a valid ICU package",
        )

    def test_probe_timeout_is_reported_instead_of_hanging(self) -> None:
        shell = ShellSpec(kind="sh", executable="/bin/sh", label="sh", is_windows=False)
        _PROBE_CACHE.clear()
        self.addCleanup(_PROBE_CACHE.clear)
        with patch(
            "minimalcode.shell.subprocess.run",
            side_effect=subprocess.TimeoutExpired("pwsh", 10),
        ):
            reason = probe_shell(shell)
        self.assertIn("did not answer", str(reason))


if __name__ == "__main__":
    unittest.main()
