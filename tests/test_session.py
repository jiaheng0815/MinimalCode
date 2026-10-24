from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from minimalcode.config import ensure_config, load_settings
from minimalcode.session import SessionStore


class SessionTests(unittest.TestCase):
    def test_create_append_load_and_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / ".MinimalCode"
            workspace = root / "workspace"
            workspace.mkdir()
            ensure_config(home)
            settings = load_settings(home, require_file=True)
            store = SessionStore(settings)

            session = store.create(
                workspace,
                provider="test",
                model="model",
                save=True,
            )
            session.append_message({"role": "user", "content": "first"})
            session.append_message({"role": "assistant", "content": "second"})
            session.replace_messages(
                [{"role": "user", "content": "summary"}],
                summary="summary",
                dropped_messages=2,
            )

            loaded = store.latest(workspace)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.session_id, session.session_id)
            self.assertEqual(loaded.messages, [{"role": "user", "content": "summary"}])
            self.assertEqual(loaded.cwd, workspace.resolve())

    def test_memory_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = load_settings(root / ".MinimalCode")
            store = SessionStore(settings)
            session = store.create(
                root,
                provider="test",
                model="model",
                save=False,
            )
            session.append_message({"role": "user", "content": "hello"})
            self.assertIsNone(session.path)
            self.assertEqual(len(session.messages), 1)


if __name__ == "__main__":
    unittest.main()
