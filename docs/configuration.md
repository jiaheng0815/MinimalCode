# Configuration reference

The default configuration root is `~/.MinimalCode`. Override it with `MINIMALCODE_HOME` or `--home`.

Built-in defaults are data, not Python literals: they live in
`minimalcode/defaults.json` from the installed package.

## Top-level settings

| Key | Type | Default | Meaning |
|---|---|---:|---|
| `provider` | string | `"deepseek"` | Default provider name |
| `model` | string | `"deepseek-flash"` | Default model ID |
| `thinking` | string | `"high"` | Reasoning effort passed only when enabled |
| `max_turns` | integer | `1000` | Maximum model/tool rounds per user task |

## Session

`session.save` controls automatic JSONL session storage. `--no-session` disables it for one invocation.

Sessions are grouped under `~/.MinimalCode/sessions/` by a deterministic workspace key. The JSONL file records messages, usage, and compaction boundaries.

## Shell

| Key | Default | Meaning |
|---|---:|---|
| `path` | `null` | Explicit shell executable; null enables auto-detection |
| `timeout_seconds` | `120` | Default and maximum command timeout; model-requested timeouts are capped at this value |
| `max_output_chars` | `60000` | Maximum stdout and stderr returned to the model |

## Tools

| Key | Default | Meaning |
|---|---:|---|
| `allow_outside_workspace` | `false` | Allow file tools outside the workspace |
| `max_file_chars` | `160000` | Maximum characters returned by one `read` call |
| `max_file_bytes` | `5000000` | Maximum size accepted by `write` and `edit` |
| `max_results` | `200` | Default `grep`/`find` result limit |

The `shell` tool always inherits the operating-system permissions of the current process.

`grep` and `find` skip ignored directories (`.git`, `node_modules`, `build`,
`dist`, caches, virtualenvs) in both the `ripgrep` path and the Python fallback.
Neither path parses `.gitignore`; install `ripgrep` if ignore-file semantics are
required. A configured `max_results` above a tool's hard cap (2000 for `grep`,
5000 for `find`) is clamped to that cap.

Shell commands write stdout and stderr to temporary files instead of pipes, so a
command that times out while a background child still holds its output returns
the partial output instead of blocking.

## Context

| Key | Default | Meaning |
|---|---:|---|
| `compact_at` | `0.8` | Compact when estimated context reaches this fraction |
| `keep_messages` | `12` | Minimum recent messages retained after compaction |
| `max_instruction_chars` | `60000` | Limit for `SYSTEM.md` and `AGENTS.md` content |

Compaction is lossy in the active context but the original JSONL history remains on disk until a later compaction record supersedes it for loading.

## Output

| Key | Default | Meaning |
|---|---:|---|
| `markdown_highlight` | `true` | Render streamed Markdown with ANSI styling on interactive terminals |
| `theme` | `"dark"` | Palette for the highlighter: `dark` or `light` |

Highlighting only applies when stdout is a terminal. `--json`, redirected
output, `NO_COLOR`, and `--no-highlight` all keep the raw Markdown.

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

Web tools are provider configuration, not runtime provider-name or hostname
checks. The built-in DeepSeek provider enables them with:

```json
"web_tools": {
  "enabled": true,
  "base_url": "https://api.deepseek.com/v1/tools",
  "search_path": "search",
  "fetch_path": "fetch",
  "timeout_seconds": 60,
  "default_count": 10,
  "max_count": 50,
  "max_content_chars": 120000,
  "api_key_env": "DEEPSEEK_API_KEY",
  "headers": {}
}
```

`api_key` or `api_key_env` inside `web_tools` override the provider credential.
When omitted, the provider API key is reused.

Search results are normalized before they reach the model: non-object entries are
dropped, at most `max_count` results are kept, and `content`/`text`/`snippet`
fields longer than `max_content_chars` are truncated with a
`<content truncated>` marker. `web_fetch` applies the same limit to page content.

`web_tools.base_url` and its credential are trusted provider configuration.
`web_fetch` validates that the requested target is HTTP(S), but the target URL
is fetched by the configured provider rather than directly by MinimalCode.
Only configure provider endpoints you trust, especially when they can reach
private networks.

Providers without an enabled `web_tools` block neither receive the two tool
schemas nor can invoke them through the local dispatcher.

Environment placeholders such as `${CUSTOM_PROVIDER_KEY}` are expanded when
`Settings.provider_config()` resolves the active provider. A placeholder that
stays unresolved raises a configuration error that names the variable, instead
of being sent to the provider as literal text. Other providers stay lazy: their
placeholders are only checked when they become active.

## Vision

`read_image` is provider configuration, like `web_tools`. A `vision` block
enables it and points at an OpenAI-compatible chat endpoint:

```json
"vision": {
  "enabled": true,
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-flash",
  "timeout_seconds": 120,
  "max_image_bytes": 33554432,
  "max_chars": 8000,
  "detail": "auto",
  "api_key_env": "DEEPSEEK_API_KEY",
  "headers": {}
}
```

| Key | Default | Meaning |
|---|---:|---|
| `enabled` | `false` | Expose `read_image` for this provider |
| `base_url` | provider `base_url` | Chat endpoint root; a full `/chat/completions` URL is also accepted |
| `model` | active model | Vision-capable model ID |
| `timeout_seconds` | `120` | HTTP timeout for one image request |
| `max_image_bytes` | `33554432` | Largest image accepted before base64 encoding |
| `max_chars` | `8000` | Maximum description characters returned to the model |
| `detail` | `"auto"` | `low`, `high`, `original`, or `auto` |
| `api_key_env` | provider credential | Optional override for the bearer token |

Images are sent inline as base64 data URLs, so `max_image_bytes` must stay
below the provider's request-body limit (DeepSeek caps base64 requests at
48 MiB and single images at 32 MiB). Formats are detected from file content;
JPEG, PNG, GIF, and WebP are accepted. Images are read through the same
workspace check as `read`, so files outside the workspace require
`tools.allow_outside_workspace: true`.

