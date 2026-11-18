"""Regression tests for the Clawd on Desk desktop-pet bridge.

The bridge is fail-open by design, so these tests pin down both halves: the
wire payload Clawd expects, and the promise that a disabled, unregistered or
offline pet never changes agent behaviour.
"""

from __future__ import annotations

import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Self
from unittest import mock

from minimalcode.agent import Agent
from minimalcode.clawd import (
    ClawdBridge,
    application_id,
    create_bridge,
    disabled_bridge,
    read_endpoint,
    register_custom_application,
    registered_agent_id,
    unregister_custom_application,
)
from minimalcode.config import load_settings
from minimalcode.session import SessionStore
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime


class _StateCollector:
    """Local stand-in for Clawd's ``/state`` endpoint."""

    def __init__(self, status: int = 200) -> None:
        self.payloads: list[dict[str, object]] = []
        collector = self
        reply_status = status

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                collector.payloads.append(json.loads(body))
                self.send_response(reply_status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args: object) -> None:
                return

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class SettingsStub:
    """Minimal ``Settings`` surface for ``create_bridge``."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get(self, path: str, default: object = None) -> object:
        node: object = self.values
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class RecordingBridge:
    """Records every lifecycle call an agent makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))

        return record


class EndpointTests(unittest.TestCase):
    def _write(self, root: Path, payload: object) -> Path:
        path = root / "runtime.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_reads_the_advertised_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"app": "clawd-on-desk", "port": 23334})
            self.assertEqual(read_endpoint(path), "http://127.0.0.1:23334/state")

    def test_accepts_a_decimal_string_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"app": "clawd-on-desk", "port": "23335"})
            self.assertEqual(read_endpoint(path), "http://127.0.0.1:23335/state")

    def test_rejects_a_foreign_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"app": "other-app", "port": 23333})
            self.assertIsNone(read_endpoint(path))

    def test_rejects_unusable_ports(self) -> None:
        for port in (0, 70_000, -1, "http", True, None):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as tmp:
                path = self._write(Path(tmp), {"app": "clawd-on-desk", "port": port})
                self.assertIsNone(read_endpoint(path))

    def test_missing_file_means_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_endpoint(Path(tmp) / "absent.json"))


class CustomApplicationTests(unittest.TestCase):
    def test_application_id_matches_clawd_rules(self) -> None:
        windows = application_id(Path("C:/Python/MinimalCode.exe"), platform="win32")
        self.assertRegex(windows, r"^custom-minimalcode-[0-9a-f]{12}$")
        # Clawd lowercases the executable path on Windows, so a differently
        # cased path must not fork the registration id.
        self.assertEqual(
            windows,
            application_id(Path("c:/python/minimalcode.EXE"), platform="win32"),
        )
        self.assertNotEqual(
            windows, application_id(Path("C:/Python/MinimalCode.exe"), platform="linux")
        )

    def test_registration_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "clawd-prefs.json"
            prefs.write_text(
                json.dumps({"version": 11, "customApplications": []}),
                encoding="utf-8",
            )
            executable = Path("C:/Python/minimalcode.exe")
            agent_id, written = register_custom_application(executable, prefs=prefs)
            self.assertTrue(written)
            self.assertEqual(registered_agent_id([prefs]), agent_id)
            # A second install is a no-op instead of a duplicate row.
            self.assertEqual(
                register_custom_application(executable, prefs=prefs),
                (agent_id, False),
            )
            data = json.loads(prefs.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 11)
            self.assertEqual(len(data["customApplications"]), 1)
            self.assertEqual(unregister_custom_application(prefs=prefs), agent_id)
            self.assertIsNone(registered_agent_id([prefs]))
            self.assertIsNone(unregister_custom_application(prefs=prefs))

    def test_registration_creates_a_missing_preference_file(self) -> None:
        # A source checkout that was never started has no prefs yet; Clawd's
        # migrate() backfills the rest of the schema on its next start.
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "nested" / "clawd-prefs.json"
            agent_id, written = register_custom_application(
                Path("C:/Python/minimalcode.exe"), prefs=prefs
            )
            self.assertTrue(written)
            data = json.loads(prefs.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["customApplications"])
            self.assertEqual(registered_agent_id([prefs]), agent_id)

    def test_malformed_preferences_raise_instead_of_being_replaced(self) -> None:
        # Rewriting a real document would discard the user's settings, so a
        # file that exists but does not parse stays an error.
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "clawd-prefs.json"
            prefs.write_text("{not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                register_custom_application(
                    Path("C:/Python/minimalcode.exe"), prefs=prefs
                )
            self.assertEqual(prefs.read_text(encoding="utf-8"), "{not json")


