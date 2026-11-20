"""Regression tests for the chat web UI runtime and its HTTP routes.

The page is only a view over the same agent and tool runtime the terminal uses,
so these tests cover the two things that could silently diverge: what a settings
write actually persists, and what a browser is allowed to reach.
"""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.config import load_settings
from minimalcode.errors import ConfigError
from minimalcode.provider import ProviderReply, ToolCall
from minimalcode.webui import create_server
from minimalcode.webui.runtime import Busy, WebRuntime, browse_directories


class FakeProvider:
    """Stands in for the wire protocol; the agent loop stays real."""

    name = "fake"
    model = "fake-model"
    thinking = "medium"
    context_window = 100_000

    def __init__(self, config: dict[str, object] | None = None) -> None:
        self.config = dict(config or {})
        self.closed = False

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        if on_thinking:
            on_thinking("reading the workspace")
        if on_text:
            on_text("do")
            on_text("ne")
        return ProviderReply(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        )

    def complete_text(self, messages):
        return "summary"

    def close(self) -> None:
        self.closed = True


class TwoRoundThinkingProvider(FakeProvider):
    """Reasons, answers, calls a tool, then reasons and answers again."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def stream_chat(self, messages, *, tools=None, on_text=None, on_thinking=None):
        self.calls += 1
        if on_thinking:
            on_thinking(f"think-{self.calls}")
        if on_text:
            on_text(f"text-{self.calls}")
        if self.calls == 1:
            return ProviderReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="write",
                        arguments=json.dumps({"path": "note.txt", "content": "x\n"}),
                    )
                ],
                finish_reason="tool_calls",
                usage={},
            )
        return ProviderReply(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            usage={},
        )


def fake_create_provider(
    settings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    transport=None,
):
    fakeprovider_calls.append(thinking)
    name = provider_name or str(settings.data["provider"])
    return FakeProvider(), name, model or "fake-model"


fakeprovider_calls: list[str | None] = []


def sse_events(text: str) -> list[tuple[str, dict]]:
    """Parse a server-sent-event body into ``(event, payload)`` pairs."""
    frames: list[tuple[str, dict]] = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        event = ""
        data: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if event and data:
            frames.append((event, json.loads("\n".join(data))))
    return frames


def _strip_js_literals(script: str) -> str:
    """Blank out comments and literals so braces can be counted.

    Only structure matters here: a ``{`` inside a comment, a string, or a
    regular expression must not shift the depth of the code around it. The
    tricky part is telling a regex from a division sign, so the previous
    significant character decides: after an operator a slash opens a pattern,
    after an operand it divides.
    """
    pieces: list[str] = []
    index = 0
    length = len(script)
    last = ""
    openers = set("(,=:[!&|?{};+-*%<>~^")
    while index < length:
        char = script[index]
        if char.isspace():
            pieces.append(char)
            index += 1
            continue
        if char == "/" and script.startswith("//", index):
            end = script.find("\n", index)
            end = length if end == -1 else end
        elif char == "/" and script.startswith("/*", index):
            end = script.find("*/", index + 2)
            end = length if end == -1 else end + 2
        elif char in "\"'`":
            end = index + 1
            while end < length:
                if script[end] == "\\":
                    end += 2
                    continue
                if script[end] == char:
                    end += 1
                    break
                end += 1
        elif char == "/" and (not last or last in openers):
            # A regular expression literal; a slash inside ``[...]`` does not
            # close it.
            end = index + 1
            in_class = False
            while end < length:
                current = script[end]
                if current == "\\":
                    end += 2
                    continue
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    end += 1
                    break
                elif current == "\n":
                    break
                end += 1
        else:
            pieces.append(char)
            index += 1
            last = char
            continue
        pieces.append(" " * (end - index))
        index = end
        # A closed literal behaves like an operand, so the next slash divides.
        last = "0"
    return "".join(pieces)


class WebUiCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.workspace = Path(self.tmp.name) / "project"
        self.workspace.mkdir()
        self.settings = load_settings(self.home)

    def runtime(self, **kwargs) -> WebRuntime:
        runtime = WebRuntime(self.settings, self.workspace, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def settings_file(self) -> dict:
        return json.loads((self.home / "settings.json").read_text(encoding="utf-8"))


class RuntimeTests(WebUiCase):
    def test_describe_reports_the_selected_provider_and_format(self) -> None:
        state = self.runtime().describe()
        names = [item["name"] for item in state["providers"]]
        self.assertIn("deepseek", names)
        self.assertIn("ollama", names)
        self.assertEqual(state["provider"], "deepseek")
        self.assertEqual(state["protocol"], "openai_responses")
        self.assertIn("anthropic_messages", state["protocols"])
        self.assertEqual(state["workspace"], str(self.workspace.resolve()))

    def test_settings_write_persists_vendor_format_and_model(self) -> None:
        runtime = self.runtime()
        state = runtime.apply_settings(
            {
                "provider": "ollama",
                "protocol": "anthropic_messages",
                "model": "qwen2.5-coder",
            }
        )
        self.assertEqual(state["provider"], "ollama")
        self.assertEqual(state["protocol"], "anthropic_messages")
        document = self.settings_file()
        self.assertEqual(document["provider"], "ollama")
        self.assertEqual(
            document["providers"]["ollama"]["protocol"], "anthropic_messages"
        )
        self.assertEqual(document["providers"]["ollama"]["model"], "qwen2.5-coder")
        # The live view changes with the file, so the next turn uses the new
        # provider without restarting the process.
        self.assertEqual(runtime.settings.data["provider"], "ollama")
        self.assertEqual(
            runtime.settings.data["providers"]["ollama"]["model"], "qwen2.5-coder"
        )

    def test_a_second_write_keeps_the_earlier_overrides(self) -> None:
        runtime = self.runtime()
        runtime.apply_settings({"provider": "ollama", "protocol": "anthropic_messages"})
        runtime.apply_settings({"model": "second-model"})
        document = self.settings_file()
        self.assertEqual(
            document["providers"]["ollama"]["protocol"], "anthropic_messages"
        )
        self.assertEqual(document["providers"]["ollama"]["model"], "second-model")

    def test_settings_reject_unknown_vendor_and_format(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(ConfigError):
            runtime.apply_settings({"provider": "not-a-provider"})
        with self.assertRaises(ConfigError):
            runtime.apply_settings({"protocol": "grpc"})
        with self.assertRaises(ConfigError):
            runtime.apply_settings({"model": 7})

    def test_settings_keep_command_line_overrides(self) -> None:
        runtime = self.runtime()
        self.settings.data["sandbox"]["enabled"] = True
        runtime.apply_settings({"provider": "ollama"})
        self.assertTrue(runtime.settings.data["sandbox"]["enabled"])

    def test_a_provider_injected_in_memory_is_not_written(self) -> None:
        # `--anthropic-env` adds a provider that has no preset. Writing a block
        # for it would leave a half-defined provider that fails the next load.
        self.settings.data["providers"]["anthropic-env"] = {
            "protocol": "anthropic_messages",
            "base_url": "https://example.test",
        }
        self.settings.data["provider"] = "anthropic-env"
        runtime = self.runtime()
        runtime.apply_settings({"model": "claude-env-model"})
        self.assertFalse((self.home / "settings.json").exists())
        self.assertEqual(runtime.effective_model(), "claude-env-model")
        self.assertEqual(
            runtime.settings.data["providers"]["anthropic-env"]["model"],
            "claude-env-model",
        )

    def test_switching_the_model_keeps_the_conversation(self) -> None:
        runtime = self.runtime()
        created: list[FakeProvider] = []

        def factory(settings, *, provider_name=None, model=None, **kwargs):
            provider = FakeProvider()
            created.append(provider)
            return provider, provider_name or "fake", model or "fake-model"

        with patch("minimalcode.webui.runtime.create_provider", factory):
            runtime.chat("hello", lambda event, data: None)
            runtime.apply_settings({"model": "next-model"})
            runtime.chat("again", lambda event, data: None)
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].closed)
        self.assertEqual(runtime.describe()["model"], "next-model")
        self.assertEqual(
            [item["role"] for item in runtime.history()["messages"]],
            ["user", "assistant", "user", "assistant"],
        )

    def test_workspace_change_requires_a_directory(self) -> None:
        runtime = self.runtime()
        notes = self.workspace / "notes.txt"
        notes.write_text("x", encoding="utf-8")
        with self.assertRaises(ConfigError):
            runtime.set_workspace(str(notes))
        with self.assertRaises(ConfigError):
            runtime.set_workspace("")
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        state = runtime.set_workspace(str(other))
        self.assertEqual(state["workspace"], str(other.resolve()))

    def test_browse_lists_folders_only(self) -> None:
        (self.workspace / "sub").mkdir()
        (self.workspace / "notes.txt").write_text("x", encoding="utf-8")
        listing = browse_directories(str(self.workspace))
        self.assertEqual([item["name"] for item in listing["directories"]], ["sub"])
        self.assertEqual(listing["path"], str(self.workspace.resolve()))
        with self.assertRaises(ConfigError):
            browse_directories(str(self.workspace / "notes.txt"))

    def test_chat_streams_events_and_records_history(self) -> None:
        runtime = self.runtime()
        events: list[tuple[str, dict]] = []
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: events.append((event, data)))
        names = [event for event, _ in events]
        self.assertIn("start", names)
        self.assertIn("thinking", names)
        self.assertIn("done", names)
        text = "".join(data["text"] for event, data in events if event == "delta")
        self.assertEqual(text, "done")
        done = next(data for event, data in events if event == "done")
        self.assertTrue(done["completed"])
        self.assertEqual(done["usage"]["total_tokens"], 10)
        messages = runtime.history()["messages"]
        self.assertEqual([item["content"] for item in messages], ["hello", "done"])
        # 思考随 assistant 消息回到页面，刷新后才展开得出来。
        self.assertEqual(
            [item["text"] for item in messages[1]["thinking"]],
            ["reading the workspace"],
        )

    def test_reasoning_survives_a_reload(self) -> None:
        """F5 rebuilds the page from /api/history, so 思考 must be in it."""
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        session_id = runtime.history()["session"]["id"]
        # A fresh runtime is what the browser talks to after the reload.
        reloaded = self.runtime()
        reloaded.open_session(session_id)
        messages = reloaded.history()["messages"]
        self.assertEqual(messages[1]["content"], "done")
        self.assertEqual(
            [item["text"] for item in messages[1]["thinking"]],
            ["reading the workspace"],
        )

    def test_a_second_turn_does_not_inherit_the_first_reasoning(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
            runtime.chat("again", lambda event, data: None)
        messages = runtime.history()["messages"]
        assistants = [item for item in messages if item["role"] == "assistant"]
        self.assertEqual(
            [[entry["text"] for entry in item["thinking"]] for item in assistants],
            [["reading the workspace"], ["reading the workspace"]],
        )

    def test_every_tool_round_keeps_its_own_reasoning(self) -> None:
        runtime = self.runtime()
        provider = TwoRoundThinkingProvider()

        def factory(*args, **kwargs):
            return provider, "fake", "fake-model"

        with patch("minimalcode.webui.runtime.create_provider", factory):
            runtime.chat("go", lambda event, data: None)
        messages = runtime.history()["messages"]
        self.assertEqual(
            [item["text"] for item in messages[1]["thinking"]],
            ["think-1", "think-2"],
        )

    def test_deleting_a_conversation_forgets_its_reasoning(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        session_id = runtime.history()["session"]["id"]
        runtime.delete_session(session_id)
        stored = json.loads(
            (self.home / "webui" / "thinking.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(session_id, stored["sessions"])

    def test_a_running_turn_blocks_settings_and_workspace_changes(self) -> None:
        runtime = self.runtime()
        started = threading.Event()
        release = threading.Event()

        class SlowProvider(FakeProvider):
            def stream_chat(self, messages, **kwargs):
                started.set()
                release.wait(10)
                return super().stream_chat(messages, **kwargs)

        def factory(*args, **kwargs):
            return SlowProvider(), "fake", "fake-model"

        worker = threading.Thread(
            target=runtime.chat, args=("hello", lambda event, data: None), daemon=True
        )
        with patch("minimalcode.webui.runtime.create_provider", factory):
            worker.start()
            self.assertTrue(started.wait(10))
            with self.assertRaises(Busy):
                runtime.set_workspace(str(self.workspace))
            with self.assertRaises(Busy):
                runtime.apply_settings({"provider": "ollama"})
            release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())

    def test_sessions_list_and_reopen_a_recorded_conversation(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        rows = runtime.sessions()
        self.assertEqual(len(rows), 1)
        session_id = rows[0]["id"]
        self.assertEqual(session_id, runtime.describe()["session"]["id"])

        # A fresh runtime (a reopened page) sees the conversation in the list.
        other = WebRuntime(self.settings, self.workspace)
        self.addCleanup(other.close)
        self.assertEqual([row["id"] for row in other.sessions()], [session_id])
        state = other.open_session(session_id)
        self.assertEqual(state["session"]["id"], session_id)
        self.assertEqual(
            [item["content"] for item in other.history()["messages"]],
            ["hello", "done"],
        )
        with self.assertRaises(ConfigError):
            other.open_session("not-a-session")

    def test_sessions_are_scoped_to_the_workspace(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        other_dir = Path(self.tmp.name) / "other"
        other_dir.mkdir()
        elsewhere = WebRuntime(self.settings, other_dir)
        self.addCleanup(elsewhere.close)
        self.assertEqual(elsewhere.sessions(), [])

    def test_folders_group_conversations_and_survive_a_reload(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        session_id = runtime.sessions()[0]["id"]
        created = runtime.create_folder("  工作   笔记 ")
        self.assertEqual(created["folder"]["name"], "工作 笔记")
        folder_id = created["folder"]["id"]

        state = runtime.assign_folder(session_id, folder_id)
        self.assertEqual([row["id"] for row in state["folders"]], [folder_id])
        self.assertEqual(state["sessions"][0]["folder"], folder_id)
        with self.assertRaises(ConfigError):
            runtime.create_folder("工作 笔记")
        with self.assertRaises(ConfigError):
            runtime.assign_folder(session_id, "not-a-folder")

        # A reopened page reads the same tree from the config home.
        other = WebRuntime(self.settings, self.workspace)
        self.addCleanup(other.close)
        self.assertEqual([row["name"] for row in other.folders()], ["工作 笔记"])
        self.assertEqual(other.sessions()[0]["folder"], folder_id)
        other.rename_folder(folder_id, "资料")
        self.assertEqual([row["name"] for row in other.folders()], ["资料"])

    def test_deleting_a_folder_keeps_its_conversations(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        session_id = runtime.sessions()[0]["id"]
        folder_id = runtime.create_folder("notes")["folder"]["id"]
        runtime.assign_folder(session_id, folder_id)

        state = runtime.delete_folder(folder_id)
        self.assertEqual(state["folders"], [])
        self.assertEqual(len(state["sessions"]), 1)
        self.assertIsNone(state["sessions"][0]["folder"])

    def test_deleting_the_open_conversation_removes_the_file(self) -> None:
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        row = runtime.sessions()[0]
        folder_id = runtime.create_folder("keep")["folder"]["id"]
        runtime.assign_folder(row["id"], folder_id)
        recorded = next(
            item for item in runtime.store.list() if item["session_id"] == row["id"]
        )

        state = runtime.delete_session(row["id"])
        self.assertEqual(state["sessions"], [])
        self.assertIsNone(state["state"]["session"])
        self.assertFalse(Path(recorded["path"]).exists())
        # The folder itself stays; only the assignment went with the transcript.
        self.assertEqual([item["id"] for item in state["folders"]], [folder_id])
        self.assertEqual(runtime.library.assignments(self.workspace), {})
        with self.assertRaises(ConfigError):
            runtime.delete_session(row["id"])

    def test_thinking_switch_rebuilds_the_provider(self) -> None:
        fakeprovider_calls.clear()
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
            self.assertIsNone(fakeprovider_calls[-1])
            self.assertTrue(runtime.describe()["thinking"])
            state = runtime.set_thinking(False)
            self.assertFalse(state["thinking"])
            self.assertEqual(fakeprovider_calls[-1], "none")
            runtime.set_thinking(True)
            self.assertIsNone(fakeprovider_calls[-1])
        with self.assertRaises(ConfigError):
            runtime.set_thinking("no")

    def test_a_conversation_from_another_workspace_cannot_be_opened(self) -> None:
        """The sidebar only lists this workspace, and nothing else is in reach."""
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        session_id = runtime.history()["session"]["id"]

        other_dir = Path(self.tmp.name) / "elsewhere"
        other_dir.mkdir()
        other = WebRuntime(self.settings, other_dir)
        self.addCleanup(other.close)

        self.assertEqual(other.sessions(), [])
        with self.assertRaises(ConfigError):
            other.open_session(session_id)

    def test_a_failed_provider_switch_keeps_the_running_one(self) -> None:
        """A selection that cannot be built must not describe the live agent."""
        runtime = self.runtime()
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            runtime.chat("hello", lambda event, data: None)
        before = runtime.describe()

        def failing_factory(*args, **kwargs):
            raise ConfigError("provider is not configured")

        with (
            patch("minimalcode.webui.runtime.create_provider", failing_factory),
            self.assertRaises(ConfigError),
        ):
            runtime.apply_settings({"model": "another-model"})

        after = runtime.describe()
        self.assertEqual(after["provider"], before["provider"])
        self.assertEqual(after["model"], before["model"])

    def test_a_settings_change_cannot_start_inside_a_turn(self) -> None:
        """The idle check and the change it protects share one gate."""
        runtime = self.runtime()
        started = threading.Event()
        release = threading.Event()

        class SlowProvider(FakeProvider):
            def stream_chat(self, messages, **kwargs):
                started.set()
                release.wait(10)
                return super().stream_chat(messages, **kwargs)

        def factory(*args, **kwargs):
            return SlowProvider(), "fake", "fake-model"

        worker = threading.Thread(
            target=runtime.chat, args=("hello", lambda event, data: None), daemon=True
        )
        with patch("minimalcode.webui.runtime.create_provider", factory):
            worker.start()
            self.assertTrue(started.wait(10))
            with self.assertRaises(Busy):
                runtime.new_session()
            with self.assertRaises(Busy):
                runtime.delete_folder("anything")
            release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())


class HttpTests(WebUiCase):
    def setUp(self) -> None:
        super().setUp()
        self.runtime = WebRuntime(self.settings, self.workspace)
        self.addCleanup(self.runtime.close)
        self.server = create_server(self.runtime, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        payload = json.dumps(body).encode() if body is not None else None
        sent = {"Content-Type": "application/json"} if payload else {}
        sent.update(headers or {})
        try:
            connection.request(method, path, body=payload, headers=sent)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_one_connection_serves_two_requests(self) -> None:
        """A browser reuses the socket; the second reply must still be written."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request("GET", "/api/state")
            first = connection.getresponse()
            self.assertEqual(first.status, 200)
            first.read()
            connection.request("GET", "/api/state")
            second = connection.getresponse()
            self.assertEqual(second.status, 200)
            self.assertIn("deepseek", json.loads(second.read()))
        finally:
            connection.close()

    def test_page_and_state_are_served(self) -> None:
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("MinimalCode", page)
        # The page keeps the chat-demo structure and design tokens instead of a
        # bespoke layout: a sider, the greeting, and the token block in the CSS.
        for fragment in (
            'class="sider"',
            "开启新对话",
            "新建文件夹",
            "今天有什么可以帮到你？",
        ):
            self.assertIn(fragment, page)
        status, css = self.request("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"--dsw-alias-bg-base", css)
        self.assertIn(b"data-ds-dark-theme", css)
        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/chat", script)
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["workspace"], str(self.workspace.resolve()))
        self.assertTrue(state["providers"])
        self.assertIn("thinking", state)

    def test_stylesheet_braces_are_balanced(self) -> None:
        """One stray ``{`` makes the browser drop every rule after it.

        A duplicated rule header is invisible in review and in the Python
        tests, but it silently strips the main pane back to plain HTML: the
        sidebar keeps its styling and the composer collapses into the corner.
        """
        status, css = self.request("GET", "/app.css")
        self.assertEqual(status, 200)
        text = re.sub(r"/\*.*?\*/", "", css.decode(), flags=re.DOTALL)
        depth = 0
        for index, char in enumerate(text):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                self.assertGreaterEqual(
                    depth, 0, f"extra closing brace at character {index}"
                )
        self.assertEqual(depth, 0, "stylesheet has an unclosed rule block")

    def test_the_collapsed_sidebar_can_be_reopened(self) -> None:
        """收起边栏会 hidden 掉整个 .sider，展开按钮必须在它外面。

        之前的 sider-toggle 在 .sider 内部，收起后连自己也一起消失，只能刷新
        页面才恢复；刷新会重置 siderHidden，所以看上去像「刷新才能展开」。
        """
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertLess(page.index("</aside>"), page.index('id="mobile-menu"'))

        status, css = self.request("GET", "/app.css")
        self.assertEqual(status, 200)
        parts = css.decode().split(".sider[hidden] ~ .main .mobile-menu", 1)
        self.assertEqual(len(parts), 2, "收起状态没有显示展开按钮的规则")
        self.assertIn("display: inline-flex", parts[1].split("}", 1)[0])

        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        parts = script.decode().split("dom.mobileMenu.addEventListener", 1)
        self.assertEqual(len(parts), 2)
        self.assertIn("setSiderHidden(false)", parts[1].split("});", 1)[0])

    def test_page_script_declares_its_functions_at_top_level(self) -> None:
        """A function nested by accident takes the whole page down.

        One bad edit left ``closeDrawer`` and ``setSiderHidden`` declared inside
        ``openDrawer``: the script still parsed, but ``bindEvents`` referenced an
        undefined ``closeDrawer`` and threw before any data was loaded, so the
        page came up with an empty sidebar. ``node --check`` cannot see that, so
        the brace depth of every ``function`` declaration is checked here.
        """
        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        depth = 0
        declarations: list[tuple[str, int, int]] = []
        for number, line in enumerate(
            _strip_js_literals(script.decode()).splitlines(), start=1
        ):
            match = re.match(r"\s*function\s+([A-Za-z_$][\w$]*)\s*\(", line)
            if match:
                declarations.append((match.group(1), depth, number))
            depth += line.count("{") - line.count("}")
        self.assertTrue(declarations)
        nested = [row for row in declarations if row[1] != 0]
        self.assertEqual(
            nested, [], f"function declarations not at top level: {nested}"
        )
        self.assertEqual(depth, 0, "script has an unbalanced brace")

    def test_settings_endpoint_writes_the_file(self) -> None:
        status, body = self.request(
            "POST",
            "/api/settings",
            {"provider": "ollama", "protocol": "openai_chat", "model": "local-model"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["provider"], "ollama")
        self.assertEqual(self.settings_file()["provider"], "ollama")

    def test_sessions_thinking_and_session_routes(self) -> None:
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            status, _ = self.request("POST", "/api/chat", {"message": "hello"})
        self.assertEqual(status, 200)
        status, body = self.request("GET", "/api/sessions")
        self.assertEqual(status, 200)
        rows = json.loads(body)["sessions"]
        self.assertEqual(len(rows), 1)

        status, body = self.request("POST", "/api/session", {"id": rows[0]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["session"]["id"], rows[0]["id"])
        status, body = self.request("POST", "/api/session", {"id": "nope"})
        self.assertEqual(status, 400)

        status, body = self.request("POST", "/api/thinking", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["thinking"])
        status, _ = self.request("POST", "/api/thinking", {"enabled": "yes"})
        self.assertEqual(status, 400)

    def test_folder_routes_group_rename_and_delete_conversations(self) -> None:
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            status, _ = self.request("POST", "/api/chat", {"message": "hello"})
        self.assertEqual(status, 200)
        status, body = self.request("GET", "/api/sessions")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["folders"], [])
        session_id = payload["sessions"][0]["id"]

        status, body = self.request("POST", "/api/folder", {"name": "资料"})
        self.assertEqual(status, 200)
        created = json.loads(body)
        folder_id = created["folder"]["id"]
        self.assertEqual([row["name"] for row in created["folders"]], ["资料"])

        status, body = self.request(
            "POST", "/api/session/folder", {"id": session_id, "folder": folder_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sessions"][0]["folder"], folder_id)

        status, body = self.request(
            "POST", "/api/folder", {"id": folder_id, "name": "资料库"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["folders"][0]["name"], "资料库")

        status, body = self.request(
            "POST", "/api/session/folder", {"id": session_id, "folder": None}
        )
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["sessions"][0]["folder"])

        status, body = self.request("POST", "/api/session/delete", {"id": session_id})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sessions"], [])
        status, _ = self.request("POST", "/api/session/delete", {"id": session_id})
        self.assertEqual(status, 400)

        status, body = self.request("POST", "/api/folder/delete", {"id": folder_id})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["folders"], [])
        status, _ = self.request("POST", "/api/folder", {"name": "   "})
        self.assertEqual(status, 400)

    def test_workspace_endpoint_rejects_a_file(self) -> None:
        notes = self.workspace / "notes.txt"
        notes.write_text("x", encoding="utf-8")
        status, body = self.request("POST", "/api/workspace", {"path": str(notes)})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_cross_origin_and_foreign_host_are_refused(self) -> None:
        status, _ = self.request(
            "POST", "/api/new", {}, headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST", "/api/new", {}, headers={"Host": "evil.example:8090"}
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST",
            "/api/new",
            {},
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)

    def test_another_local_origin_is_refused(self) -> None:
        """另一个本地应用的页面不是同源。

        只要 Origin 的主机名是回环就放行，会让任何跑在本机别的端口上的页面
        （或者一个有 XSS 漏洞的 localhost 页面）POST /api/chat，从而在工作区里
        执行命令。同源要主机名和端口都一样。
        """
        status, _ = self.request(
            "POST",
            "/api/new",
            {},
            headers={"Origin": f"http://127.0.0.1:{self.port + 1}"},
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST",
            "/api/new",
            {},
            headers={"Origin": f"http://localhost:{self.port}"},
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST",
            "/api/new",
            {},
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)

    def test_restored_reasoning_is_expanded_and_toggleable(self) -> None:
        """刷新重建的思考块默认展开，标题既是开关也是键盘可达的按钮。"""
        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        text = script.decode()
        self.assertIn("function setThinkingOpen", text)
        restored = text.split("function restoreThinking", 1)[1].split("\n}", 1)[0]
        # 恢复时沿用浏览器偏好而不是无条件折叠：流式时整段推理是看得见的，
        # 刷新后如果强制折叠，用户看到的就是「思考不见了」。
        self.assertNotIn("thinkBody.hidden = true", restored)
        self.assertIn("setThinkingOpen(round, app.thinkingOpen)", restored)
        self.assertIn('setAttribute("aria-expanded"', text)
        self.assertIn("toggleThinking", text)

    def test_chat_streams_server_sent_events(self) -> None:
        with patch("minimalcode.webui.runtime.create_provider", fake_create_provider):
            status, body = self.request("POST", "/api/chat", {"message": "hello"})
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("event: delta", text)
        self.assertIn("event: done", text)
        self.assertIn('"completed": true', text)
        status, body = self.request("GET", "/api/history")
        self.assertEqual(status, 200)
        messages = json.loads(body)["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["thinking"][0]["text"], "reading the workspace")

    def test_each_tool_round_arrives_as_its_own_block(self) -> None:
        """The page groups 思考/正文/工具 per round, so the stream must say when."""
        provider = TwoRoundThinkingProvider()

        def factory(*args, **kwargs):
            return provider, "fake", "fake-model"

        with patch("minimalcode.webui.runtime.create_provider", factory):
            status, body = self.request("POST", "/api/chat", {"message": "go"})
        self.assertEqual(status, 200)
        events = sse_events(body.decode())
        names = [name for name, _ in events]
        self.assertEqual(
            [row for name, row in events if name == "round"],
            [{"index": 1}, {"index": 2}],
        )

        rounds = [index for index, name in enumerate(names) if name == "round"]
        first_thinking = names.index("thinking")
        first_delta = names.index("delta")
        first_activity = names.index("activity")
        # Round 1: reasoning, answer, then the tool it asked for.
        self.assertLess(first_thinking, first_delta)
        self.assertLess(first_delta, first_activity)
        # Round 2 starts after that tool finished, and repeats the same order.
        self.assertLess(first_activity, rounds[1])
        second_thinking = names.index("thinking", first_activity)
        second_delta = names.index("delta", first_activity)
        self.assertLess(rounds[1], second_thinking)
        self.assertLess(second_thinking, second_delta)
        self.assertEqual(names[-1], "done")

    def test_a_refused_turn_arrives_as_an_error_event(self) -> None:
        # The 200 status line is already sent when the turn starts, so a
        # configuration failure has to reach the page as an event.
        def broken(*args, **kwargs):
            raise ConfigError("provider is not configured")

        with patch("minimalcode.webui.runtime.create_provider", broken):
            status, body = self.request("POST", "/api/chat", {"message": "hello"})
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("event: error", text)
        self.assertIn("provider is not configured", text)


if __name__ == "__main__":
    unittest.main()
