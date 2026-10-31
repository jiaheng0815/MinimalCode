from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.config import load_settings
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolError, ToolRuntime
from minimalcode.tools.search import run_python_grep


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

    def test_read_keeps_line_numbers_after_an_oversized_line(self) -> None:
        # A physical line longer than the per-line cap used to be counted once
        # per fragment, so every later line number was wrong.
        self.tools.write("minified.js", "x" * 5000 + "\nsecond\n")
        self.tools.max_file_chars = 200
        result = self.tools.read("minified.js", start_line=2)
        self.assertEqual(result["start_line"], 2)
        self.assertIn("second", result["content"])

    def test_python_grep_numbers_lines_after_an_oversized_line(self) -> None:
        # The child-process fallback read a >1 MB line in capped fragments and
        # counted each fragment, reporting the match one line too late.
        target = self.workspace / "minified.js"
        target.write_text("y" * 1_200_000 + "\nneedle\n", encoding="utf-8")
        result = run_python_grep(
            pattern="needle",
            files=[target],
            limit=5,
            max_bytes=5_000_000,
            cwd=self.workspace,
        )
        self.assertEqual(len(result["matches"]), 1)
        self.assertTrue(result["matches"][0].endswith(":2:needle"))

    def test_read_keeps_line_numbers_when_limit_splits_crlf(self) -> None:
        # The per-line cap used to land between CR and LF, making the LF look
        # like a new empty line and shifting every later line number.
        target = self.workspace / "windows.txt"
        target.write_bytes((("a" * 99) + "\r\nTARGET\r\n").encode())
        self.tools.max_file_chars = 100
        result = self.tools.read("windows.txt", start_line=2)
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 2)
        self.assertIn("TARGET", result["content"])

    def test_single_character_edit_does_not_enumerate_every_match(self) -> None:
        self.tools.write("many.txt", "a" * 100_000)
        with self.assertRaisesRegex(ToolError, "more than once"):
            self.tools.edit("many.txt", "a", "b")
        result = self.tools.edit("many.txt", "a", "b", replace_all=True)
        self.assertEqual(result["replacements"], 100_000)
        self.assertEqual((self.workspace / "many.txt").read_text(), "b" * 100_000)

    def test_read_returns_prefix_of_oversized_first_line(self) -> None:
        self.tools.write("minified.js", "x" * 5000 + "\nshort\n")
        self.tools.max_file_chars = 200
        result = self.tools.read("minified.js")
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 1)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["content"].strip())
        self.assertLessEqual(len(result["content"]), 200)

    def test_edit_rejects_content_over_max_file_bytes(self) -> None:
        self.tools.write("small.txt", "x")
        self.tools.max_file_bytes = 20
        with self.assertRaisesRegex(ToolError, "max_file_bytes"):
            self.tools.edit("small.txt", "x", "y" * 100)
        self.assertEqual(
            (self.workspace / "small.txt").read_text(encoding="utf-8"), "x"
        )

    def test_read_start_beyond_eof_is_reported(self) -> None:
        self.tools.write("short.txt", "one\ntwo\n")
        with self.assertRaisesRegex(ToolError, "beyond the end"):
            self.tools.read("short.txt", start_line=10)
        self.tools.write("empty.txt", "")
        empty = self.tools.read("empty.txt")
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["end_line"], 0)

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
                "minimalcode.sandbox.runner.Path.expanduser",
                side_effect=RuntimeError("no home"),
            ),
            self.assertRaisesRegex(ToolError, "invalid path"),
        ):
            self.tools.read("~missing/file")

    def test_write_rejects_directory_target(self) -> None:
        (self.workspace / "folder").mkdir()
        with self.assertRaisesRegex(ToolError, "directory"):
            self.tools.write("folder", "content")

    def test_write_rejects_content_over_max_file_bytes(self) -> None:
        self.tools.max_file_bytes = 10
        with self.assertRaisesRegex(ToolError, "max_file_bytes"):
            self.tools.write("big.txt", "x" * 11)
        self.assertFalse((self.workspace / "big.txt").exists())

    def test_python_grep_supports_recursive_glob_prefix(self) -> None:
        self.tools.write("root.py", "needle = 1\n")
        self.tools.write("src/nested.py", "needle = 2\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            grep_result = self.tools.grep("needle", glob="**/*.py")
            find_result = self.tools.find("**/*.py")
        self.assertTrue(any("root.py" in line for line in grep_result["matches"]))
        self.assertTrue(any("nested.py" in line for line in grep_result["matches"]))
        self.assertTrue(any(path.endswith("root.py") for path in find_result["files"]))

    def test_default_result_limit_is_capped_by_the_tool_schema(self) -> None:
        self.tools.max_results = 5000
        lines = [f"a.py:1:needle-{index}" for index in range(3000)]
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                return_value=(0, lines, True, ""),
            ),
        ):
            result = self.tools.grep("needle")
        self.assertEqual(len(result["matches"]), 2000)
        self.assertTrue(result["truncated"])

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
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                return_value=(1, [], False, ""),
            ) as run,
        ):
            self.tools.grep("needle")
            grep_command = run.call_args.args[0]
            self.tools.find("*.py")
            find_command = run.call_args.args[0]

        for command in (grep_command, find_command):
            self.assertIn("--hidden", command)
            self.assertIn("--no-ignore", command)
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
            patch("minimalcode.tools.search_tools.shutil.which", return_value=None),
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
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
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

    def test_python_grep_uses_smart_case(self) -> None:
        self.tools.write("case.py", "UPPER = 1\nlower = 2\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            insensitive = self.tools.grep("upper")
            sensitive = self.tools.grep("UPPER")
        self.assertTrue(any("UPPER" in line for line in insensitive["matches"]))
        self.assertTrue(any("UPPER" in line for line in sensitive["matches"]))
        self.assertFalse(any("lower" in line for line in sensitive["matches"]))

    def test_explicit_ignored_directory_is_searchable(self) -> None:
        self.tools.write("build/generated.py", "needle = 1\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            result = self.tools.grep("needle", path="build")
            files = self.tools.find("*.py", path="build")
        self.assertTrue(any("generated.py" in line for line in result["matches"]))
        self.assertTrue(any(path.endswith("generated.py") for path in files["files"]))

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
            self.tools.grep("needle", path="build")
            self.tools.grep("needle", path="build/generated.py")
        self.assertNotIn("!**/build/**", run.call_args.args[0])

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
            "minimalcode.tools.process.run_shell",
            return_value={"status": "success"},
        ) as run:
            self.tools.shell("echo test", timeout=3600)
        self.assertEqual(run.call_args.kwargs["timeout"], 120)

        with patch(
            "minimalcode.tools.process.run_shell",
            return_value={"status": "success"},
        ) as run:
            self.tools.shell("echo test", timeout=5)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
