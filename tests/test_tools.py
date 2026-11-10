from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.config import ConfigError, load_settings
from minimalcode.shell import ShellSpec, detect_shell
from minimalcode.tools import ToolError, ToolRuntime
from minimalcode.tools.search import (
    inline_python_grep,
    run_bounded_command,
    run_python_grep,
)


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
        written = self.tools.write("src/example.py", "first = 1\nsecond = 2\n")
        self.assertTrue(written["created"])
        read = self.tools.read("src/example.py", 2, 2)
        self.assertIn("second", read["content"])
        self.assertNotIn("first", read["content"])

        edited = self.tools.edit("src/example.py", "second = 2", "changed = 3")
        self.assertEqual(edited["replacements"], 1)
        self.assertEqual(
            (self.workspace / "src" / "example.py").read_text(encoding="utf-8"),
            "first = 1\nchanged = 3\n",
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

    def test_read_reports_non_utf8_instead_of_silently_replacing(self) -> None:
        # A GBK file decoded with errors="replace" looked like a clean read of
        # U+FFFD characters, and only `edit` later refused the same file.
        target = self.workspace / "gbk.txt"
        target.write_bytes("中文\n".encode("gbk"))
        result = self.tools.read("gbk.txt")
        self.assertIn("warnings", result)
        self.assertIn("\ufffd", result["content"])
        with self.assertRaisesRegex(ToolError, "not valid UTF-8"):
            self.tools.edit("gbk.txt", "\ufffd", "x")

    def test_read_strips_a_utf8_bom_without_a_warning(self) -> None:
        (self.workspace / "bom.txt").write_bytes(b"\xef\xbb\xbffirst\nsecond\n")
        result = self.tools.read("bom.txt")
        self.assertNotIn("\ufeff", result["content"])
        self.assertIn("first", result["content"])
        self.assertNotIn("warnings", result)

    def test_read_only_and_allowlist_gate_dispatch(self) -> None:
        self.settings.data["tools"]["allow"] = ["read", "grep"]
        restricted = ToolRuntime(self.settings, self.workspace, detect_shell())
        self.assertEqual(restricted.allowed_tools, frozenset({"read", "grep"}))
        with self.assertRaisesRegex(ToolError, "disabled by tools.allow"):
            restricted.dispatch("write", {"path": "x.txt", "content": "x"})
        self.tools.write("readable.txt", "content\n")
        read = restricted.dispatch("read", {"path": "readable.txt"})
        self.assertIn("content", read["content"])

        read_only = ToolRuntime(
            self.settings, self.workspace, detect_shell(), read_only=True
        )
        self.assertEqual(read_only.allowed_tools, frozenset({"read", "grep"}))
        with self.assertRaisesRegex(ToolError, "disabled by --read-only"):
            read_only.dispatch("shell", {"command": "echo hi"})
        self.assertFalse((self.workspace / "x.txt").exists())

    def test_unknown_tool_in_allowlist_is_a_config_error(self) -> None:
        self.settings.data["tools"]["allow"] = ["raed"]
        with self.assertRaisesRegex(ConfigError, "unknown tools: raed"):
            ToolRuntime(self.settings, self.workspace, detect_shell())

    def test_read_only_with_an_unusable_allowlist_is_a_config_error(self) -> None:
        # Every name is allowed, but none of them survive --read-only.
        self.settings.data["tools"]["allow"] = ["write"]
        with self.assertRaisesRegex(ConfigError, "no tools are enabled"):
            ToolRuntime(self.settings, self.workspace, detect_shell(), read_only=True)

    def test_read_start_beyond_eof_is_reported(self) -> None:
        self.tools.write("short.txt", "one\ntwo\n")
        with self.assertRaisesRegex(ToolError, "beyond the end"):
            self.tools.read("short.txt", start_line=10)
        self.tools.write("empty.txt", "")
        empty = self.tools.read("empty.txt")
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["end_line"], 0)

    def test_line_edit_replaces_the_requested_range(self) -> None:
        self.tools.write("lines.py", "one = 1\ntwo = 2\nthree = 3\n")
        result = self.tools.edit(
            "lines.py", start_line=2, end_line=2, new_text="TWO = 20"
        )
        self.assertEqual(result["mode"], "lines")
        self.assertEqual(result["lines_removed"], 1)
        self.assertEqual(
            (self.workspace / "lines.py").read_text(encoding="utf-8"),
            "one = 1\nTWO = 20\nthree = 3\n",
        )

    def test_line_edit_can_insert_before_a_line(self) -> None:
        self.tools.write("insert.py", "one = 1\nthree = 3\n")
        self.tools.edit("insert.py", start_line=2, end_line=1, new_text="two = 2")
        self.assertEqual(
            (self.workspace / "insert.py").read_text(encoding="utf-8"),
            "one = 1\ntwo = 2\nthree = 3\n",
        )

    def test_line_edit_preserves_crlf_and_patch_line_endings(self) -> None:
        target = self.workspace / "crlf.py"
        target.write_bytes(b"one = 1\r\ntwo = 2\r\n")
        self.tools.edit("crlf.py", start_line=2, end_line=2, new_text="TWO = 2")
        self.assertEqual(target.read_bytes(), b"one = 1\r\nTWO = 2\r\n")

    def test_line_edit_out_of_range_reports_the_nearest_lines(self) -> None:
        self.tools.write("short.py", "one = 1\ntwo = 2\n")
        with self.assertRaises(ToolError) as context:
            self.tools.edit("short.py", start_line=9, end_line=10, new_text="x = 1")
        message = str(context.exception)
        self.assertIn("beyond EOF", message)
        self.assertIn("closest available range", message)
        self.assertIn("two = 2", message)

    def test_read_range_requires_and_returns_an_explicit_window(self) -> None:
        self.tools.write("range.txt", "one\ntwo\nthree\nfour\n")
        result = self.tools.dispatch(
            "read_range", {"path": "range.txt", "start_line": 2, "end_line": 3}
        )
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(result["line_count"], 2)
        self.assertIn("two", result["content"])
        self.assertIn("three", result["content"])
        self.assertNotIn("four", result["content"])
        with self.assertRaisesRegex(ToolError, "greater than or equal"):
            self.tools.read_range("range.txt", 3, 2)

    def test_append_file_creates_and_extends_without_tail_guessing(self) -> None:
        created = self.tools.dispatch(
            "append_file", {"path": "notes.txt", "content": "one\n"}
        )
        self.assertTrue(created["created"])
        self.tools.append_file("notes.txt", "two\n")
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"),
            "one\ntwo\n",
        )
        self.tools.append_file("notes.txt", "three")
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"),
            "one\ntwo\nthree",
        )

    def test_append_file_preserves_crlf_and_adds_a_separator(self) -> None:
        target = self.workspace / "crlf.txt"
        target.write_bytes(b"one\r\n")
        self.tools.append_file("crlf.txt", "two\n")
        self.assertEqual(target.read_bytes(), b"one\r\ntwo\r\n")
        target.write_bytes(b"no-terminator")
        self.tools.append_file("crlf.txt", "next")
        self.assertEqual(target.read_bytes(), b"no-terminator\nnext")

    def test_append_file_rolls_back_invalid_python(self) -> None:
        self.tools.write("append.py", "value = 1\n")
        target = self.workspace / "append.py"
        with self.assertRaises(ToolError) as context:
            self.tools.append_file("append.py", "def broken(:\n")
        self.assertIn("rolled back", str(context.exception))
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

    def test_mixing_line_numbers_with_old_text_is_rejected(self) -> None:
        self.tools.write("mixed.py", "one = 1\n")
        with self.assertRaisesRegex(ToolError, "not both"):
            self.tools.edit(
                "mixed.py",
                start_line=1,
                end_line=1,
                old_text="one = 1",
                new_text="two = 2",
            )

    def test_missing_exact_text_reports_the_closest_region_and_diff(self) -> None:
        self.tools.write("closest.py", "def target():\n    return 1\n")
        with self.assertRaises(ToolError) as context:
            self.tools.edit(
                "closest.py",
                old_text="def targt():\n    return 1",
                new_text="def target():\n    return 2",
            )
        message = str(context.exception)
        self.assertIn("Most similar region", message)
        self.assertIn("lines 1-2", message)
        self.assertIn("--- old_text", message)
        self.assertIn("-def targt():", message)
        self.assertIn("+def target():", message)

    def test_not_found_diagnostics_are_bounded_for_a_huge_line(self) -> None:
        self.tools.write("huge.txt", "x" * 20_000 + "\n")
        with self.assertRaises(ToolError) as context:
            self.tools.edit("huge.txt", "needle", "replacement")
        message = str(context.exception)
        self.assertIn("Most similar region", message)
        self.assertLess(len(message), 5_000)

    def test_write_rolls_back_a_new_python_syntax_error(self) -> None:
        with self.assertRaises(ToolError) as context:
            self.tools.write("broken.py", "def broken(:\n")
        message = str(context.exception)
        self.assertIn("rolled back", message)
        self.assertIn("py_compile", message)
        self.assertRegex(message, r"broken\.py:1")
        self.assertIn("def broken(:", message)
        self.assertFalse((self.workspace / "broken.py").exists())

    def test_edit_rolls_back_a_python_syntax_error(self) -> None:
        self.tools.write("safe.py", "value = 1\n")
        target = self.workspace / "safe.py"
        with self.assertRaises(ToolError) as context:
            self.tools.edit(
                "safe.py", start_line=1, end_line=1, new_text="def broken(:"
            )
        message = str(context.exception)
        self.assertIn("rolled back", message)
        self.assertRegex(message, r"safe\.py:1")
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

    def test_ruff_rejects_new_lint_errors_and_restores_the_file(self) -> None:
        if self.tools._ruff_invocation() is None:
            self.skipTest("ruff is not installed")
        with self.assertRaises(ToolError) as context:
            self.tools.write("lint.py", "import os\n")
        self.assertIn("F401", str(context.exception))
        self.assertFalse((self.workspace / "lint.py").exists())

    def test_preexisting_lint_errors_do_not_block_an_unrelated_edit(self) -> None:
        if self.tools._ruff_invocation() is None:
            self.skipTest("ruff is not installed")
        target = self.workspace / "legacy.py"
        target.write_text("import os\nvalue = 1\n", encoding="utf-8")
        result = self.tools.edit(
            "legacy.py", start_line=2, end_line=2, new_text="value = 2"
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["status"], "success")
        self.assertIn("import os", target.read_text(encoding="utf-8"))

    def test_write_keeps_a_utf8_bom(self) -> None:
        target = self.workspace / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbfhello\n")
        self.tools.write("bom.txt", "hello\n")
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfhello\n")

    def test_a_validator_that_cannot_run_does_not_delete_the_file(self) -> None:
        # A broken ruff config makes the checker exit without a diagnostic. That
        # says nothing about the new content, but it was counted as one and the
        # just-created file was rolled back (deleted).
        def broken_ruff() -> list[str]:
            return [sys.executable, "-c", "import sys; sys.exit(2)"]

        with patch.object(self.tools, "_ruff_invocation", broken_ruff):
            result = self.tools.write("fresh.py", "value = 1\n")
        self.assertEqual(result["status"], "success")
        self.assertTrue((self.workspace / "fresh.py").exists())
        self.assertTrue(any("exit" in item for item in result["warnings"]))

    def test_path_glob_finds_files_below_a_non_root_search_path(self) -> None:
        (self.workspace / "sub" / "deep").mkdir(parents=True)
        (self.workspace / "sub" / "deep" / "d.py").write_text("needle\n")
        found = self.tools.find("deep/*.py", path="sub")
        self.assertEqual(len(found["files"]), 1, found)
        self.assertTrue(found["files"][0].endswith("d.py"))
        matches = self.tools.grep("needle", path="sub", glob="deep/*.py")
        self.assertEqual(len(matches["matches"]), 1, matches)

    def test_grep_keeps_text_after_a_form_feed_in_a_line(self) -> None:
        (self.workspace / "ff.txt").write_bytes(b"needle-before\x0cneedle-after\n")
        result = self.tools.grep("needle", path=".")
        self.assertEqual(len(result["matches"]), 1, result)
        self.assertIn("needle-after", result["matches"][0])

    def test_ambiguous_edit_fails(self) -> None:
        self.tools.write("duplicate.txt", "same\nsame\n")
        with self.assertRaises(ToolError) as context:
            self.tools.edit("duplicate.txt", "same", "other")
        self.assertIn("first at lines 1, 2", str(context.exception))
        result = self.tools.edit("duplicate.txt", "same", "other", replace_all=True)
        self.assertEqual(result["replacements"], 2)

    def test_read_reports_the_detected_line_ending(self) -> None:
        (self.workspace / "lf.txt").write_bytes(b"one\ntwo\n")
        (self.workspace / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
        (self.workspace / "none.txt").write_bytes(b"one")
        self.assertEqual(self.tools.read("lf.txt")["line_ending"], "LF")
        self.assertEqual(self.tools.read("crlf.txt")["line_ending"], "CRLF")
        self.assertEqual(self.tools.read("none.txt")["line_ending"], "none")

    def test_write_preserves_an_existing_crlf_style(self) -> None:
        target = self.workspace / "style.txt"
        target.write_bytes(b"old = 1\r\n")
        result = self.tools.write("style.txt", "new = 1\nnext = 2\n")
        self.assertEqual(result["line_ending"], "CRLF")
        self.assertEqual(target.read_bytes(), b"new = 1\r\nnext = 2\r\n")

    def test_write_preserves_an_existing_lf_style(self) -> None:
        target = self.workspace / "style.txt"
        target.write_bytes(b"old = 1\n")
        self.tools.write("style.txt", "new = 1\r\nnext = 2\r\n")
        self.assertEqual(target.read_bytes(), b"new = 1\nnext = 2\n")

    def test_write_keeps_explicit_line_endings_for_a_new_file(self) -> None:
        result = self.tools.write("new.txt", "one\r\ntwo\r\n")
        self.assertEqual(result["line_ending"], "CRLF")
        self.assertEqual((self.workspace / "new.txt").read_bytes(), b"one\r\ntwo\r\n")
        self.tools.write("new-lf.txt", "one\ntwo\n")
        self.assertEqual((self.workspace / "new-lf.txt").read_bytes(), b"one\ntwo\n")

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

    def test_line_edit_of_an_unterminated_last_line_keeps_the_file_style(self) -> None:
        # The replaced span is the last line and has no terminator of its own,
        # so the patch's newlines inherit the file's CRLF instead of LF.
        target = self.workspace / "tail.txt"
        target.write_bytes(b"a=1\r\nb=2")
        self.tools.edit("tail.txt", start_line=2, end_line=2, new_text="x\ny")
        self.assertEqual(target.read_bytes(), b"a=1\r\nx\r\ny")

    def test_edit_rejects_a_nul_byte(self) -> None:
        # `read` treats NUL as binary; accepting it here produced a file that
        # no later tool could open.
        self.tools.write("text.txt", "one\n")
        with self.assertRaisesRegex(ToolError, "binary"):
            self.tools.edit("text.txt", start_line=1, end_line=1, new_text="a\x00b")
        self.assertEqual((self.workspace / "text.txt").read_bytes(), b"one\n")

    def test_edit_keeps_a_utf8_bom_in_place(self) -> None:
        target = self.workspace / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbffirst\nsecond\n")
        self.tools.edit("bom.txt", start_line=1, end_line=1, new_text="FIRST")
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfFIRST\nsecond\n")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_edit_and_write_preserve_file_mode(self) -> None:
        script = self.workspace / "run.sh"
        script.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        os.chmod(script, 0o755)
        self.tools.edit("run.sh", "old", "new")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
        self.tools.write("run.sh", "#!/bin/sh\necho newer\n")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)

    def test_read_range_and_append_file_are_advertised_and_gated(self) -> None:
        from minimalcode.tools import available_tool_schemas
        from minimalcode.tools.schemas import READ_ONLY_TOOLS, TOOL_NAMES

        advertised = {schema["function"]["name"] for schema in available_tool_schemas()}
        self.assertIn("read_range", advertised)
        self.assertIn("append_file", advertised)
        self.assertIn("read_range", READ_ONLY_TOOLS)
        self.assertNotIn("append_file", READ_ONLY_TOOLS)
        self.assertTrue({"read_range", "append_file"} <= TOOL_NAMES)
        self.tools.write("gate.txt", "one\n")
        read_only = ToolRuntime(
            self.settings, self.workspace, detect_shell(), read_only=True
        )
        self.assertIn(
            "one",
            read_only.dispatch(
                "read_range", {"path": "gate.txt", "start_line": 1, "end_line": 1}
            )["content"],
        )
        with self.assertRaisesRegex(ToolError, "disabled by --read-only"):
            read_only.dispatch("append_file", {"path": "gate.txt", "content": "two\n"})

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

    def test_grep_single_file_with_ripgrep_keeps_its_only_match(self) -> None:
        self.tools.write("a.txt", "needle\n")
        path = str(self.workspace / "a.txt")
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                return_value=(0, [f"{path}:1:needle"], False, ""),
            ) as run,
        ):
            result = self.tools.grep("needle", path="a.txt")
        # A single-file ripgrep run prints `LINE:TEXT` unless --with-filename
        # is passed; the path filter then dropped every real match.
        self.assertIn("--with-filename", run.call_args.args[0])
        self.assertEqual(len(result["matches"]), 1)

    def test_grep_retries_when_a_filtered_path_fills_the_window(self) -> None:
        self.tools.write("a.txt", "needle\n")
        (self.workspace / ".env.local").write_text("needle\n", encoding="utf-8")
        blocked = f"{self.workspace / '.env.local'}:1:needle"
        allowed = f"{self.workspace / 'a.txt'}:1:needle"
        responses = [
            (0, [blocked, blocked, blocked], True, ""),
            (0, [allowed], False, ""),
        ]
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                side_effect=responses,
            ) as run,
        ):
            result = self.tools.grep("needle", max_results=1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(result["matches"]), 1)
        self.assertGreater(
            run.call_args_list[1].kwargs["limit"],
            run.call_args_list[0].kwargs["limit"],
        )

    def test_find_retries_when_a_filtered_path_fills_the_window(self) -> None:
        self.tools.write("a.txt", "one\n")
        (self.workspace / ".env").write_text("x\n", encoding="utf-8")
        blocked = str(self.workspace / ".env")
        allowed = str(self.workspace / "a.txt")
        responses = [
            (0, [blocked, blocked, blocked], True, ""),
            (0, [allowed], False, ""),
        ]
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                side_effect=responses,
            ) as run,
        ):
            result = self.tools.find("*", max_results=1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([Path(path).name for path in result["files"]], ["a.txt"])

    def test_python_find_matches_braces_and_leading_exclusions(self) -> None:
        self.tools.write("a.py", "value = 1\n")
        self.tools.write("b.txt", "two\n")
        self.tools.write("c.md", "three\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            braced = self.tools.find("*.{py,txt}")
            excluded = self.tools.find("!*.py")
        braced_names = {Path(path).name for path in braced["files"]}
        excluded_names = {Path(path).name for path in excluded["files"]}
        self.assertEqual(braced_names, {"a.py", "b.txt"})
        self.assertIn("b.txt", excluded_names)
        self.assertNotIn("a.py", excluded_names)

    def test_python_find_expands_every_brace_group(self) -> None:
        # Only the first `{...}` group was expanded, so `{a,b}.{py,txt}` stayed
        # literal and matched nothing.
        self.tools.write("a.py", "value = 1\n")
        self.tools.write("b.txt", "two\n")
        self.tools.write("c.md", "three\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            result = self.tools.find("{a,b}.{py,txt}")
        names = {Path(path).name for path in result["files"]}
        self.assertEqual(names, {"a.py", "b.txt"})

    def test_python_grep_keeps_an_escaped_backslash_literal_case_sensitive(
        self,
    ) -> None:
        # `\\D` is an escaped backslash plus a literal `D`, which --smart-case
        # treats as uppercase; stripping every letter escape hid that.
        self.tools.write("escaped.py", "pattern = r'\\\\d'\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            sensitive = self.tools.grep(r"\\D")
        self.assertEqual(sensitive["matches"], [])

    def test_line_edit_inserts_at_unterminated_eof(self) -> None:
        path = self.workspace / "eof.txt"
        path.write_bytes(b"one\ntwo")
        result = self.tools.edit("eof.txt", start_line=3, end_line=2, new_text="three")
        self.assertEqual(result["lines_added"], 1)
        self.assertEqual(path.read_bytes(), b"one\ntwo\nthree")

    def test_line_edit_counts_only_cr_and_lf_lines(self) -> None:
        path = self.workspace / "formfeed.txt"
        path.write_bytes(b"a = 1\n\x0c\nb = 2\n")
        self.assertIn("b = 2", self.tools.read("formfeed.txt", 3, 3)["content"])
        self.tools.edit("formfeed.txt", start_line=3, end_line=3, new_text="B = 3")
        self.assertEqual(path.read_bytes(), b"a = 1\n\x0c\nB = 3\n")

    def test_mixed_line_endings_use_the_dominant_style(self) -> None:
        path = self.workspace / "mixed.txt"
        path.write_bytes(b"a\r\nb\nc\nd\ne\n")
        self.assertEqual(self.tools.read("mixed.txt")["line_ending"], "LF")
        self.tools.write("mixed.txt", "x\ny\n")
        self.assertEqual(path.read_bytes(), b"x\ny\n")

    @unittest.skipUnless(os.name == "nt", "read-only replacement is Windows-only")
    def test_read_only_target_reports_a_tool_error_without_a_temp_file(self) -> None:
        path = self.workspace / "readonly.txt"
        path.write_text("old\n", encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
        try:
            with self.assertRaisesRegex(ToolError, "cannot write"):
                self.tools.write("readonly.txt", "new\n")
        finally:
            os.chmod(path, stat.S_IWRITE)
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(
            [
                item.name
                for item in self.workspace.iterdir()
                if item.name.startswith(".")
            ],
            [],
        )

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

    def test_python_grep_matches_line_content_without_the_terminator(self) -> None:
        # Both fallback engines fed the raw line (including "\n") to the regex,
        # so `\s+$` matched every line and a CRLF `needle$` matched none.
        self.tools.write("lines.txt", "needle\nneedle \nplain\n")
        (self.workspace / "crlf.txt").write_bytes(b"needle\r\nother\r\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            whitespace = self.tools.grep(r"\s+$", path="lines.txt")
            anchored = self.tools.grep("needle$", path="crlf.txt")
            inline = inline_python_grep(
                pattern=r"\s+$",
                files=[self.workspace / "lines.txt"],
                limit=10,
                max_bytes=1000,
            )
        self.assertEqual(
            [line.rsplit(":", 1)[-1] for line in whitespace["matches"]], ["needle "]
        )
        self.assertEqual(
            [line.rsplit(":", 1)[-1] for line in inline["matches"]], ["needle "]
        )
        self.assertEqual(len(anchored["matches"]), 1)

    def test_ripgrep_is_told_about_crlf_line_endings(self) -> None:
        # Without --crlf ripgrep keeps the `\r` in the line, so `needle$`
        # matched in the Python fallback and not with ripgrep installed.
        (self.workspace / "crlf.txt").write_bytes(b"needle\r\nother\r\n")
        path = str(self.workspace / "crlf.txt")
        with (
            patch(
                "minimalcode.tools.search_tools.shutil.which",
                return_value="/usr/bin/rg",
            ),
            patch(
                "minimalcode.tools.search.run_bounded_command",
                return_value=(0, [f"{path}:1:needle"], False, ""),
            ) as run,
        ):
            result = self.tools.grep("needle$", path="crlf.txt")
        self.assertIn("--crlf", run.call_args.args[0])
        self.assertEqual(len(result["matches"]), 1)

    def test_python_grep_drops_a_utf8_bom_before_matching(self) -> None:
        # `utf-8` kept U+FEFF in line 1, so `^first` missed there, and `first`
        # echoed the marker into the match text the model sees.
        (self.workspace / "bom.txt").write_bytes(
            b"\xef\xbb\xbffirst line\r\nsecond\r\n"
        )
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            anchored = self.tools.grep("^first", path="bom.txt")
            plain = self.tools.grep("first", path="bom.txt")
        self.assertEqual(
            [line.rsplit(":", 1)[-1] for line in anchored["matches"]], ["first line"]
        )
        self.assertEqual(
            [line.rsplit(":", 1)[-1] for line in plain["matches"]], ["first line"]
        )

    def test_grep_file_size_cap_is_the_same_in_both_engines(self) -> None:
        # ripgrep ignores --max-filesize for a file named on the command line,
        # so the same call returned matches with rg installed and nothing
        # without it. A directory walk skips the file in both engines.
        self.tools.max_file_bytes = 8
        (self.workspace / "big.txt").write_text("needle\n" * 4, encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "too large for grep"):
            self.tools.grep("needle", path="big.txt")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            with self.assertRaisesRegex(ToolError, "too large for grep"):
                self.tools.grep("needle", path="big.txt")
            self.assertEqual(self.tools.grep("needle", path=".")["matches"], [])

    def test_python_grep_exact_limit_is_not_truncated(self) -> None:
        self.tools.write("limits.txt", "needle 1\nneedle 2\nneedle 3\n")
        target = self.workspace / "limits.txt"
        exact = run_python_grep(
            pattern="needle",
            files=[target],
            limit=3,
            max_bytes=1000,
            cwd=self.workspace,
        )
        over = run_python_grep(
            pattern="needle",
            files=[target],
            limit=2,
            max_bytes=1000,
            cwd=self.workspace,
        )
        # A result that fits exactly is complete; only an extra match proves it
        # was cut, which is how the ripgrep and inline engines behave.
        self.assertEqual(len(exact["matches"]), 3)
        self.assertFalse(exact["truncated"])
        self.assertTrue(over["truncated"])

    def test_python_find_matches_zero_directories_for_a_recursive_glob(self) -> None:
        # fnmatch needs at least one directory for `**/`, while ripgrep treats
        # it as "zero or more", so a file directly under `pkg` was skipped.
        self.tools.write("pkg/config.py", "needle = 1\n")
        self.tools.write("pkg/tools/files.py", "needle = 2\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            found = self.tools.find("pkg/**/*.py")
            grepped = self.tools.grep("needle", glob="pkg/**/*.py")
        found_names = {
            path.replace("\\", "/").rsplit("/", 1)[-1] for path in found["files"]
        }
        self.assertEqual(found_names, {"config.py", "files.py"})
        self.assertEqual(len(grepped["matches"]), 2)

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

    def test_read_exact_line_limit_keeps_following_lines(self) -> None:
        # The per-line read limit was one char short of the configured maximum,
        # so a line of exactly that width looked truncated and the rest of the
        # file was dropped even though the character budget had room.
        self.tools.max_file_chars = 2_000_000
        (self.workspace / "exact.txt").write_text(
            "x" * 1_000_000 + "\ny\n", encoding="utf-8", newline=""
        )
        result = self.tools.read("exact.txt")
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 2)
        self.assertFalse(result["truncated"])
        self.assertIn("\ty\n", result["content"])

    def test_glob_with_repeated_double_stars_is_linear(self) -> None:
        from minimalcode.tools.search_tools import SearchToolsMixin

        path = self.workspace / ("a" * 200)
        pattern = "**a" * 20 + "b"
        started = time.monotonic()
        matched = SearchToolsMixin._matches_glob(path, self.workspace, pattern)
        elapsed = time.monotonic() - started
        self.assertFalse(matched)
        self.assertLess(elapsed, 1.0)

    @unittest.skipIf(os.name == "nt", "Windows file names cannot contain ':'")
    def test_grep_result_path_with_colon_numbers_is_not_truncated(self) -> None:
        self.tools.write("a:33:b.txt", "needle\n")
        result = self.tools.grep("needle", path="a:33:b.txt")
        self.assertEqual(len(result["matches"]), 1)
        # The tool resolves the workspace, and on macOS the temp root is a
        # symlink (``/var`` -> ``/private/var``), so compare the resolved path.
        expected = str((self.workspace / "a:33:b.txt").resolve())
        self.assertTrue(
            result["matches"][0].startswith(expected + ":1:"), result["matches"]
        )

    def test_bounded_search_times_out_when_a_grandchild_holds_the_pipe(self) -> None:
        # The direct child exits immediately, but its child keeps stdout open.
        # The timeout used to skip the kill (the leader had exited) and then
        # block in readline() until that grandchild finished on its own.
        code = (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(ToolError, "timed out"):
            run_bounded_command(
                [sys.executable, "-c", code],
                # The surviving grandchild inherits the working directory, and
                # Windows keeps a directory locked while it is a process cwd.
                cwd=Path(sys.prefix),
                limit=10,
                timeout=1,
            )
        self.assertLess(time.monotonic() - started, 20)

    def test_shell_tool(self) -> None:
        shell = detect_shell()
        powershell = shell.kind in {"pwsh7", "windows_powershell"}
        command = 'Write-Output "tool-shell"' if powershell else 'printf "tool-shell"'
        result = self.tools.shell(command, timeout=30)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("tool-shell", str(result["stdout"]))

    def test_powershell_shell_rejects_bash_heredocs_before_running(self) -> None:
        powershell = ShellSpec(
            kind="pwsh7",
            executable="pwsh",
            label="PowerShell 7",
            is_windows=os.name == "nt",
        )
        runtime = ToolRuntime(self.settings, self.workspace, powershell)
        with self.assertRaises(ToolError) as context:
            runtime.shell("python - <<'PY'\nprint(1)\nPY\n")
        message = str(context.exception)
        self.assertIn("does not support bash heredocs", message)
        self.assertIn("append_file", message)
        self.assertIn("here-string", message)

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

    def test_write_rejects_nul_bytes(self) -> None:
        with self.assertRaisesRegex(ToolError, "binary files"):
            self.tools.write("nul.txt", "a\x00b")
        self.assertFalse((self.workspace / "nul.txt").exists())

    def test_edit_reports_non_utf8_text(self) -> None:
        target = self.workspace / "latin.txt"
        target.write_bytes(b"caf\xe9\n")
        with self.assertRaisesRegex(ToolError, "not valid UTF-8"):
            self.tools.edit("latin.txt", "caf", "CAF")
        self.assertEqual(target.read_bytes(), b"caf\xe9\n")

    def test_find_glob_does_not_match_across_directories(self) -> None:
        self.tools.write("src/top.py", "x = 1\n")
        self.tools.write("src/deep/nested.py", "x = 2\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            result = self.tools.find("src/*.py")
        names = {Path(path).name for path in result["files"]}
        self.assertEqual(names, {"top.py"})

    def test_find_glob_is_case_sensitive(self) -> None:
        self.tools.write("UPPER.PY", "x = 1\n")
        with patch("minimalcode.tools.search_tools.shutil.which", return_value=None):
            result = self.tools.find("*.py")
        self.assertEqual(result["files"], [])


if __name__ == "__main__":
    unittest.main()


class ToolLimits151Tests(unittest.TestCase):
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

    def test_out_of_range_edit_hint_is_bounded(self) -> None:
        # A 1,000,000-line request used to paste the whole file into the tool
        # error, which then went into the next provider request.
        lines = "".join(f"line-{number}\n" for number in range(1, 400))
        (self.workspace / "big.txt").write_text(lines, encoding="utf-8")
        with self.assertRaises(ToolError) as caught:
            self.tools.edit("big.txt", start_line=1, end_line=10_000_000, new_text="x")
        message = str(caught.exception)
        self.assertIn("closest available range", message)
        self.assertIn("omitted", message)
        self.assertLess(len(message.splitlines()), 60)
        # The file was not modified by the refused edit.
        self.assertEqual(
            (self.workspace / "big.txt").read_text(encoding="utf-8"), lines
        )

    def test_write_refuses_an_oversized_existing_file(self) -> None:
        self.settings.data["tools"]["max_file_bytes"] = 32
        small = ToolRuntime(self.settings, self.workspace, detect_shell())
        (self.workspace / "large.txt").write_text("x" * 64, encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "too large for write"):
            small.write("large.txt", "small")
        self.assertEqual(len((self.workspace / "large.txt").read_text("utf-8")), 64)

    def test_single_file_grep_honours_the_glob_in_both_engines(self) -> None:
        (self.workspace / "notes.txt").write_text("needle\n", encoding="utf-8")
        self.assertEqual(
            self.tools.grep("needle", path="notes.txt", glob="*.py")["matches"], []
        )
        self.assertEqual(
            len(self.tools.grep("needle", path="notes.txt", glob="*.txt")["matches"]),
            1,
        )

    def test_subagent_timeout_report_matches_the_shell_ceiling(self) -> None:
        from minimalcode.shell import MAX_TIMEOUT_SECONDS
        from minimalcode.tools.process import _clamp_timeout

        self.assertEqual(_clamp_timeout(7200), MAX_TIMEOUT_SECONDS)
        self.assertEqual(_clamp_timeout(5), 5)
