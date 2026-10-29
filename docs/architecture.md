# Architecture

Applies to MinimalCode 1.0.0.

MinimalCode keeps one process and one HTTP connection pool per run. There is no daemon, UI framework, embedded database, or background service.

## Request flow

```text
CLI
  -> Settings + shell detection
  -> session load/create
  -> provider stream request
  -> assistant message or tool calls
  -> focused tool execution
  -> tool result appended to JSONL
  -> next model turn
  -> final text / JSON
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Settings merge, validation, environment expansion, atomic config writes |
| `httpclient.py` | Client construction, proxy policy for loopback targets, bounded connection retries |
| `sandbox/` | Sandbox policy, path/network checks, pluggable backends, bounded execution, audit |
| `shell.py` | Shell detection, command prefixing, sandbox delegation, UTF-8 normalization |
| `prompt.py` | Concise default prompt and `AGENTS.md` discovery |
| `provider/` | OpenAI Chat/Responses payloads, HTTP retries, and SSE parsing |
| `web.py` | Configurable provider web search and fetch client |
| `vision.py` | Configurable provider vision client for inline image understanding |
| `tools/` | Tool schemas, workspace-bound file/search/process tools, and dispatch |
| `subagent.py` | Child `minimalcode -p` command construction, depth tracking |
| `session.py` | Append-only JSONL history and project-scoped discovery |
| `agent.py` | Tool loop, usage accounting, compaction, interrupted-turn repair |
| `highlight/` | Incremental Markdown rendering, code coloring, and ANSI styling |
| `cli.py` | Interactive and non-interactive command handling |

## Package layout

0.7.0 split the four large modules into packages. Only the names listed in a
package's `__all__` are public; the pre-split module-level aliases were removed.
Private helpers are imported from the submodule that defines and uses them.

### `tools/`

| Module | Responsibility |
|---|---|
| `runtime.py` | `ToolRuntime`: shared settings, path resolver, dispatcher, activity text |
| `schemas.py` | Provider-facing tool declarations and enablement rules |
| `files.py` | `read`, `write`, atomic write, line-ending-preserving `edit` |
| `search.py` | Bounded `rg`/Python search processes, smart-case, output parsing |
| `search_tools.py` | `grep` and `find`, ignore globs, sandbox glob exclusions |
| `process.py` | `shell` and `subagent` execution through the runner |
| `remote.py` | Provider-backed `read_image`, `web_search`, `web_fetch` |
| `validation.py` | Argument arity/type checks before a handler runs |

`ToolRuntime` is composed from the file, search, process, and remote mixins, so
every tool shares one sandbox, workspace, and configuration instance.

### `sandbox/`

| Module | Responsibility |
|---|---|
| `runner.py` | `SandboxRunner`: path resolution, policy checks, command execution |
| `policy.py` | Immutable `SandboxPolicy`, settings validation, network denial |
| `sensitive.py` | Credential-like path rules and search exclusion globs |
| `network.py` | Command-string network detection and host allowlisting |
| `backends.py` | `local`, `bwrap`, `firejail`, `sandbox-exec`, `docker`, selection |
| `execution.py` | Bounded stdout/stderr capture, timeout tree kill, rlimits |
| `audit.py` | Bounded denial/note trail surfaced in `/status` and `sandbox` |

### `provider/`

| Module | Responsibility |
|---|---|
| `client.py` | `OpenAICompatibleProvider`: configuration, HTTP client, dispatch |
| `chat.py` | Chat Completions payload, SSE stream, non-stream parsing |
| `responses.py` | Responses payload, event stream, non-stream parsing |
| `transport.py` | Request construction, connection retries, HTTP status retries |
| `common.py` | Usage/content/argument normalization shared by both protocols |
| `types.py` | `ToolCall` and `ProviderReply` |
| `factory.py` | `create_provider` and provider/model/effort resolution |

### `highlight/`

| Module | Responsibility |
|---|---|
| `markdown.py` | Incremental Markdown state machine over streamed deltas |
| `thinking.py` | Six-level heading styling for streamed reasoning text |
| `code.py` | Fenced-code tokenizer and keyword/number/string styling |
| `sanitize.py` | Terminal escape/control sequence removal across deltas |
| `color.py` | ANSI capability detection and Windows console enabling |
| `palette.py` | Dark/light palettes, `strip_ansi`, style wrapper |

## Public surface and patch targets

- Import public objects from the package facade (`minimalcode.tools`,
  `minimalcode.sandbox`, `minimalcode.provider`, `minimalcode.highlight`).
- Import helpers from the submodule that owns them, for example
  `minimalcode.tools.search.run_python_grep` or
  `minimalcode.sandbox.execution._posix_limits`.
- `unittest.mock.patch` must target the module whose global the implementation
  looks up at call time. For example, patch
  `minimalcode.provider.transport.time.sleep`, not
  `minimalcode.provider.time.sleep`.

The 0.6 → 0.7 migration table is in [compatibility.md](compatibility.md).

## Performance choices

- One reusable `httpx2.Client` maintains the connection pool.
- All three HTTP clients are built by `httpclient.build_client`, so proxy rules and retry policy cannot drift between the protocol, web, and vision paths.
- Loopback endpoints ignore environment proxies; an injected transport is never shadowed by proxy mounts.
- Token estimates weight CJK and emoji characters at roughly one token each instead of `len / 4`, and estimated usage is marked as such.
- Tool schemas are static; the system prompt is built once per agent rather than once per turn.
- `rg` is used when available; a timeout-bounded Python fallback keeps commands working without it.
- Shell and search output is captured through bounded head/tail buffers, so a noisy command cannot exhaust memory or disk.
- Tool output is truncated by head and tail, preserving both leading context and final errors.
- Untrusted model, tool, and error text is stripped of terminal control sequences before display.
- Session writes are append-only; configuration is the only atomically rewritten file.
- The renderer treats incomplete inline markers as active styles, emits text without waiting for a newline, and keeps redirected and JSON output as raw Markdown.
