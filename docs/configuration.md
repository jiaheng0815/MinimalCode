# Configuration reference

Applies to MinimalCode 1.0.0.

The default configuration root is `~/.MinimalCode`. Override it with `MINIMALCODE_HOME` or `--home`.

Built-in defaults are data, not Python literals: they live in
`minimalcode/defaults.json` from the installed package.

## Top-level settings

| Key | Type | Default | Meaning |
|---|---|---:|---|
| `provider` | string | `"deepseek"` | Default provider name |
| `model` | string | `"deepseek-flash"` | Default model ID |
| `thinking` | string | `"high"` | Reasoning effort: `none` (disabled), `minimal`, `low`, `medium`, `high`, `xhigh`, `ultra`, or `max`; other values are rejected at startup |
| `max_turns` | integer | `1000` | Maximum model/tool rounds per user task |

## Session

`session.save` controls automatic JSONL session storage. `--no-session` disables it for one invocation.

Sessions are grouped under `~/.MinimalCode/sessions/` by a deterministic workspace key. The JSONL file records messages, usage, and compaction boundaries.

`--session <path>` only accepts files ending in `.jsonl`; any other file is rejected before a message is appended instead of having JSONL written into it. A workspace recorded in a session's `meta` event is used only when the file lives under the sessions directory, so an arbitrary JSONL cannot point every tool outside the workspace.

Usage written to the session may be estimated. When a provider omits the usage object, the agent derives token counts from character width (CJK and emoji count as about one token per character, other text as four characters per token) and marks the summary `(estimated)`; `--json` exposes the same fact as `usage_estimated`.

## Shell

| Key | Default | Meaning |
|---|---:|---|
| `path` | `null` | Explicit shell executable; null enables auto-detection |
| `timeout_seconds` | `120` | Default and maximum command timeout; model-requested timeouts are capped at this value |
| `max_output_chars` | `60000` | Maximum stdout and stderr returned to the model |

Auto-detection probes each candidate (`pwsh`, then `bash`/`sh`, or
`powershell` on Windows) with a trivial command before accepting it, so an
installed shell that cannot start — a pwsh build without libicu, a .NET runtime
that cannot reserve memory, a wrong-architecture binary — falls through to the
next one instead of failing every command. The probe costs one shell start
(roughly 0.2 s for pwsh) per process and is cached per executable. A
`shell.path` override is probed too and reports the failure instead of silently
switching shells. The fallback reason is printed by `minimalcode shell`.

## Tools

| Key | Default | Meaning |
|---|---:|---|
| `allow_outside_workspace` | `false` | Allow file tools outside the workspace |
| `max_file_chars` | `160000` | Maximum characters returned by one `read` call |
| `max_file_bytes` | `5000000` | Maximum size accepted by `write` and `edit` |
| `max_results` | `200` | Default `grep`/`find` result limit |

The `shell` tool runs through `SandboxRunner`. With the execution layer disabled
(the default) commands run with the current user's permissions; enabling the
layer adds policy, timeout, output, and resource controls.

`grep` and `find` skip ignored directories (`.git`, `node_modules`, `build`,
`dist`, caches, virtualenvs) in both the `ripgrep` path and the Python fallback.
The Python fallback uses ripgrep-compatible smart-case matching, runs the regex
in a timeout-bounded child process, and truncates long result lines. An explicit
`path` pointing at an ignored directory is still searched. Neither path parses
`.gitignore`; install `ripgrep` if ignore-file semantics are required. A
configured `max_results` above a tool's hard cap (2000 for `grep`, 5000 for
`find`) is clamped to that cap.

Shell commands stream stdout and stderr through bounded in-memory captures instead
of unbounded temporary files, so a command that times out while a background
child still holds its output returns the partial output without blocking or
filling the disk. A timeout kills the whole process tree, and Ctrl-C follows the
same cleanup path.

## Sandbox

Every tool that resolves a path or spawns a process goes through the single
`SandboxRunner` entry point in `minimalcode/sandbox/`. The execution layer is
off by default (`--no-sandbox`), so only the filesystem policy applies out of the
box; enable it with `--sandbox` or `sandbox.enabled: true`. When enabled, the
layer is fail-closed: a path outside the allowed roots, a credential-like file,
a denied network command, or an unavailable backend is refused instead of
quietly falling back to unsandboxed execution. Denials are recorded with their
reason in `SandboxAudit`, surfaced in tool errors and `/status`, and summarized by
`minimalcode sandbox` together with backend availability.