class ProbeTests(unittest.TestCase):
    def _probe(self, root: Path, port: int) -> int | None:
        from minimalcode.clawd import probe_state

        runtime = root / "runtime.json"
        runtime.write_text(
            json.dumps({"app": "clawd-on-desk", "port": port}), encoding="utf-8"
        )
        return probe_state(
            agent_id="custom-minimalcode-0123456789ab",
            session_id="probe",
            cwd=root,
            runtime_file=runtime,
        )

    def test_accepted_id_reports_200(self) -> None:
        with _StateCollector(200) as collector, tempfile.TemporaryDirectory() as tmp:
            status = self._probe(Path(tmp), collector.port)
        self.assertEqual(status, 200)
        self.assertEqual(collector.payloads[0]["event"], "SessionStart")

    def test_rejected_id_reports_204(self) -> None:
        with _StateCollector(204) as collector, tempfile.TemporaryDirectory() as tmp:
            status = self._probe(Path(tmp), collector.port)
        self.assertEqual(status, 204)

    def test_offline_clawd_reports_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from minimalcode.clawd import probe_state

            status = probe_state(
                agent_id="custom-minimalcode-0123456789ab",
                session_id="probe",
                cwd=Path(tmp),
                runtime_file=Path(tmp) / "absent.json",
            )
        self.assertIsNone(status)


