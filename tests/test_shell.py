from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
                timeout=10,
                max_output_chars=10_000,
            )
        self.assertEqual(result["status"], "success", result)
        self.assertIn("hello", str(result["stdout"]))

    def test_truncation_keeps_head_and_tail(self) -> None:
        text = "A" * 100 + "B" * 100
        result, truncated = truncate_text(text, 80)
        self.assertTrue(truncated)
        self.assertLessEqual(len(result), 80)
        self.assertIn("truncated", result)
        self.assertTrue(result.startswith("A"))
        self.assertTrue(result.endswith("B"))

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