| Key | Default | Meaning |
|---|---|---:|
| `enabled` | `false` | Enforce network, timeout, output, and resource policy (the path policy always applies) |
| `backend` | `"local"` | `auto`, `local`, `bwrap`, `firejail`, `sandbox-exec`, or `docker` |
| `allowed_roots` | `[]` | Extra readable/writable roots; relative entries resolve against the workspace. The workspace is always allowed |
| `network_policy` | `"deny"` | `deny`, `allowlist`, or `allow` |
| `allowed_hosts` | `[]` | Hosts (or parent domains) accepted under `allowlist` |
| `timeout_seconds` | `120` | Hard cap for one command, on top of `shell.timeout_seconds` |
| `max_output_chars` | `60000` | Hard cap for one shell command's stdout/stderr |
| `allow_sensitive_files` | `false` | Permit `.env`, private keys, `.ssh/`, `.aws/`, and similar paths |
| `max_memory_mb` | `0` | Linux `RLIMIT_AS` for child processes; `0` disables. Opt-in because address-space caps break .NET/JVM/Go children |
| `max_processes` | `0` | Linux `RLIMIT_NPROC`; `0` disables |
| `docker_image` | `"alpine:3"` | Image used by the `docker` backend |

`allowed_roots` and sensitive-file checks apply to `read`, `write`, `edit`,
`grep`, `find`, `read_image`, and to `shell`/`subagent` working directories and
their resolved symlinks, whether or not the execution layer is enabled. Symlinks
are resolved before the check, so a link that points outside the workspace is
refused. Sensitive names are blocked in both
directions and excluded from bulk searches; `.env.example` and similar templates
remain readable.

`network_policy`, the command timeout/output caps, and the resource limits only
apply while the execution layer is enabled:

- `deny` (default): filesystem/namespace backends (`bwrap`, `firejail`,
  `sandbox-exec`, `docker`) remove network access. The `local` baseline has no
  network namespace, so it rejects commands that look like network clients
  (`curl`, `wget`, `ssh`, `git clone`, `pip install`, package managers, ...).
- `allowlist`: named hosts are checked against `allowed_hosts`; commands whose
  target host cannot be determined are refused. At least one host is required.
- `allow`: no network restriction.

Backends are probed at execution time, which only happens when the layer is
enabled. `auto` picks the first available of `bwrap`, `firejail`,
`sandbox-exec`, then `local`; the default is `local`. Selecting a backend that is
not installed refuses to run the command (it never silently downgrades). Path and
sensitive-file policy is enforced in-process, so file tools stay usable even when
the execution layer is off or a command backend is unavailable; only command
execution is gated by the backend.
`bwrap` isolates namespaces and mounts the workspace/allowed roots read-write
with the rest of the filesystem read-only. `firejail` adds a private temp
directory and `--net=none` for `deny`. `sandbox-exec` uses a profile that denies
network traffic for `deny`. `docker` runs `sh -lc` in the configured image with
the workspace mounted and no host environment; it refuses argv-based search
children, so `grep`/`find` fall back to the in-process Python scanner. The
`docker` backend is POSIX-host only (Docker Desktop cannot consume the
`C:\ws:C:\ws` mount this backend builds) and runs the container as the invoking
`uid:gid`, so workspace files do not end up owned by root.

The local baseline enforces the timeout and output caps on every platform,
kills the process tree on timeout, and runs children with a private temporary
directory (`TMPDIR`/`TMP`/`TEMP`) that is removed afterwards. On POSIX it also
applies `RLIMIT_CORE` and `RLIMIT_CPU`; `RLIMIT_AS` (`max_memory_mb`) and
`RLIMIT_NPROC` (`max_processes`) are Linux-only and opt-in. `RLIMIT_AS` is off by
default because it limits reserved address space: .NET (pwsh), JVM, and Go
children reserve gigabytes of virtual memory and abort with "Out of memory"
under a 2 GB cap even when their real usage is small.

CLI overrides (all optional):

```text
--sandbox / --no-sandbox      (the default is --no-sandbox)
--sandbox-backend auto|local|bwrap|firejail|sandbox-exec|docker
--sandbox-root PATH          (repeatable)
--sandbox-network deny|allowlist|allow
--sandbox-allow-host HOST    (repeatable)
--sandbox-timeout SECONDS
--sandbox-max-output CHARS
--sandbox-allow-sensitive / --no-sandbox-allow-sensitive
```

Any `--sandbox-*` tuning flag also enables the layer, so `--sandbox-timeout 30`
is not a silent no-op; an explicit `--no-sandbox` still wins.

`tools.allow_outside_workspace` remains an explicit legacy escape hatch: when
set, it bypasses the root confinement but still applies sensitive-file and
network policy. The execution layer being off does not change path handling: the
root confinement and the sensitive-file filter always stay in force, because a
toggle meant for command wrapping must not silently unlock every file tool.

