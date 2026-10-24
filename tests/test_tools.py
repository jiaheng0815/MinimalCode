from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.tools.max_file_chars = 200
        result = self.tools.read("large.txt", start_line=150)
        self.assertIn("line-150", result["content"])
        self.assertNotIn("line-149", result["content"])
        self.assertTrue(result["truncated"])

    def test_read_returns_prefix_of_oversized_first_line(self) -> None:
        self.tools.write("minified.js", "x" * 5000 + "\nshort\n")
        self.tools.max_file_chars = 200
        result = self.tools.read("minified.js")
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 1)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["content"].strip())
        self.assertLessEqual(len(result["content"]), 200)

    def test_ambiguous_edit_fails(self) -> None:
        self.tools.write("duplicate.txt", "same\nsame\n")
        with self.assertRaises(ToolError):
            self.tools.edit("duplicate.txt", "same", "other")
        result = self.tools.edit("duplicate.txt", "same", "other", replace_all=True)
        self.assertEqual(result["replacements"], 2)

    def test_edit_preserves_unrelated_line_endings(self) -> None:
        target = self.workspace / "mixed.txt"
        target.write_bytes(b"a\r\nb\nc\r\n")
        self.tools.edit("mixed.txt", "b", "B")
        self.assertEqual(target.read_bytes(), b"a\r\nB\nc\r\n")

    def test_edit_matches_lf_text_in_crlf_file(self) -> None:
        target = self.workspace / "windows.txt"
        target.write_bytes(b"one\r\ntwo\r\nthree\r\n")
        self.tools.edit("windows.txt", "one\ntwo", "one\nTWO")
        self.assertEqual(target.read_bytes(), b"one\r\nTWO\r\nthree\r\n")

    def test_edit_preserves_cr_only_line_endings(self) -> None:
        target = self.workspace / "classic.txt"
        target.write_bytes(b"one\rtwo\rthree\r")
        self.tools.edit("classic.txt", "one\ntwo", "one\nTWO")
        self.assertEqual(target.read_bytes(), b"one\rTWO\rthree\r")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_edit_and_write_preserve_file_mode(self) -> None:
        script = self.workspace / "run.sh"
        script.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        os.chmod(script, 0o755)
        self.tools.edit("run.sh", "old", "new")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
        self.tools.write("run.sh", "#!/bin/sh\necho newer\n")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)

    def test_dispatch_reports_invalid_arguments(self) -> None:
        with self.assertRaises(ToolError) as context:
            self.tools.dispatch("read", {"unknown": "x"})
        self.assertIn("invalid arguments", str(context.exception))

    def test_dispatch_rejects_optional_argument_with_wrong_type(self) -> None:
        cases = (
            ("grep", {"pattern": "x", "glob": 5}),
            ("find", {"pattern": "*", "max_results": "10"}),
            ("read", {"path": "a.txt", "end_line": [1]}),
            ("shell", {"command": "echo hi", "timeout": "soon"}),
            ("web_search", {"query": "q", "count": [1]}),
        )
        for name, arguments in cases:
            with (
                self.subTest(tool=name, arguments=arguments),
                self.assertRaisesRegex(ToolError, "invalid arguments"),
            ):
                self.tools.dispatch(name, arguments)

    def test_dispatch_does_not_mask_handler_type_errors(self) -> None:
        def broken(path: str = "") -> dict:
            raise TypeError("internal bug")

        self.tools.read = broken  # type: ignore[method-assign]
        with self.assertRaisesRegex(TypeError, "internal bug"):
            self.tools.dispatch("read", {"path": "x"})

    def test_unresolvable_path_is_reported_as_tool_error(self) -> None:
        with (
            patch(
                "minimalcode.tools.Path.expanduser",
                side_effect=RuntimeError("no home"),
            ),
            self.assertRaisesRegex(ToolError, "invalid path"),
        ):
            self.tools.read("~missing/file")

    def test_write_rejects_directory_target(self) -> None:
        (self.workspace / "folder").mkdir()
        with self.assertRaisesRegex(ToolError, "directory"):
            self.tools.write("folder", "content")

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

    def test_ripgrep_paths_apply_the_same_ignore_globs(self) -> None:
        self.tools.write("src/current.py", "needle = 1\n")
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b""
        )
        with (
            patch("minimalcode.tools.shutil.which", return_value="/usr/bin/rg"),
            patch("minimalcode.tools.subprocess.run", return_value=completed) as run,
        ):
            self.tools.grep("needle")
            grep_command = run.call_args.args[0]
            self.tools.find("*.py")
            find_command = run.call_args.args[0]

        for command in (grep_command, find_command):
            self.assertIn("!**/build/**", command)
            self.assertIn("!**/node_modules/**", command)
            self.assertIn("!**/__pycache__/**", command)
            # Ignore globs must come after the positive glob so ripgrep's
            # last-match-wins rule stays exclusion-only.
            self.assertGreater(
                command.index("!**/build/**"),
                command.index("--glob") + 1,
            )
        self.assertLess(
            find_command.index("*.py"),
            find_command.index("!**/build/**"),
        )

    def test_grep_rejects_missing_path(self) -> None:
        with (
            patch("minimalcode.tools.shutil.which", return_value=None),
            self.assertRaisesRegex(ToolError, "path not found"),
        ):
            self.tools.grep("needle", path="missing")

    def test_search_limits_must_be_positive(self) -> None:
        self.tools.write("a.py", "needle = 1\n")
        for name, arguments in (
            ("grep", {"pattern": "needle", "max_results": 0}),
            ("grep", {"pattern": "needle", "max_results": -1}),
            ("find", {"pattern": "*.py", "max_results": 0}),
            ("find", {"pattern": "*.py", "max_results": -1}),
        ):
            with (
                self.subTest(tool=name, arguments=arguments),
                self.assertRaisesRegex(ToolError, "max_results"),
            ):
                self.tools.dispatch(name, arguments)

    def test_python_search_fallback_skips_build_artifacts(self) -> None:
        self.tools.write("src/current.py", "needle = 1\n")
        self.tools.write("build/lib/stale.py", "needle = 1\n")
        with patch("minimalcode.tools.shutil.which", return_value=None):
            grep_result = self.tools.grep("needle", glob="*.py")
            find_result = self.tools.find("*.py")

        self.assertTrue(
            any(
                "src" in line and "current.py" in line
                for line in grep_result["matches"]
            )
        )
        self.assertFalse(any("build" in path for path in grep_result["matches"]))
        self.assertTrue(
            any(path.endswith("current.py") for path in find_result["files"])
        )
        self.assertFalse(any("build" in path for path in find_result["files"]))

    def test_shell_tool(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        command = 'Write-Output "tool-shell"' if powershell else 'printf "tool-shell"'
        result = self.tools.shell(command, timeout=30)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("tool-shell", str(result["stdout"]))

    def test_shell_rejects_nan_timeout(self) -> None:
        with self.assertRaisesRegex(ToolError, "timeout"):
            self.tools.shell("echo hi", timeout=float("nan"))

    def test_shell_timeout_is_capped_by_configuration(self) -> None:
        self.tools.shell_timeout = 120
        with patch(
            "minimalcode.tools.run_shell",
            return_value={"status": "success"},
        ) as run:
            self.tools.shell("echo test", timeout=3600)
        self.assertEqual(run.call_args.kwargs["timeout"], 120)

        with patch(
            "minimalcode.tools.run_shell",
            return_value={"status": "success"},
        ) as run:
            self.tools.shell("echo test", timeout=5)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
