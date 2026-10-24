from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

from .config import Settings
from .prompt import build_system_prompt
from .provider import OpenAICompatibleProvider, ProviderError
from .session import Session
from .shell import ShellSpec
from .tools import TOOL_SCHEMAS, ToolError, ToolRuntime, tool_result_text


def _json_size(messages: list[dict[str, Any]]) -> int:
    text = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return len(text)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return max(1, _json_size(messages) // 4)


def _tool_output_preview(result: dict[str, Any]) -> str:
    if result.get("status") == "success":
        if "stderr" in result and result.get("stderr"):
            return f"exit {result.get('exit_code', 0)}; stderr present"
        return "ok"
    return str(result.get("message") or result.get("status") or "error")


def _repair_tool_history(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    repaired: list[dict[str, Any]] = []
    pending: dict[str, str] = {}
    changed = False

    def flush_missing() -> None:
        nonlocal changed
        for call_id, name in list(pending.items()):
            repaired.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(
                        {
                            "status": "error",
                            "message": "interrupted before tool result was recorded",
                        },
                        separators=(",", ":"),
                    ),
                }
            )
            pending.pop(call_id, None)
            changed = True

    for message in messages:
        role = message.get("role")
        if pending and role != "tool":
            flush_missing()
        append = dict(message)
        repaired.append(append)
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            for call in message["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                if call_id:
                    pending[call_id] = name
        elif role == "tool":
            pending.pop(str(message.get("tool_call_id") or ""), None)
    flush_missing()
    return repaired, changed


@dataclass(slots=True)
class AgentResult:
    text: str
    completed: bool
    usage: dict[str, int] = field(default_factory=dict)
    turns: int = 0


class Agent:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: OpenAICompatibleProvider,
        session: Session,
        tool_runtime: ToolRuntime,
        shell: ShellSpec,
        max_turns: int | None = None,
        output: TextIO | None = None,
        error_output: TextIO | None = None,
        quiet: bool = False,
        stream_output: bool = True,
    ):
        self.settings = settings
        self.provider = provider
        self.session = session
        self.tools = tool_runtime
        self.shell = shell
        self.max_turns = int(max_turns or settings.get("max_turns", 50))
        self.output = output or sys.stdout
        self.error_output = error_output or sys.stderr
        self.quiet = quiet
        self.stream_output = stream_output
        self.last_prompt_tokens = 0
        self.total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

        repaired, changed = _repair_tool_history(session.messages)
        if changed:
            session.replace_messages(
                repaired,
                summary="Recovered an interrupted tool turn.",
                dropped_messages=0,
            )

    def _system_messages(self) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": build_system_prompt(
                    self.settings,
                    self.session.cwd,
                    self.shell,
                ),
            }
        ]

    def _messages(self) -> list[dict[str, Any]]:
        return [*self._system_messages(), *self.session.messages]

    def _accumulate_usage(self, usage: dict[str, int], messages: list[dict[str, Any]]) -> None:
        if usage:
            normalized = usage
        else:
            normalized = {
                "prompt_tokens": estimate_tokens(messages),
                "completion_tokens": 0,
                "total_tokens": estimate_tokens(messages),
            }
        for key in self.total_usage:
            self.total_usage[key] += int(normalized.get(key, 0))
        self.last_prompt_tokens = int(normalized.get("prompt_tokens", 0))
        self.session.append_usage(normalized)

    def _write_delta(self, text: str) -> None:
        if self.stream_output:
            self.output.write(text)
            self.output.flush()

    def _activity(self, message: str) -> None:
        if not self.quiet:
            self.error_output.write(message + "\n")
            self.error_output.flush()

    def _execute_tool(self, name: str, raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"status": "error", "message": f"invalid tool JSON: {exc}"}
        if not isinstance(arguments, dict):
            return {"status": "error", "message": "tool arguments must be an object"}
        self._activity(self.tools.activity(name, arguments))
        try:
            return self.tools.dispatch(name, arguments)
        except (ToolError, OSError, ValueError) as exc:
            return {"status": "error", "message": str(exc)}

    def run(self, user_text: str) -> AgentResult:
        if not user_text.strip():
            raise ValueError("user message is empty")
        self.session.append_message({"role": "user", "content": user_text})
        final_text = ""
        final_completed = False
        turn = 0

        for turn in range(1, self.max_turns + 1):
            if self.should_compact():
                self._compact(force=False)
            messages = self._messages()
            reply = self.provider.stream_chat(
                messages,
                tools=TOOL_SCHEMAS,
                on_text=self._write_delta,
            )
            self._accumulate_usage(reply.usage, messages)
            assistant = reply.as_message()
            self.session.append_message(assistant)
            final_text = reply.content

            if not reply.tool_calls:
                final_completed = reply.finish_reason in {None, "stop", "completed"}
                break

            for call in reply.tool_calls:
                result = self._execute_tool(call.name, call.arguments)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": tool_result_text(result),
                }
                self.session.append_message(tool_message)
                self._activity(f"  {_tool_output_preview(result)}")

        if not final_completed and turn >= self.max_turns:
            message = f"Reached max turns ({self.max_turns}); task may be incomplete."
            if not self.quiet:
                self.error_output.write(message + "\n")
                self.error_output.flush()
        elif not final_completed:
            message = f"Provider stopped early: {final_text or 'no final text'}"
            if not self.quiet:
                self.error_output.write(message + "\n")
                self.error_output.flush()
        return AgentResult(
            text=final_text,
            completed=final_completed,
            usage=dict(self.total_usage),
            turns=turn,
        )

    def should_compact(self) -> bool:
        threshold = float(self.settings.get("context.compact_at", 0.8))
        window = max(1, self.provider.context_window)
        current = max(self.last_prompt_tokens, estimate_tokens(self._messages()))
        return current >= int(window * threshold)

    def _compact(self, *, force: bool) -> bool:
        keep = max(4, int(self.settings.get("context.keep_messages", 12)))
        messages = self.session.messages
        if len(messages) <= keep + 2:
            return False

        target = len(messages) - keep
        cut = None
        for index in range(target, 0, -1):
            if messages[index].get("role") == "user":
                cut = index
                break
        if cut is None:
            if not force:
                return False
            raise RuntimeError("cannot compact: no safe user-message boundary found")

        old_messages = messages[:cut]
        transcript = self._transcript(old_messages)
        prompt = (
            "Summarize this coding-agent conversation for continuation. Preserve modified "
            "files, decisions, exact commands or tests already run, errors, and remaining "
            "work. Omit greetings and redundant reasoning. Use compact plain text."
        )
        try:
            summary = self.provider.complete_text(
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": transcript,
                    },
                ]
            ).strip()
        except ProviderError:
            if force:
                raise
            self._activity("[compact] skipped: provider request failed")
            return False
        if not summary:
            return False

        replacement = [
            {
                "role": "user",
                "content": (
                    "[Earlier conversation compacted. Continue from this state.]\n\n"
                    + summary
                ),
            },
            {
                "role": "assistant",
                "content": "Context received. I will continue from the recorded state.",
            },
            *messages[cut:],
        ]
        self.session.replace_messages(
            replacement,
            summary=summary,
            dropped_messages=len(old_messages),
        )
        self.last_prompt_tokens = 0
        self._activity(f"[compact] {len(old_messages)} messages -> summary")
        return True

    def compact(self) -> bool:
        return self._compact(force=True)

    def _transcript(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role", "unknown"))
            content = message.get("content")
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            lines.append(f"{role.upper()}: {content or ''}")
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    function = call.get("function") or {}
                    lines.append(
                        f"TOOL_CALL {function.get('name', '')}: "
                        f"{function.get('arguments', '')}"
                    )
        text = "\n".join(lines)
        max_chars = max(20_000, self.provider.context_window * 2)
        if len(text) > max_chars:
            head = int(max_chars * 0.25)
            tail = max_chars - head
            text = text[:head] + "\n...<middle omitted>...\n" + text[-tail:]
        return text

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider.name,
            "model": self.provider.model,
            "shell": self.shell.label,
            "session": str(self.session.path) if self.session.path else "<memory>",
            "messages": len(self.session.messages),
            "last_prompt_tokens": self.last_prompt_tokens,
            "context_window": self.provider.context_window,
            "usage": dict(self.total_usage),
        }