Known limits: the `local` baseline is a policy and resource layer, not an
OS-level filesystem jail, and its network gate is a command-string heuristic
rather than kernel enforcement. `shell` still inherits the user's environment
(including credentials) and the user's OS permissions. Windows has no rlimit
equivalent in this baseline, and `RLIMIT_*` applies to the direct child and its
descendants only where the platform supports it. Path checks are performed
before opening a file, so a local attacker who can modify the tree concurrently
can still win a TOCTOU race. Docker backend availability depends on a working
Docker daemon and a locally pullable image. `auto` and explicit OS backends use
binary presence as the availability signal; a binary that exists but cannot
create its sandbox (for example, unprivileged user namespaces are disabled)
reports the execution failure instead of falling back to `local`.

## Subagents

`subagent` delegates a self-contained task (code review, broad analysis,
independent verification) to a fresh `minimalcode -p` process. There is no new
protocol: the child is spawned through the configured shell.

| Key | Default | Meaning |
|---|---:|---|
| `enabled` | `true` | Offer the `subagent` tool at all |
| `max_depth` | `2` | Maximum nesting level; a child at this depth no longer receives the tool |
| `max_turns` | `40` | Default and maximum model/tool rounds for one child |
| `timeout_seconds` | `900` | Default and maximum runtime for one child, further capped by `sandbox.timeout_seconds` while the execution layer is enabled |

The child inherits the parent's `--home`, provider, model, reasoning effort, and
workspace. The task text is piped through stdin, so it never appears on the
command line. The child runs with `-p --no-session --quiet`: it prints only its
final answer and leaves no session files behind. Because a child is one command,
its runtime is also bounded by `sandbox.timeout_seconds` while the execution layer
is enabled: when the subagent budget is larger, the tool clamps to the sandbox cap
and records the reason in the sandbox notes.

## Context

| Key | Default | Meaning |
|---|---:|---|
| `compact_at` | `0.8` | Compact when estimated context reaches this fraction |
| `keep_messages` | `12` | Minimum recent messages retained after compaction |
| `max_instruction_chars` | `60000` | Combined budget for `SYSTEM.md`, global `AGENTS.md`, and project instruction files |

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
| `send_reasoning_effort` | no | Send reasoning effort or the `none` thinking switch, default `false` |
| `headers` | no | Additional HTTP headers |
| `extra_body` | no | Additional request fields such as `temperature` |
| `trust_env` | no | Apply environment/system proxies; loopback `base_url` values default to `false` |

Provider entries are validated when settings load: `protocol`, `base_url`,
numeric limits, booleans, and object fields are checked before the first request.
Values containing `${VAR}` placeholders are expanded later, so they are checked
again when that provider becomes active.

Reserved request fields (`model`, `messages`, `stream`, `tools`, `tool_choice`) cannot be overridden through `extra_body`.

Requests that fail before a response is received (`ConnectError`, `ConnectTimeout`,
and `RemoteProtocolError` such as `Server disconnected without sending a
response.`) are retried 10 times with an exponential backoff from 0.5 s up to
8 s. A stream that already delivered data is not retried, so a partial answer is
never printed twice or billed twice; HTTP 429/503 keeps its single `Retry-After`
retry. Retry notices go to the tool activity stream, so `--quiet` and `--json`
stay silent.

Proxies: environment and system proxy settings apply to remote `base_url`
values. A loopback `base_url` (`127.0.0.1`, `::1`, `localhost`) ignores them
unconditionally, because routing a local provider through a proxy always fails —
a stopped Clash/corporate proxy answers 502 and a running one has no route back
to the caller's loopback interface. Set `trust_env` explicitly (per provider,
`web_tools`, or `vision`) when a target needs the opposite behaviour. The web
and vision clients share the same rules and the same retry budget.

For `openai_responses`, `include_usage` is not needed because usage is returned in the terminal response event. `send_reasoning_effort` sends `{"reasoning":{"effort": ...}}` (`none` disables thinking); reserved request fields are `model`, `input`, `stream`, `tools`, and `tool_choice`. For `openai_chat`, it sends `reasoning_effort`; `none` instead sends `{"thinking":{"type":"disabled"}}`.

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

Web requests that fail before receiving a response (`ConnectError`,
`ConnectTimeout`, and `RemoteProtocolError`) are retried with capped
exponential backoff. The configured `timeout_seconds` bounds the whole request,
including retries.

Search results are normalized before they reach the model: non-object entries are
dropped, at most the requested count (capped by `max_count`) results are kept,
and every string field is truncated to `max_content_chars` with a
`<content truncated>` marker. `web_fetch` applies the same limit to page content
and the title.

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

## See also

- [architecture.md](architecture.md): request flow, package layout, and which
  module implements each setting.
- [compatibility.md](compatibility.md): 0.6 → 0.7 import and patch-target
  migration for code that embeds MinimalCode.