class BridgeReportingTests(unittest.TestCase):
    def test_posts_lifecycle_states_in_order(self) -> None:
        with _StateCollector() as collector, tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime.json"
            runtime.write_text(
                json.dumps({"app": "clawd-on-desk", "port": collector.port}),
                encoding="utf-8",
            )
            bridge = ClawdBridge(
                agent_id="custom-minimalcode-0123456789ab",
                session_id="session-1",
                cwd=Path(tmp),
                runtime_file=runtime,
            )
            bridge.session_start()
            bridge.prompt()
            bridge.tool_start("read")
            bridge.tool_end("read")
            bridge.tool_end("write", ok=False)
            bridge.subagent_start()
            bridge.subagent_end()
            bridge.compact_start()
            bridge.compact_end()
            bridge.turn_end()
            bridge.shutdown()
            bridge._queue.join()

        self.assertEqual(
            [payload["event"] for payload in collector.payloads],
            [
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
                "SubagentStart",
                "SubagentStop",
                "PreCompact",
                "PostCompact",
                "Stop",
                "SessionEnd",
            ],
        )
        self.assertEqual(
            [payload["state"] for payload in collector.payloads],
            [
                "idle",
                "thinking",
                "working",
                "working",
                "error",
                "juggling",
                "working",
                "sweeping",
                "thinking",
                "attention",
                "idle",
            ],
        )
        self.assertEqual(collector.payloads[2]["tool_name"], "read")
        self.assertEqual(
            collector.payloads[0]["agent_id"], "custom-minimalcode-0123456789ab"
        )
        self.assertEqual(collector.payloads[0]["session_id"], "session-1")

    def test_disabled_bridge_drops_everything(self) -> None:
        bridge = disabled_bridge()
        bridge.session_start()
        bridge.tool_start("read")
        bridge.session_end()
        bridge.shutdown()
        self.assertEqual(list(bridge._queue.queue), [])

    def test_offline_clawd_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = ClawdBridge(
                agent_id="custom-minimalcode-0123456789ab",
                session_id="session-1",
                cwd=Path(tmp),
                runtime_file=Path(tmp) / "absent.json",
            )
            bridge.session_start()
            bridge.turn_end()
            bridge.shutdown()
            bridge._queue.join()
        self.assertIsNone(bridge._endpoint())

    def test_registration_is_preferred_over_the_native_id(self) -> None:
        settings = SettingsStub({"integrations": {"clawd": {}}})
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "clawd-prefs.json"
            prefs.write_text(
                json.dumps(
                    {
                        "customApplications": [
                            {
                                "id": "custom-minimalcode-abcdef123456",
                                "name": "MinimalCode",
                                "processName": "minimalcode.exe",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            patched = mock.patch("minimalcode.clawd.prefs_paths", return_value=[prefs])
            with patched:
                bridge = create_bridge(settings, session_id="session-1", cwd=Path(tmp))
                self.assertEqual(bridge.agent_id, "custom-minimalcode-abcdef123456")
                bridge.close()

    def test_explicit_agent_id_wins(self) -> None:
        settings = SettingsStub(
            {"integrations": {"clawd": {"agent_id": "custom-other-0123456789ab"}}}
        )
        bridge = create_bridge(settings, session_id="session-1", cwd=Path())
        self.assertEqual(bridge.agent_id, "custom-other-0123456789ab")
        bridge.close()

    def test_disabled_integration_is_a_noop(self) -> None:
        settings = SettingsStub({"integrations": {"clawd": {"enabled": False}}})
        bridge = create_bridge(settings, session_id="session-1", cwd=Path())
        self.assertFalse(bridge._running)

    def test_unregistered_install_stays_silent(self) -> None:
        # Clawd accepts an unknown id but files it under claude-code, so an
        # unregistered MinimalCode must send nothing at all.
        settings = SettingsStub({"integrations": {"clawd": {}}})
        empty = mock.patch("minimalcode.clawd.prefs_paths", return_value=[])
        with empty:
            bridge = create_bridge(settings, session_id="session-1", cwd=Path())
        self.assertFalse(bridge._running)

    def test_subagent_child_suppresses_reporting(self) -> None:
        # The parent reports SubagentStart/SubagentStop; a child card would
        # only flash and vanish, so the child must stay silent.
        import os

        from minimalcode import clawd

        settings = SettingsStub(
            {"integrations": {"clawd": {"agent_id": "custom-minimalcode-0123456789ab"}}}
        )
        with mock.patch.dict(os.environ, {clawd.SUPPRESS_ENV: "1"}):
            bridge = create_bridge(settings, session_id="session-1", cwd=Path())
            bridge.session_start()
        self.assertFalse(bridge._running)
        self.assertEqual(list(bridge._queue.queue), [])

    def test_subagent_and_bridge_agree_on_the_suppression_variable(self) -> None:
        from minimalcode import clawd, subagent

        self.assertEqual(subagent.CLAWD_ENV, clawd.SUPPRESS_ENV)


class _NullProvider:
    """Provider stand-in: these tests never run a model turn."""

    name = "fake"
    model = "fake-model"
    context_window = 100_000
    thinking = "none"
    notify = None

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        raise AssertionError("the agent must not call the provider here")

    def complete_text(self, messages):
        raise AssertionError("the agent must not call the provider here")


class AgentReportingTests(unittest.TestCase):
    def _agent(self, bridge: object) -> Agent:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        workspace = root / "workspace"
        workspace.mkdir()
        settings = load_settings(root / ".MinimalCode")
        shell = detect_shell()
        session = SessionStore(settings).create(
            workspace, provider="fake", model="fake-model", save=False
        )
        return Agent(
            settings=settings,
            provider=_NullProvider(),
            session=session,
            tool_runtime=ToolRuntime(settings, workspace, shell),
            shell=shell,
            quiet=True,
            stream_output=False,
            clawd=bridge,
        )

    def test_tool_loop_reports_working_and_error_states(self) -> None:
        bridge = RecordingBridge()
        agent = self._agent(bridge)
        agent.tools.dispatch = lambda name, arguments: {"status": "ok"}
        agent._run_tool("read", {})
        agent.tools.dispatch = lambda name, arguments: {
            "status": "error",
            "message": "boom",
        }
        agent._run_tool("write", {})
        self.assertEqual(
            list(bridge.calls),
            [
                ("tool_start", ("read",), {}),
                ("tool_end", ("read",), {"ok": True}),
                ("tool_start", ("write",), {}),
                ("tool_end", ("write",), {"ok": False}),
            ],
        )

    def test_subagent_uses_the_juggling_events(self) -> None:
        bridge = RecordingBridge()
        agent = self._agent(bridge)
        agent.tools.dispatch = lambda name, arguments: {"status": "ok"}
        agent._run_tool("subagent", {})
        self.assertEqual(
            [name for name, _, _ in bridge.calls], ["subagent_start", "subagent_end"]
        )

    def test_provider_retry_notifies_the_pet(self) -> None:
        bridge = RecordingBridge()
        agent = self._agent(bridge)
        agent.provider.notify("[retry] provider did not respond")
        self.assertEqual([name for name, _, _ in bridge.calls], ["notify"])

    def test_new_session_retargets_and_restarts_the_card(self) -> None:
        bridge = RecordingBridge()
        agent = self._agent(bridge)
        session = SessionStore(agent.settings).create(
            agent.session.cwd, provider="fake", model="fake-model", save=False
        )
        agent.new_session(session, agent.tools)
        self.assertEqual(
            [name for name, _, _ in bridge.calls],
            ["session_end", "retarget", "session_start"],
        )


if __name__ == "__main__":
    unittest.main()
