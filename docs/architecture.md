# Architecture

Applies to MinimalCode 1.8.0.

MinimalCode keeps one process and one HTTP connection pool per run. There is no daemon, UI framework, embedded database, or background service.

## Request flow

```text
CLI
  -> Settings + shell detection
  -> session load/create
  -> repository instructions re-read (AGENTS.md / CLAUDE.md)
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
| `config.py` | Settings merge, provider presets in `defaults.json`, validation, environment expansion, atomic config writes |
| `httpclient.py` | Client construction, proxy policy for loopback targets, bounded connection retries |
| `sandbox/` | Sandbox policy, path/network checks, pluggable backends, bounded execution, audit |
| `shell.py` | Shell detection, command prefixing, sandbox delegation, UTF-8 normalization |
| `prompt.py` | Concise default prompt, `AGENTS.md` discovery, marked summary block merge |
| `provider/` | OpenAI Chat/Responses and Anthropic Messages payloads, HTTP retries, and SSE parsing |
| `web.py` | Configurable provider web search and fetch client |
| `media.py` | Configurable provider OCR, speech, and document-parsing client |
| `vision.py` | Configurable provider vision client for inline image understanding |
| `clipboard.py` | Cross-platform clipboard image reader (PowerShell, osascript, wl-paste, xclip) |
| `tools/` | Tool schemas, workspace-bound file/search/process tools, and dispatch |
| `mcp/` | MCP client: JSON-RPC framing, stdio/HTTP/SSE transports, tool discovery, and the server manager |
| `subagent.py` | Child `minimalcode -p` command construction, depth tracking |
| `session.py` | Append-only JSONL history, conversation title event, project-scoped discovery, post-compaction rewrite |
| `reminder.py` | `latest_reminder` content: date, localized weekday, location, platform, language |
| `clawd.py` | Clawd on Desk bridge: prefs/runtime file discovery, lifecycle events, background delivery |
| `agent.py` | Tool loop, concurrent subagent and read-only dispatch, usage accounting, compaction, session summary, interrupted-turn repair, in-session provider switch |
| `highlight/` | Incremental Markdown rendering, code coloring, and ANSI styling |
| `cli.py` | Interactive and non-interactive command handling, `/model` runtime provider swap |
| `webui/` | Loopback chat page: settings writes, workspace switching, streamed turns, sidebar folders and conversation deletion |

## Package layout

0.7.0 split the four large modules into packages; 1.8.0 added `webui/` as the
fifth. Only the names listed in a package's `__all__` are public; the pre-split
module-level aliases were removed. Private helpers are imported from the
submodule that defines and uses them.

### `tools/`

| Module | Responsibility |
|---|---|
| `runtime.py` | `ToolRuntime`: shared settings, path resolver, dispatcher, allowlist gate, activity text |
| `schemas.py` | Provider-facing tool declarations, the canonical tool-name set, and enablement rules |
| `files.py` | `read`/`read_range`, `write`, `append_file`, atomic line-ending-preserving `edit`, post-write validation and rollback |
| `search.py` | Bounded `rg`/Python search processes, smart-case, output parsing |
| `search_tools.py` | `grep` and `find`, ignore globs, sandbox glob exclusions |
| `remote.py` | Provider-backed `read_image`, `web_search`, `web_fetch` |
| `media_tools.py` | Provider-backed `ocr`, `transcribe`, `speech`, and the `/attach` document upload |
| `validation.py` | Argument arity/type checks before a handler runs |

`ToolRuntime` is composed from the file, search, process, remote, and media
mixins, so every tool shares one sandbox, workspace, and configuration
instance. It also owns the `MCPManager`, so the discovered MCP tools follow the
same `tools.allow`, `--read-only`, and dispatch path as the built-in ones.

### `mcp/`

| Module | Responsibility |
|---|---|
| `protocol.py` | JSON-RPC 2.0 message builders/validators, content conversion, tool schema conversion and name normalisation |
| `transport.py` | `StdioTransport` (child process pipes), `HTTPTransport` (streamable HTTP), `SSETransport` (legacy HTTP+SSE), base environment for stdio servers |
| `client.py` | `MCPClient`: handshake, `tools/list` pagination, `tools/call`, server-request handling, per-server status |
| `manager.py` | `MCPManager`: start servers concurrently, failure isolation, namespaced tool index, allowlist/read-only helpers, refresh, shutdown |

A client serialises requests over one session and answers `ping` and
`roots/list` from inside the receive loop. Sampling and elicitation are refused
with `-32601`; the client has no second model call to offer.


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
| `anthropic.py` | Anthropic Messages payload, SSE stream, non-stream parsing |
| `anthropic_env.py` | Claude Code style `ANTHROPIC_*` environment provider for `--anthropic-env` |
| `deepseek_web.py` | DeepSeek web payload, JSON-patch SSE stream, proof of work, text tool protocol |
| `transport.py` | Request construction, connection retries, HTTP status retries |
| `common.py` | Usage/content/argument normalization shared by every protocol |
| `types.py` | `ToolCall` and `ProviderReply` |
| `factory.py` | `create_provider` and provider/model/effort resolution |

### `webui/`

| Module | Responsibility |
|---|---|
| `runtime.py` | `WebRuntime`: vendor/format/model/API-key writes, workspace switch, session list and reopen, thinking switch, one streamed turn |
| `server.py` | `ThreadingHTTPServer` routes, `Host`/`Origin` checks, SSE framing, directory listing for the folder picker |
| `assets/` | `index.html`, `app.css`, `app.js`; package data, no build step and no front-end dependency |

The page is a reproduction of the [deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo)
interface in plain HTML/CSS/JS: its design tokens (`--dsw-alias-*`,
`--dsw-specific-*`, light `body` and dark `body[data-ds-dark-theme]`), the 261px
sider with date-grouped sessions, the 840px message column, the 22px user
bubble, the 24px composer, and the 760×500 settings dialog are carried over
rather than reinvented. `app.js` renders assistant Markdown with a small
hand-written renderer (headings, lists, tables, fences, inline styles) so the
page needs no Markdown library.

The sider is a two-level tree: folders first, the conversations filed in each
folder beneath them, and an uncategorized section for the rest. `library.py`
owns the index (`<home>/webui/folders.json`, one bucket per workspace, written
through a temp file and `os.replace`) while `SessionStore.delete` unlinks only
files inside `sessions/`. A folder is a label, never a move, so the JSONL the
CLI resumes from is exactly the file the page wrote; deleting the conversation
that is currently open also resets the runtime, so no live agent appends to a
file that no longer exists.
Reasoning is display-only and never enters the JSONL transcript, so
`thinking.py` keeps one `<home>/webui/thinking.json` keyed by session id: each
finished turn's blocks are matched back to its assistant message by answer text,
newest entry first, so a compacted or hand-edited transcript loses old blocks
instead of borrowing someone else's. `GET /api/history` attaches them to the
assistant message and `app.js` rebuilds the same 思考 blocks (collapsed, title
clickable) instead of dropping them on a reload; deleting a conversation forgets
its entry, and one conversation keeps its newest 200 turns.
The sider toggle lives inside the sider, so collapsing it would hide the only
control that brings it back. The page therefore keeps its reopen affordance
outside: the
`mobile-menu` button that lives in `.main` (normally only shown by the narrow
media query for the drawer) is displayed by `.sider[hidden] ~ .main
.mobile-menu`, and `setSiderHidden()` is the single place that flips the state,
so the two buttons can never disagree. Under 768px that button restores the
sider before opening the drawer, because a sider left `hidden` by a wide window
would otherwise stay `display: none`. The drawer itself is `.sider.sider-open`
(`transform: translateX(0)`, nested in the narrow media query), which
`openDrawer()` adds and `closeDrawer()` removes.

Every `function` in `app.js` is declared at the top level, and
`test_page_script_declares_its_functions_at_top_level` enforces it by counting
brace depth with a scanner that tells a regex literal from a division sign: a
declaration nested by accident still parses, so `node --check` passes while
`bindEvents` throws on the undefined name and `boot()` dies before loading any
data — a blank page rather than a broken detail.

`WebAgent` subclasses `Agent` and overrides the display hooks
(`_begin_round`, `_write_delta`, `_write_thinking`, `_activity`,
`_provider_notice`), so the tool loop, compaction, and usage accounting are the
same code the terminal runs and no ANSI escape sequence can reach the browser.
`Agent._begin_round` is a no-op the terminal inherits; the web agent turns it
into a `round` event so the page renders one block per model request and can
keep 思考 → 正文 → 工具 in that order instead of merging every round into two
ever-growing areas. A settings write is a delta on the user's raw document, and
the live `Settings` mapping is updated in place
rather than reloaded, so `--sandbox-*` overrides given on the command line
survive a model switch in the same process. A provider/model switch rebuilds the
provider and tool runtime but keeps the loaded session (`Agent.switch_runtime`),
which is what `/model` does in the terminal; reopening a recorded session
instead adopts it and appends a `meta` event if the provider changed. The page
can reach a tool loop, so the server binds loopback by default and refuses a
request whose `Host` or `Origin` is not this server.
request whose `Host` or `Origin` is not this server.

The HTTP surface is deliberately small; every route below is loopback-only.

| Route | Method | Purpose |
|---|---|---|
| `/`, `/app.css`, `/app.js` | GET | Page assets read from `webui/assets/` |
| `/api/state` | GET | Providers, protocols, models, workspace, key presence |
| `/api/history` | GET | Current transcript with its reasoning attached |
| `/api/sessions` | GET | Conversations and the folder index for this workspace |
| `/api/browse?path=` | GET | Subdirectory names only, for the folder picker |
| `/api/chat` | POST | One streamed turn, framed as SSE events |
| `/api/settings` | POST | Delta write of provider, format, model, and key |
| `/api/session` | POST | Reopen a recorded conversation |
| `/api/session/delete` | POST | Delete one conversation |
| `/api/session/folder` | POST | Move a conversation into a folder |
| `/api/folder` | POST | Create a folder, or rename one when `id` is given |
| `/api/folder/delete` | POST | Delete a folder; its conversations return to uncategorized |
| `/api/thinking` | POST | Turn the reasoning request on or off |
| `/api/workspace` | POST | Switch the working directory |
| `/api/new` | POST | Start a new conversation |

`/api/chat` frames `start`, `round`, `delta`, `thinking`, `activity`, `notice`,
`done`, and `error`. While a turn is streaming, the mutating routes answer 409
(`Busy`) instead of swapping the runtime under the tool that is running.
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
- `minimalcode.webui` exports `serve` and `create_server`; `WebRuntime`,
  `WebAgent`, and `browse_directories` live in `minimalcode.webui.runtime`, and
  a test that stubs the model client patches
  `minimalcode.webui.runtime.create_provider`.

The 0.6 → 0.7 migration table is in [compatibility.md](compatibility.md).

## Performance choices

- One reusable `httpx2.Client` maintains the connection pool.
- All three HTTP clients are built by `httpclient.build_client`, so proxy rules and retry policy cannot drift between the protocol, web, and vision paths.
- Loopback endpoints ignore environment proxies; an injected transport is never shadowed by proxy mounts.
- Token estimates weight CJK and emoji characters at roughly one token each instead of `len / 4`, and estimated usage is marked as such.
- Tool schemas are static; the system prompt is built once per agent rather than once per turn.
- Adjacent read-only tool calls share one bounded thread group, so a turn that reads several files pays one round of latency instead of one per call; the dispatcher, not the schema list, is the enforcement point for `tools.allow` and `--read-only`. The in-process regex fallback skips its `SIGALRM` guard off the main thread (it already did for subagent workers); when the backend can spawn argv children, `grep` uses the timeout-bounded child process instead.
- `read` decodes strictly first, so a file that is valid UTF-8 is scanned once; only a non-UTF-8 range is read a second time to return the lossy content alongside a warning.
- `rg` is used when available; a timeout-bounded Python fallback keeps commands working without it.
- Shell and search output is captured through bounded head/tail buffers, so a noisy command cannot exhaust memory or disk.
- Tool output is truncated by head and tail, preserving both leading context and final errors.
- MCP servers start concurrently (at most four at a time), and an empty `mcp.servers` object starts no thread or process at all. A failed server costs one timeout window at startup and nothing afterwards, because its tools are simply absent from the advertised list.
- MCP results are converted to text before they reach the transcript: image and audio blocks report their MIME type and payload size instead of inlining base64, and the whole result is clipped by head and tail under `mcp.max_output_chars`.
- Untrusted model, tool, and error text is stripped of terminal control sequences before display.
- Interactive input is read line by line: bracketed paste keeps a pasted block in one message, buffered input (`msvcrt.kbhit` on Windows, `select` elsewhere) joins the lines of a terminal that does not support it, and a trailing `\` continues the message. Non-interactive prompts and piped stdin are untouched.
- Session writes are append-only, with one exception: a compaction that would leave the file more than twice the size of the state it keeps rewrites it atomically (temp file, `fsync`, `os.replace`), and configuration is otherwise the only atomically rewritten file.
- The renderer treats incomplete inline markers as active styles, emits text without waiting for a newline, and keeps redirected and JSON output as raw Markdown.
- The web, vision, and media clients are built by `httpclient.build_client`
  too, so all four provider-facing clients share one proxy and trust policy.
- One `ProviderMediaClient` serves OCR, speech recognition, speech synthesis,
  and document upload, so a session that mixes them keeps one connection pool.
- A document uploaded through `/attach` is cached by file name and content
  hash, so attaching the same file twice costs one hash instead of a second
  paid parse.
- The conversation title is requested once per session, not once per turn: the
  provider may hold the response open for up to five seconds while it generates
  the title.
- A `latest_reminder` turn is built per user message and never stored, so a
  session replayed days later does not claim the old date.
- Search results are ordered before they are deduplicated, so the copy kept for
  a mirrored URL is the best-scoring one rather than the first one returned.
- The web page is three static files read from package data once and cached in
  memory; SSE frames are written straight from the turn's own thread, so no
  queue sits between the agent and the socket and a closed tab costs one failed
  write instead of a stalled turn.
