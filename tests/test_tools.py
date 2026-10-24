from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minimalcode.config import load_settings
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolError, ToolRuntime


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / ".MinimalCode"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.settings = load_settings(self.home)
        self.tools = ToolRuntime(self.settings, self.workspace, detect_shell())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_write_read_and_edit(self) -> None:
        written = self.tools.write("src/example.py", "first\nsecond\n")
        self.assertTrue(written["created"])
        read = self.tools.read("src/example.py", 2, 2)
        self.assertIn("second", read["content"])
        self.assertNotIn("first", read["content"])

        edited = self.tools.edit("src/example.py", "second", "changed")
        self.assertEqual(edited["replacements"], 1)
        self.assertEqual(
            (self.workspace / "src" / "example.py").read_text(encoding="utf-8"),
            "first\nchanged\n",
        )

    def test_read_large_file_from_requested_line(self) -> None:
        lines = "".join(f"line-{number}\n" for number in range(1, 200))
        self.tools.write("large.txt", lines)
        self.tools.max_file_bytes = 50
        result = self.tools.read("large.txt", start_line=150)
        self.assertIn("line-150", result["content"])
        self.assertNotIn("line-149", result["content"])

    def test_ambiguous_edit_fails(self) -> None:
        self.tools.write("duplicate.txt", "same\nsame\n")
        with self.assertRaises(ToolError):
            self.tools.edit("duplicate.txt", "same", "other")
        result = self.tools.edit("duplicate.txt", "same", "other", replace_all=True)
        self.assertEqual(result["replacements"], 2)

    def test_outside_workspace_is_blocked(self) -> None:
        outside = self.root / "outside.txt"
        with self.assertRaises(ToolError):
            self.tools.write(str(outside), "blocked")
        self.assertFalse(outside.exists())

    def test_grep_and_find(self) -> None:
        self.tools.write("a.py", "needle = 1\n")
        self.tools.write("b.txt", "nothing\n")
        grep_result = self.tools.grep("needle", glob="*.py")
        self.assertTrue(any("a.py" in line for line in grep_result["matches"]))
        find_result = self.tools.find("*.py")
        self.assertTrue(any(path.endswith("a.py") for path in find_result["files"]))

    def test_shell_tool(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        command = 'Write-Output "tool-shell"' if powershell else 'printf "tool-shell"'
        result = self.tools.shell(command, timeout=10)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("tool-shell", str(result["stdout"]))


if __name__ == "__main__":
    unittest.main()
