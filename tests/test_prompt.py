from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minimalcode.config import load_settings
from minimalcode.prompt import (
    SUMMARY_END,
    SUMMARY_START,
    build_system_prompt,
    merge_agents_summary,
)
from minimalcode.shell import detect_shell


class PromptTests(unittest.TestCase):
    def test_symlinked_instruction_outside_the_directory_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            project = root / "project"
            project.mkdir()
            home.mkdir()
            secret = root / "secret.txt"
            secret.write_text("SECRET-CONTENT", encoding="utf-8")
            link = project / "AGENTS.md"
            try:
                link.symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            settings = load_settings(home)
            prompt = build_system_prompt(settings, project, detect_shell())
            self.assertNotIn("SECRET-CONTENT", prompt)

    def test_repository_instructions_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            project = root / "project"
            child = project / "src"
            child.mkdir(parents=True)
            home.mkdir()
            (home / "AGENTS.md").write_text("global rule", encoding="utf-8")
            (project / "AGENTS.md").write_text("project rule", encoding="utf-8")
            settings = load_settings(home)
            prompt = build_system_prompt(settings, child, detect_shell())
            self.assertIn("MinimalCode", prompt)
            self.assertIn("global rule", prompt)
            self.assertIn("project rule", prompt)
            self.assertIn(str(child), prompt)

    def test_missing_agents_file_is_created_with_the_summary_block(self) -> None:
        merged = merge_agents_summary(None, "# Task\n\n- wrote result.txt\n")
        self.assertTrue(merged.startswith(SUMMARY_START))
        self.assertTrue(merged.endswith(SUMMARY_END + "\n"))
        self.assertIn("- wrote result.txt", merged)

    def test_existing_agents_file_gets_the_block_appended(self) -> None:
        existing = "# Notes\n\nkeep me\n"
        merged = merge_agents_summary(existing, "new summary")
        self.assertTrue(merged.startswith(existing))
        self.assertIn(f"{SUMMARY_START}\nnew summary\n{SUMMARY_END}", merged)

    def test_existing_block_is_replaced_in_place(self) -> None:
        existing = f"before\n{SUMMARY_START}\nold summary\n{SUMMARY_END}\nafter\n"
        merged = merge_agents_summary(existing, "new summary")
        self.assertEqual(
            merged,
            f"before\n{SUMMARY_START}\nnew summary\n{SUMMARY_END}\nafter\n",
        )

    def test_crlf_file_keeps_its_line_endings(self) -> None:
        existing = "# Notes\r\n\r\nkeep me\r\n"
        merged = merge_agents_summary(existing, "line one\nline two")
        self.assertIn(
            f"{SUMMARY_START}\r\nline one\r\nline two\r\n{SUMMARY_END}", merged
        )
        self.assertNotIn("\n", merged.replace("\r\n", ""))

    def test_echoed_markers_do_not_nest_a_second_block(self) -> None:
        summary = f"intro\n{SUMMARY_START}\nbody\n{SUMMARY_END}\nend"
        merged = merge_agents_summary(None, summary)
        self.assertEqual(merged.count(SUMMARY_START), 1)
        self.assertEqual(merged.count(SUMMARY_END), 1)
        self.assertIn("body", merged)

    def test_unterminated_marker_does_not_swallow_later_prose(self) -> None:
        # A stray start marker used to pair with the end marker of the newly
        # appended block, deleting everything in between on the second run.
        existing = (
            "# Project\n\nKeep me\n\n"
            f"{SUMMARY_START}\nold summary without end\n\n## Also keep me\n"
        )
        first = merge_agents_summary(existing, "first summary")
        second = merge_agents_summary(first, "second summary")
        self.assertIn("Keep me", second)
        self.assertIn("Also keep me", second)
        # The unterminated block is ambiguous, so its body is preserved as
        # ordinary prose rather than being paired with a later marker.
        self.assertIn("old summary without end", second)
        self.assertEqual(second.count(SUMMARY_START), 1)
        self.assertEqual(second.count(SUMMARY_END), 1)
        self.assertIn(f"{SUMMARY_START}\nsecond summary\n{SUMMARY_END}", second)


if __name__ == "__main__":
    unittest.main()
