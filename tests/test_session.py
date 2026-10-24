from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from minimalcode.config import ensure_config, load_settings
from minimalcode.session import SessionStore, _workspace_key


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

    def test_latest_uses_modification_time_not_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore(load_settings(root / ".MinimalCode"))
            first = store.create(workspace, provider="test", model="model", save=True)
            second = store.create(workspace, provider="test", model="model", save=True)
            assert first.path is not None and second.path is not None

            older = first.path.with_name("29991231T235959Z-zzzz.jsonl")
            newer = second.path.with_name("20000101T000000Z-aaaa.jsonl")
            os.replace(first.path, older)
            os.replace(second.path, newer)
            os.utime(older, (1_000, 1_000))
            os.utime(newer, (2_000, 2_000))

            latest = store.latest(workspace)

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.session_id, second.session_id)

    @unittest.skipIf(os.name == "nt", "Windows paths are case-insensitive")
    def test_workspace_key_keeps_case_on_case_sensitive_platforms(self) -> None:
        self.assertNotEqual(_workspace_key(Path("/tmp/Foo")), _workspace_key(Path("/tmp/foo")))

    def test_latest_skips_sessions_without_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore(load_settings(root / ".MinimalCode"))
            valid = store.create(workspace, provider="test", model="model", save=True)
            broken = store.create(workspace, provider="test", model="model", save=True)
            assert valid.path is not None and broken.path is not None
            broken.path.write_text("not json\n", encoding="utf-8")
            os.utime(valid.path, (1_000, 1_000))
            os.utime(broken.path, (2_000, 2_000))

            latest = store.latest(workspace)

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.session_id, valid.session_id)

    def test_resolve_accepts_full_recorded_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            store = SessionStore(settings)
            session = store.create(
                workspace,
                provider="test",
                model="model",
                save=True,
            )

            resolved = store.resolve(session.session_id, workspace)

            self.assertEqual(resolved.session_id, session.session_id)
            self.assertEqual(resolved.path, session.path)

    def test_corrupt_session_metadata_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = load_settings(root / ".MinimalCode")
            store = SessionStore(settings)
            session = store.create(
                workspace,
                provider="test",
                model="model",
                save=True,
            )
            assert session.path is not None
            lines = session.path.read_text(encoding="utf-8").splitlines(keepends=True)
            for broken in ("[]\n", "\"meta\"\n", "null\n", "not json\n"):
                with self.subTest(broken=broken):
                    session.path.write_text(broken + "".join(lines[1:]), encoding="utf-8")
                    resolved = store.resolve(session.path.stem, workspace)
                    self.assertEqual(resolved.path, session.path)
                    self.assertFalse(
                        any(
                            row["path"] == str(session.path)
                            and row["session_id"] == session.session_id
                            for row in store.list()
                        )
                    )


if __name__ == "__main__":
    unittest.main()
