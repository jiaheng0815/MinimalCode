from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from .config import Settings
from .highlight import (
    MarkdownHighlighter,
    TerminalSanitizer,
    ThinkingRenderer,
    sanitize_terminal_text,
    style_activity_tag,
)
from .prompt import build_system_prompt, merge_agents_summary
from .provider import OpenAICompatibleProvider, ProviderError
from .session import Session
from .shell import ShellSpec
from .tools import ToolError, ToolRuntime, available_tool_schemas, tool_result_text


def _is_wide_character(char: str) -> bool:
    """Return True for scripts whose characters cost roughly one token each."""
    code = ord(char)
    return (
        0x1100 <= code <= 0x115F  # Hangul Jamo
        or 0x2E80 <= code <= 0x9FFF  # CJK radicals through CJK unified ideographs
        or 0xA000 <= code <= 0xA4CF  # Yi
        or 0xAC00 <= code <= 0xD7A3  # Hangul syllables
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0xFE30 <= code <= 0xFE6F  # CJK compatibility forms
        or 0xFF00 <= code <= 0xFF60  # Fullwidth forms
        or 0xFFE0 <= code <= 0xFFE6
        or 0x1F300 <= code <= 0x1FAFF  # Emoji
        or 0x20000 <= code <= 0x3FFFD
    )


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens for one string, weighted by character width.

    ``len(text) // 4`` is a decent ASCII rule and a fourfold undercount for CJK:
    a Chinese character carries roughly one token, not a quarter of one. Because
    this estimate is the compaction trigger and the fallback when a provider
    omits usage, undercounting silently lets the history outgrow the window.
    """
    if not text:
        return 0
    wide = 0
    for char in text:
        if _is_wide_character(char):
            wide += 1
    narrow = len(text) - wide
    return wide + (narrow + 3) // 4


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return max(1, estimate_text_tokens(json.dumps(messages, ensure_ascii=False)))


def _tool_output_preview(result: dict[str, Any]) -> str:
    if result.get("status") == "success":
        if "stderr" in result and result.get("stderr"):
            return f"exit {result.get('exit_code', 0)}; stderr present"
        return "ok"
    return str(result.get("message") or result.get("status") or "error")


_ACTIVITY_OUTPUT_MAX_LINES = 12
_ACTIVITY_OUTPUT_MAX_CHARS = 4000
_INCOMPLETE_FINISH_REASONS = {
    "length",
    "max_tokens",
    "content_filter",
    "max_output_tokens",
    "failed",
    "cancelled",
    "canceled",
    "incomplete",
    "incomplete_stream",
    "in_progress",
    "queued",
    "server_error",
}


def _clip_activity_output(text: str) -> str:
    """Keep tool activity readable without echoing entire command output."""
    lines = text.splitlines()
    if len(lines) > _ACTIVITY_OUTPUT_MAX_LINES:
        head = _ACTIVITY_OUTPUT_MAX_LINES // 2
        tail = _ACTIVITY_OUTPUT_MAX_LINES - head
        omitted = len(lines) - _ACTIVITY_OUTPUT_MAX_LINES
        lines = [*lines[:head], f"  … {omitted} lines omitted", *lines[-tail:]]
    clipped = "\n".join(lines)
    if len(clipped) > _ACTIVITY_OUTPUT_MAX_CHARS:
        clipped = clipped[:_ACTIVITY_OUTPUT_MAX_CHARS] + "…"
    return clipped


def _shell_output_activity(result: dict[str, Any]) -> str:
    lines: list[str] = []
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int):
        lines.append(f"  exit {exit_code}")
    else:
        lines.append(f"  {result.get('status', 'error')}")

    stdout = _clip_activity_output(str(result.get("stdout") or "").rstrip())
    stderr = _clip_activity_output(str(result.get("stderr") or "").rstrip())
    message = str(result.get("message") or "").strip()
    if stdout:
        lines.extend(("  stdout:", stdout))
    if stderr:
        lines.extend(("  stderr:", stderr))
    if message:
        lines.append(f"  {message}")
    return "\n".join(lines)


def _repair_tool_history(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    repaired: list[dict[str, Any]] = []
    changed = False
    call_order: list[str] | None = None
    call_names: dict[str, str] = {}
    block_results: list[dict[str, Any]] = []

    def synthetic_result(call_id: str, name: str) -> dict[str, Any]:
        return {
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

    def flush_block() -> None:
        nonlocal changed, call_order, call_names, block_results
        if call_order is None:
            block_results = []
            return
        by_id: dict[str, dict[str, Any]] = {}
        for result in block_results:
            call_id = str(result.get("tool_call_id") or "")
            if call_id in call_names and call_id not in by_id:
                by_id[call_id] = result
            else:
                # A result for an unknown or already answered call id makes
                # most providers reject the whole request; drop it.
                changed = True
        for call_id in call_order:
            result = by_id.get(call_id)
            if result is not None:
                repaired.append(result)
            else:
                repaired.append(synthetic_result(call_id, call_names[call_id]))
                changed = True
        call_order = None
        call_names = {}
        block_results = []

    for message in messages:
        role = message.get("role")
        if call_order is not None and role != "tool":
            flush_block()
        if role == "tool" and call_order is not None:
            block_results.append(dict(message))
            continue
        if role == "tool":
            # Orphan result: no assistant tool_calls precedes it, so replaying
            # it is a protocol error for every OpenAI-compatible server.
            changed = True
            continue
        repaired.append(dict(message))
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            call_order = []
            call_names = {}
            for call in message["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                if call_id:
                    if call_id not in call_names:
                        call_order.append(call_id)
                    call_names[call_id] = name
    flush_block()
    return repaired, changed


@dataclass(slots=True)
class AgentResult:
    text: str
    completed: bool
    usage: dict[str, int] = field(default_factory=dict)
    run_usage: dict[str, int] = field(default_factory=dict)
    turns: int = 0
    # True when at least one turn in this run had no provider-reported usage and
    # the numbers came from ``estimate_tokens`` instead.
    usage_estimated: bool = False


SUMMARY_PROMPT = """\
Summarize this coding-agent session as durable repository notes for the next \
session. Record what was asked, the files created or modified, the decisions \
taken and why, the commands and tests that were actually run with their \
outcomes, and any remaining work, blockers, or known limitations. Write short \
Markdown sections with terse bullets. Omit greetings, narration, and reasoning \
that did not change the result. Never claim a check ran unless the transcript \
shows it ran, and do not wrap the answer in a code fence.
"""


def _compaction_boundary(messages: list[dict[str, Any]], target: int) -> int | None:
    """Return an index that keeps ``messages[index:]`` replayable.

    A cut may only land before a message that does not belong to a tool-call
    block, otherwise the kept tail starts with an orphan tool result.
    """
    for index in range(target, 0, -1):
        if messages[index].get("role") == "user":
            return index
    # Some session files start mid-turn; fall forward to the next user message
    # so compaction can still make progress instead of silently never running.
    for index in range(target + 1, len(messages)):
        if messages[index].get("role") == "user":
            return index
    for index in range(target, 0, -1):
        if messages[index].get("role") == "tool":
            continue
        previous = messages[index - 1]
        if previous.get("role") == "assistant" and previous.get("tool_calls"):
            continue
        return index
    return None


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
        markdown_highlight: bool | None = None,
    ):
        self.settings = settings
        self.provider = provider
        self.session = session
        self.tools = tool_runtime
        self.tool_schemas = available_tool_schemas(
            self.tools.web_client, self.tools.vision_client, self.tools.subagent_enabled
        )
        self.shell = shell
        configured_turns = (
            settings.get("max_turns", 1000) if max_turns is None else max_turns
        )
        self.max_turns = max(1, int(configured_turns))
        self.output = output or sys.stdout
        self.error_output = error_output or sys.stderr
        self.quiet = quiet
        self.stream_output = stream_output
        # Retry notices travel through the activity stream, so --quiet and
        # --json stay silent while an interactive turn shows the retries.
        if hasattr(self.provider, "notify"):
            self.provider.notify = self._activity
        highlight_enabled = (
            bool(settings.get("output.markdown_highlight", True))
            if markdown_highlight is None
            else markdown_highlight
        )
        self._highlighter = (
            MarkdownHighlighter(
                self.output,
                theme=str(settings.get("output.theme", "dark")),
            )
            if stream_output and highlight_enabled
            else None
        )
        # Reasoning is shown in the six-level heading style, so it follows the
        # same switch as highlighting: no ANSI means no reasoning block, and
        # redirected stdout keeps carrying the answer alone.
        self._thinking_renderer = (
            ThinkingRenderer(
                self.output,
                theme=str(settings.get("output.theme", "dark")),
            )
            if stream_output and highlight_enabled
            else None
        )
        self.last_prompt_tokens = 0
        self.estimated_turns = 0
        self._system_prompt: str | None = None
        self._plain_sanitizer = TerminalSanitizer()
        self.total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

        self._repair_history()

    def _repair_history(self) -> None:
        """Close tool_calls left without results by an interrupted turn.

        Ctrl-C or a provider failure can stop the loop between an assistant
        tool call and its result. Replaying that history makes most providers
        reject the next request, so the gap is filled before every run.
        """
        repaired, changed = _repair_tool_history(self.session.messages)
        if changed:
            self.session.replace_messages(
                repaired,
                summary="Recovered an interrupted tool turn.",
                dropped_messages=0,
            )

    def refresh_instructions(self) -> None:
        """Drop the cached system prompt so the next build re-reads instructions.

        ``AGENTS.md`` / ``CLAUDE.md`` are read at the start of every run rather
        than once per process: ``/summary`` and external editors rewrite them
        between turns, and a stale copy would silently keep old rules.
        """
        self._system_prompt = None

    def _system_messages(self) -> list[dict[str, Any]]:
        if self._system_prompt is None:
            # Building the prompt reads SYSTEM.md plus every AGENTS.md/CLAUDE.md
            # on the path; missing files are skipped. The tool loop calls this
            # several times per run, so the result is cached until the next run.
            self._system_prompt = build_system_prompt(
                self.settings,
                self.session.cwd,
                self.shell,
            )
        return [{"role": "system", "content": self._system_prompt}]

    def _messages(self) -> list[dict[str, Any]]:
        return [*self._system_messages(), *self.session.messages]

    def _accumulate_usage(
        self,
        usage: dict[str, int],
        messages: list[dict[str, Any]],
        reply_text: str = "",
    ) -> None:
        """Record one turn's usage, estimating only what the provider omitted.

        A missing usage object is not the same as a turn that cost nothing: Chat
        servers that ignore ``stream_options`` report nothing at all, and
        printing ``0 completion`` for a real answer is a lie. Estimates are
        flagged so the CLI can mark them instead of presenting them as reported.
        """
        estimated = not usage
        if estimated:
            prompt = estimate_tokens(messages)
            completion = estimate_text_tokens(reply_text)
            normalized = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            }
        else:
            normalized = dict(usage)
            if reply_text and not int(normalized.get("completion_tokens", 0) or 0):
                prompt = int(normalized.get("prompt_tokens", 0) or 0)
                completion = estimate_text_tokens(reply_text)
                normalized["completion_tokens"] = completion
                normalized["total_tokens"] = max(
                    int(normalized.get("total_tokens", 0) or 0), prompt + completion
                )
                estimated = True
        for key in self.total_usage:
            self.total_usage[key] += int(normalized.get(key, 0))
        self.last_prompt_tokens = int(normalized.get("prompt_tokens", 0))
        if estimated:
            self.estimated_turns += 1
        self.session.append_usage(normalized)

    def _write_delta(self, text: str) -> None:
        if self._thinking_renderer is not None:
            # Close the reasoning block first, so the answer never continues on
            # the styled line the reasoning was written to.
            self._thinking_renderer.finish()
        if self.stream_output:
            if self._highlighter is not None:
                self._highlighter.feed(text)
            else:
                safe = self._plain_sanitizer.feed(text)
                if safe:
                    self.output.write(safe)
                    self.output.flush()

    def _write_thinking(self, text: str) -> None:
        """Render one reasoning delta in the six-level heading style."""
        if self._thinking_renderer is not None:
            self._thinking_renderer.feed(text)

    def _finish_delta(self) -> None:
        if self._thinking_renderer is not None:
            self._thinking_renderer.finish()
        if self._highlighter is not None:
            self._highlighter.finish()
        else:
            self._plain_sanitizer.finish()
            self._plain_sanitizer = TerminalSanitizer()

    def _activity(self, message: str) -> None:
        if not self.quiet:
            self.error_output.write(
                style_activity_tag(message, self.error_output) + "\n"
            )
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
        except (ToolError, OSError, TypeError, ValueError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a bad tool must not end the turn
            # Any unexpected failure inside a handler is still reported to the
            # model as a tool result; re-raising would abort the whole turn and
            # leave the user message without an answer.
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

    def run(self, user_text: str) -> AgentResult:
        if not user_text.strip():
            raise ValueError("user message is empty")
        # Read AGENTS.md / CLAUDE.md again for this run: instructions may have
        # changed on disk since the previous turn (including via /summary).
        self.refresh_instructions()
        self._repair_history()
        usage_before = dict(self.total_usage)
        estimated_before = self.estimated_turns
        self.session.append_message({"role": "user", "content": user_text})
        final_text = ""
        final_completed = False
        final_finish_reason: str | None = None
        turn = 0

        for turn in range(1, self.max_turns + 1):
            if self.should_compact():
                self._compact(force=False)
            messages = self._messages()
            try:
                reply = self.provider.stream_chat(
                    messages,
                    tools=self.tool_schemas,
                    on_text=self._write_delta,
                    on_thinking=self._write_thinking,
                )
            finally:
                self._finish_delta()
            self._accumulate_usage(reply.usage, messages, reply.content)
            assistant = reply.as_message()
            self.session.append_message(assistant)
            final_text = reply.content
            final_finish_reason = reply.finish_reason

            if not reply.tool_calls:
                final_completed = reply.finish_reason in {None, "stop", "completed"}
                if final_completed and not reply.content.strip():
                    # A finished turn with neither text nor a tool call is a
                    # provider-side failure; nothing was delivered to the user.
                    final_completed = False
                break

            if reply.finish_reason in _INCOMPLETE_FINISH_REASONS:
                for call in reply.tool_calls:
                    result = {
                        "status": "error",
                        "message": (
                            "tool call was not executed because the provider "
                            f"stopped with finish reason {reply.finish_reason!r}"
                        ),
                    }
                    self.session.append_message(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": tool_result_text(result),
                        }
                    )
                    self._activity(f"  {_tool_output_preview(result)}")
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
                if call.name in {"shell", "subagent"}:
                    self._activity(_shell_output_activity(result))
                else:
                    self._activity(f"  {_tool_output_preview(result)}")

        if not final_completed:
            self._report_incomplete(
                turn=turn,
                final_text=final_text,
                finish_reason=final_finish_reason,
            )
        return AgentResult(
            text=final_text,
            completed=final_completed,
            usage=dict(self.total_usage),
            run_usage={
                key: value - usage_before.get(key, 0)
                for key, value in self.total_usage.items()
            },
            turns=turn,
            usage_estimated=self.estimated_turns > estimated_before,
        )

    def _report_incomplete(
        self,
        *,
        turn: int,
        final_text: str,
        finish_reason: str | None,
    ) -> None:
        if self.quiet:
            return
        if turn >= self.max_turns:
            message = f"Reached max turns ({self.max_turns}); task may be incomplete."
        elif not final_text.strip():
            message = "The provider returned an empty response; the task is unfinished."
        else:
            # Never echo a whole answer to stderr while reporting the failure.
            preview = " ".join(final_text.split())
            if len(preview) > 200:
                preview = preview[:197] + "..."
            message = f"Provider stopped early: {preview}"
        if finish_reason and finish_reason not in {"stop", "completed", "tool_calls"}:
            message = f"{message} (finish reason: {finish_reason})"
        message = sanitize_terminal_text(message)
        self.error_output.write(message + "\n")
        self.error_output.flush()

    def should_compact(self) -> bool:
        threshold = float(self.settings.get("context.compact_at", 0.8))
        window = max(1, self.provider.context_window)
        # Re-serializing the whole history on every turn is O(n^2) over a long
        # session; the provider-reported prompt size is the better signal and is
        # only missing before the first reply or right after a compaction.
        current = self.last_prompt_tokens
        if current <= 0:
            current = estimate_tokens(self._messages())
        return current >= int(window * threshold)

    def _compact(self, *, force: bool) -> bool:
        keep = max(4, int(self.settings.get("context.keep_messages", 12)))
        messages = self.session.messages
        if len(messages) <= keep + 2:
            return False

        target = len(messages) - keep
        cut = _compaction_boundary(messages, target)
        if cut is None:
            if not force:
                return False
            raise RuntimeError("cannot compact: no safe message boundary found")

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

    def summarize(self) -> str:
        """Ask the provider to condense the whole session into durable notes."""
        summary = self.provider.complete_text(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": self._transcript(self._messages())},
            ]
        ).strip()
        if not summary:
            raise ProviderError("the provider returned an empty summary")
        return summary

    def write_summary(self) -> Path:
        """Write the session summary into the workspace ``AGENTS.md``.

        The path goes through the sandbox resolver like any tool path, so a
        symlinked or sensitive ``AGENTS.md`` is refused instead of overwritten.
        """
        target = self.tools.sandbox.resolve_path(
            "AGENTS.md",
            cwd=self.session.cwd,
            operation="write",
            allow_outside=self.tools.allow_outside,
        )
        existing: str | None = None
        if target.is_file():
            try:
                existing = target.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ToolError(f"{target} is not valid UTF-8 text") from exc
            except OSError as exc:
                raise ToolError(f"cannot read {target}: {exc}") from exc
        merged = merge_agents_summary(existing, self.summarize())
        self.tools.write(str(target), merged)
        return target

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
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if not isinstance(function, dict):
                        continue
                    lines.append(
                        f"TOOL_CALL {function.get('name', '')}: "
                        f"{function.get('arguments', '')}"
                    )
        text = "\n".join(lines)
        # Fit the transcript comfortably in the model window: roughly half the
        # token budget converted to characters, bounded so a 1M-token model
        # does not produce a multi-megabyte summarization request.
        max_chars = min(200_000, max(4_000, self.provider.context_window // 2))
        if len(text) > max_chars:
            head = int(max_chars * 0.25)
            tail = max_chars - head
            text = text[:head] + "\n...<middle omitted>...\n" + text[-tail:]
        return text

    def new_session(self, session: Session, tool_runtime: ToolRuntime) -> None:
        """Start a fresh conversation while keeping the active provider."""
        schemas = available_tool_schemas(
            tool_runtime.web_client,
            tool_runtime.vision_client,
            tool_runtime.subagent_enabled,
        )
        self.session = session
        self.tools = tool_runtime
        self.tool_schemas = schemas
        self.last_prompt_tokens = 0
        self.estimated_turns = 0
        self._system_prompt = None
        self.total_usage = dict.fromkeys(self.total_usage, 0)

    def switch_runtime(
        self,
        provider: OpenAICompatibleProvider,
        tool_runtime: ToolRuntime,
    ) -> None:
        """Adopt a new provider/model without discarding the session history.

        The caller owns the old provider and its optional web/vision clients and
        closes them after this returns. Tool declarations are rebuilt because a
        provider without ``web_tools`` or ``vision`` must stop advertising those
        tools, and the previous prompt size is dropped because it describes the
        old model's context window.
        """
        schemas = available_tool_schemas(
            tool_runtime.web_client,
            tool_runtime.vision_client,
            tool_runtime.subagent_enabled,
        )
        if hasattr(provider, "notify"):
            provider.notify = self._activity
        self.provider = provider
        self.tools = tool_runtime
        self.tool_schemas = schemas
        self.last_prompt_tokens = 0

    def status(self) -> dict[str, Any]:
        summary = getattr(self.tools, "sandbox_summary", None)
        return {
            "provider": self.provider.name,
            "model": self.provider.model,
            "effort": self.provider.thinking,
            "shell": self.shell.label,
            "session": str(self.session.path) if self.session.path else "<memory>",
            "messages": len(self.session.messages),
            "last_prompt_tokens": self.last_prompt_tokens,
            "context_window": self.provider.context_window,
            "usage": dict(self.total_usage),
            "usage_estimated": self.estimated_turns > 0,
            "sandbox": summary() if callable(summary) else {"enabled": False},
        }
