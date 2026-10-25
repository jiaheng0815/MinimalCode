# Changelog

## Unreleased

### Added

- Added the `read_image` tool: local JPEG/PNG/GIF/WebP images are sent to the provider's vision model and returned as text descriptions or answers. It is enabled by the provider's `vision` block, defaults on for DeepSeek, and stays inside the workspace.
- Increased the default per-task model/tool loop budget from 50 to 1000 turns.
- Added streaming Markdown and README rendering for interactive terminals.
- Unterminated bold, italic, strikethrough, inline code, and link text render immediately, following Streamdown/remend-style incomplete-block handling.
- Links render as `label (url)` so the target stays clickable in a terminal.
- Added dark/light palettes, `--no-highlight`, and `NO_COLOR` support while keeping JSON and redirected output plain.
- Shell tool activity now displays the command exit code, stdout, and stderr unless tools are quiet or JSON output is active.
- Tool activity tags such as `[shell]` and `[read]` are green on ANSI-capable terminals.
- Thematic breaks (`---`, `***`, `___`) are highlighted instead of being swallowed as emphasis markers.

### Changed

- Shell tool activity clips stdout/stderr to the first and last few lines so long commands no longer flood the terminal; the model still receives the full output.
- `grep` and `find` now skip the same ignored directories (`.git`, `node_modules`, `build`, `dist`, caches, virtualenvs) with or without `ripgrep`.
- An unset `${VAR}` placeholder in the active provider now raises a configuration error naming the variable instead of reaching the provider as literal text.
- `web_fetch` accepts a `null` title from the provider and rejects only non-string titles.
- Shell commands capture output through temporary files instead of pipes, so a timed-out command with a live background child returns partial output instead of blocking.
- The system prompt is built once per agent instead of once per model turn, so `SYSTEM.md`/`AGENTS.md` are not re-read every round trip.
- Web search results are now normalized: non-object entries are dropped, the count is capped by `max_count`, and long `content`/`text`/`snippet` fields are truncated by `max_content_chars`.
- `write` enforces `tools.max_file_bytes` instead of an unrelated `max_file_chars * 8` heuristic.
- `config show` no longer redacts token budgets and counters such as `max_tokens`, `prompt_tokens`, or `tokenizer`, while `access_token`-style keys stay hidden.
- A configured `max_results` above the tool cap is clamped instead of silently ignored for the default limit.
- `import-pi` works when Pi has `models.json` but no `settings.json`.

### Fixed

- Fixed `read` returning empty content when a single line exceeds `max_file_chars`; the oversized line's prefix is now returned with `truncated`.
- Fixed `edit` rewriting line endings across the whole file and failing on CRLF files when `old_text` used LF; only the matched span is rewritten.
- Fixed Markdown rendering stalling on deeply indented lines: each delta now finishes the scan where the previous one stopped instead of rescanning the line prefix.
- `write` and `edit` preserve the existing file mode instead of replacing it with `0600`; new files are still created with `0600`.
- Invalid tool arguments are detected before dispatch, so handler `TypeError`s are no longer reported as argument errors.
- `/new` keeps `--no-highlight` and non-streaming output settings.
- Shell timeouts requested by the model are capped by `shell.timeout_seconds`.
- Web fetch responses reject non-string content and unset provider environment placeholders fail with the variable name.
- Corrupt or non-object session headers no longer raise `AttributeError`; they are skipped by `list()` and resolved by filename.
- Full session IDs now resolve against recorded session metadata instead of only the shortened filename.
- A settings file that exists but cannot be read now raises a configuration error instead of silently falling back to the built-in defaults.
- An empty model reply is reported as an incomplete task instead of a successful empty answer, and early-stop diagnostics no longer dump the full answer to stderr.
- A failed turn no longer terminates the interactive session; provider and I/O errors are reported and the prompt continues.
- Markdown rendering no longer drops a trailing backslash at the end of the answer.
- `##` and `###` headings are no longer styled as headings of a lower level when the `#` marker is split across stream deltas.
- Session files are created with private permissions from the first write instead of being tightened afterwards, and a failed usage write no longer discards the model's reply.
- `sessions` no longer crashes on metadata whose `session_id` is not a string.
- Provider and web-tool numeric settings reject non-finite values (`Infinity`) instead of raising `OverflowError`.
- Responses requests no longer send empty `role`/`content` items that strict servers reject.
- `grep` and `find` agree on recursive globs such as `**/*.py` in the Python fallback, and the fallback skips files larger than `tools.max_file_bytes`.

## 0.3.0 - 2026-10-24

- Removed provider host checks and hardcoded web endpoints from runtime code.
- Web tool capability, endpoints, timeout, result limits, content limits, key source, and headers are now provider configuration.
- Moved the complete default configuration out of Python into packaged `defaults.json`.
- DeepSeek keeps web tools enabled by default; all other providers remain disabled unless explicitly configured.

## 0.2.0 - 2026-10-24

- Added DeepSeek-hosted web search and web fetch tools.
- Web tools are exposed only for the official DeepSeek API and are blocked for all other providers.
- Added provider-aware tool schemas and web API regression tests.

## 0.1.0

- Introduced the `MinimalCode` package and `minimalcode` command.
- Added PowerShell 7, Windows PowerShell 5.1, bash, and sh shell fallback.
- Added read, write, edit, shell, grep, and find tools.
- Added OpenAI Chat Completions and Responses compatible streaming provider support.
- Added one-command migration of compatible Pi providers and default models.
- Added automatic JSONL sessions, continuation, usage tracking, and context compaction.
- Added `AGENTS.md` discovery, workspace boundaries, JSON mode, and focused tests.
