# Changelog

## Unreleased

### Added

- Increased the default per-task model/tool loop budget from 50 to 1000 turns.
- Added streaming Markdown and README rendering for interactive terminals.
- Unterminated bold, italic, strikethrough, inline code, and link text render immediately, following Streamdown/remend-style incomplete-block handling.
- Links render as `label (url)` so the target stays clickable in a terminal.
- Added dark/light palettes, `--no-highlight`, and `NO_COLOR` support while keeping JSON and redirected output plain.
- Shell tool activity now displays the command exit code, stdout, and stderr unless tools are quiet or JSON output is active.
- Tool activity tags such as `[shell]` and `[read]` are green on ANSI-capable terminals.

### Changed

- Shell tool activity clips stdout/stderr to the first and last few lines so long commands no longer flood the terminal; the model still receives the full output.
- `grep` and `find` now skip the same ignored directories (`.git`, `node_modules`, `build`, `dist`, caches, virtualenvs) with or without `ripgrep`.
- An unset `${VAR}` placeholder in the active provider now raises a configuration error naming the variable instead of reaching the provider as literal text.
- `web_fetch` accepts a `null` title from the provider and rejects only non-string titles.

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
