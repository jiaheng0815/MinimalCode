# Configuration reference

The default configuration root is `~/.MinimalCode`. Override it with `MINIMALCODE_HOME` or `--home`.

## Top-level settings

| Key | Type | Default | Meaning |
|---|---|---:|---|
| `provider` | string | `"deepseek"` | Default provider name |
| `model` | string | `"deepseek-flash"` | Default model ID |
| `thinking` | string | `"high"` | Reasoning effort passed only when enabled |
| `max_turns` | integer | `50` | Maximum model/tool rounds per user task |

## Session

`session.save` controls automatic JSONL session storage. `--no-session` disables it for one invocation.

Sessions are grouped under `~/.MinimalCode/sessions/` by a deterministic workspace key. The JSONL file records messages, usage, and compaction boundaries.

## Shell

| Key | Default | Meaning |
|---|---:|---|
| `path` | `null` | Explicit shell executable; null enables auto-detection |
| `timeout_seconds` | `120` | Default command timeout |
| `max_output_chars` | `60000` | Maximum stdout and stderr returned to the model |

## Tools

| Key | Default | Meaning |
|---|---:|---|
| `allow_outside_workspace` | `false` | Allow file tools outside the workspace |
| `max_file_chars` | `160000` | Maximum characters returned by one `read` call |
| `max_file_bytes` | `5000000` | Maximum full-file read/edit size |
| `max_results` | `200` | Default `grep`/`find` result limit |

The `shell` tool always inherits the operating-system permissions of the current process.

## Context

| Key | Default | Meaning |
|---|---:|---|
| `compact_at` | `0.8` | Compact when estimated context reaches this fraction |
| `keep_messages` | `12` | Minimum recent messages retained after compaction |
| `max_instruction_chars` | `60000` | Limit for `SYSTEM.md` and `AGENTS.md` content |

Compaction is lossy in the active context but the original JSONL history remains on disk until a later compaction record supersedes it for loading.

## Provider fields

| Key | Required | Meaning |
|---|---|---|
| `protocol` | yes | `"openai_chat"` or `"openai_responses"` |
| `base_url` | yes | Base URL up to `/v1`, or the complete chat endpoint |
| `model` | no | Provider-specific default model; overrides the top-level model for this provider |
| `api_key_env` | no | Environment variable containing the bearer token |
| `api_key` | no | Inline bearer token; prefer an environment variable |
| `require_api_key` | no | Defaults to true when `api_key_env` is set |
| `context_window` | no | Model context size, default `128000` |
| `timeout_seconds` | no | HTTP read timeout, default `120` |
| `stream` | no | SSE streaming, default `true` |
| `include_usage` | no | Send `stream_options`, default `true` |
| `send_reasoning_effort` | no | Send top-level `reasoning_effort`, default `false` |
| `headers` | no | Additional HTTP headers |
| `extra_body` | no | Additional request fields such as `temperature` |

Reserved request fields (`model`, `messages`, `stream`, `tools`, `tool_choice`) cannot be overridden through `extra_body`.

For `openai_responses`, `include_usage` is not needed because usage is returned in the terminal response event. `send_reasoning_effort` sends `{"reasoning":{"effort": ...}}`; reserved request fields are `model`, `input`, `stream`, `tools`, and `tool_choice`.

## Web tools

`web_search` and `web_fetch` are enabled only when the provider `base_url`
uses the official `https://api.deepseek.com` host. They call DeepSeek's
`/v1/tools/search` and `/v1/tools/fetch` endpoints with the same API key.

Other providers neither receive the two tool schemas nor can invoke them through
the local tool dispatcher.
