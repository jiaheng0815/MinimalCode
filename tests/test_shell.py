from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.errors import ShellError
from minimalcode.shell import detect_shell, run_shell, select_shell, truncate_text


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
        with tempfile.TemporaryDirectory() as temp:
            started = time.perf_counter()
            result = run_shell(
                command,
                shell=shell,
                cwd=Path(temp),
                timeout=1,
                max_output_chars=10_000,
            )
            elapsed = time.perf_counter() - started
        self.assertEqual(result["status"], "timeout", result)
        self.assertIn("started", str(result["stdout"]))
        self.assertLess(elapsed, 15.0)

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


if __name__ == "__main__":
    unittest.main()
