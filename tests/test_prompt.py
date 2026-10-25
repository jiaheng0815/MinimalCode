from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minimalcode.config import load_settings
from minimalcode.prompt import build_system_prompt
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


if __name__ == "__main__":
    unittest.main()
