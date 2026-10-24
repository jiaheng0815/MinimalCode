# Changelog

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
