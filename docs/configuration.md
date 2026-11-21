# Configuration reference

Applies to MinimalCode 1.10.1.

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

Compaction appends a `compact` event that carries the whole replacement history,
so appending one to a file that already holds that history stores the same
messages twice. When the file is at least twice the size of the state being kept
and at least 64 KiB, it is rewritten atomically (temp file, `fsync`,
`os.replace`) to `meta` plus that one event; a crash leaves the append-only file
untouched, and a size mismatch caused by another writer skips the rewrite. Small
sessions stay append-only.

`--session <path>` only accepts files ending in `.jsonl`; any other file is rejected before a message is appended instead of having JSONL written into it. A workspace recorded in a session's `meta` event is used only when the file lives under the sessions directory, so an arbitrary JSONL cannot point every tool outside the workspace.

Usage written to the session may be estimated. When a provider omits the usage object, the agent derives token counts from character width (CJK and emoji count as about one token per character, other text as four characters per token) and marks the summary `(estimated)`; `--json` exposes the same fact as `usage_estimated`. Compaction and `/summary` requests are counted too, so the totals cover the whole run rather than only the conversation turns.

## Shell

| Key | Default | Meaning |
|---|---:|---|
| `path` | `null` | Explicit shell executable; null enables auto-detection |
| `timeout_seconds` | `120` | Default and maximum command timeout; model-requested timeouts are capped at this value |
| `max_output_chars` | `60000` | Maximum stdout and stderr returned to the model |

Auto-detection probes each candidate (`pwsh`, then `bash`, `zsh`, and `sh`, or
`powershell` and Git Bash after `pwsh` on Windows) with a trivial command before
accepting it, so an
installed shell that cannot start — a pwsh build without libicu, a .NET runtime
that cannot reserve memory, a wrong-architecture binary — falls through to the
next one instead of failing every command. The probe costs one shell start
(roughly 0.2 s for pwsh) per process and is cached per executable. A
`shell.path` override is probed too and reports the failure instead of silently
switching shells. When no candidate starts, the `ShellError` raised by
`minimalcode shell` lists every probe failure. On Windows, `System32\bash.exe`
(the WSL launcher) is not accepted as the native bash candidate: it looks like
bash but runs the command inside a Linux namespace with different paths and
interpreters. `pwsh-preview` / `powershell-preview` map to their PowerShell
kind instead of falling back to `sh` semantics.

## Tools

| Key | Default | Meaning |
|---|---:|---|
| `allow` | `null` | Allowlist of tool names; `null` enables every tool the provider supports |
| `allow_outside_workspace` | `false` | Allow file tools outside the workspace |
| `max_file_chars` | `160000` | Maximum characters returned by one `read` call |
| `max_file_bytes` | `5000000` | Maximum size accepted by `write`, `append_file`, and `edit`; `grep` skips larger files |
| `max_results` | `200` | Default `grep`/`find` result limit |
| `max_parallel` | `4` | Concurrency for adjacent read-only tool calls |

`tools.allow` names are checked against the dispatcher's tool set when the runtime
is built, and an unknown name is a configuration error rather than a silent
no-op: intersecting with a typo would otherwise leave the agent with no usable
tools. `--read-only` intersects the same set with the read-only tools (`read`,
`read_range`, `grep`, `find`, `read_image`, `web_search`, `web_fetch`, `ocr`,
`transcribe`), so the flag and the
file compose in one direction only. Disabled tools are dropped from the request's
`tools` array and refused by `dispatch`, which also covers a replayed session or
a provider that ignores the schema list.

The `shell` tool runs through `SandboxRunner`. With the execution layer disabled
(the default) commands run with the current user's permissions; enabling the
layer adds policy, timeout, output, and resource controls.

`grep` and `find` skip ignored directories (`.git`, `node_modules`, `build`,
`dist`, caches, virtualenvs) in both the `ripgrep` path and the Python fallback.
The Python fallback uses ripgrep-compatible smart-case matching, runs the regex
in a timeout-bounded child process, and truncates long result lines. Glob
matching follows ripgrep in both engines: `*` does not cross `/`, `**/` matches
zero or more directories, and matching is case-sensitive. Every result path is
re-checked against the sandbox policy, so a sensitive file cannot appear in
search output even when the exclusion globs do not cover its spelling, and both
engines pass `--no-config` to `ripgrep` so `RIPGREP_CONFIG_PATH` cannot widen
the file set. An explicit `path` pointing at an ignored directory is still
searched. Neither path parses `.gitignore`; install `ripgrep` if ignore-file
(2000 for `grep`, 5000 for `find`) is clamped to that cap. Both engines skip
files larger than `tools.max_file_bytes` while walking a directory, so the cap
is applied to the `ripgrep` walk as well; a `path` that names such a file
directly raises `file is too large for grep` instead of returning no matches.
Line endings and encodings agree too: `ripgrep` runs with `--crlf`, so `$`
anchors at the line content rather than after the `\r`, and both Python engines
decode with `utf-8-sig`, so a leading BOM is dropped the way `ripgrep` drops it
and `^` matches line 1.

`read`, `read_range`, `write`, `append_file`, and `edit` operate on UTF-8 text.
Content containing a NUL byte is treated as binary and rejected, and `edit`
reports a non-UTF-8 file as a tool error instead of a codec exception. `read`
decodes with a leading UTF-8 BOM removed and reports the detected `line_ending`
(`LF`, `CRLF`, `CR`, or `none`); writes preserve an existing file's style.
`read` scans the requested range strictly first: if the bytes are not valid
UTF-8 it reads them again with replacement characters and adds a `warnings`
entry saying so, because `edit` refuses that same file. A valid file is decoded
once and carries no extra field. Python writes are compiled with `py_compile` and
checked with `ruff` (when installed); a new diagnostic rolls the write back and
returns its file, line, and source preview. Pre-existing lint findings do not
block an unrelated edit.

Adjacent read-only calls in one model turn run concurrently, bounded by
`tools.max_parallel`, and their results are still written back in request order.
A read-only call next to `write`, `edit`, `shell`, or `subagent` stays on the
main thread: the write's file replacement could otherwise land between a read's
path check and its open.

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
remain readable. Search results are re-checked against the same policy, so a
sensitive path cannot appear in `grep` / `find` output even when its spelling
(such as an upper-case `.PEM` suffix) is not covered by the exclusion globs.
Since 1.5.1 the same rule inspects a `shell` command string: a word that names a
sensitive path (`type .env`, `cat ~/.ssh/id_rsa`) is refused whether or not it is
quoted (`type ".env"` reads the same file), a quoted inline command
(`cmd /c "type .env"`, `pwsh -Command "Get-Content .env"`) is scanned in turn,
and a directory named by a long option (`minimalcode --home <dir>`) is not a
credential read. Multi-word quoted data such as `git commit -m "document .env"`
is not a path and stays allowed. Credential files are also recognized in their
YAML/TOML spellings (`secrets.yaml`, `credentials.toml`), `.envrc`, and
`.git/config`. Since 1.5.3 the value of a PowerShell `-Param:value` word is
checked too (`Get-Content -Path:.env`), and an escaped quote no longer hides the
rest of the line: the command is analysed once per dialect (`\"` for POSIX,
`` `" `` for PowerShell) and the findings are unioned, so the inspection can only
report more. Line continuations (`\` or `` ` `` before a newline) are joined
before analysis, because the shell joins them before it tokenizes. Since 1.5.4
the exemption is limited to known directory options (`--home`, `--dir`,
`--cwd`, `--chdir`, `--directory`, `--prefix`, `--project`, `--work-tree`,
`git -C`), so the `--` end-of-options marker no longer exempts a sensitive
directory (`grep -rn . -- ~/.ssh`), and a combined inline flag such as
`-ec`/`-xc` hands its quoted payload to the scanner the way `-c` does. A
PowerShell `-EncodedCommand` payload is decoded and scanned (an undecodable one
fails closed), and a bare backslash escape is resolved (`cat \.env` opens
`.env`), while the same text inside quotes stays a literal file name.

`network_policy`, the command timeout/output caps, and the resource limits only
apply while the execution layer is enabled:

- `deny` (default): filesystem/namespace backends (`bwrap`, `firejail`,
  `sandbox-exec`, `docker`) remove network access. The `local` baseline has no
  network namespace, so it rejects commands that look like network clients
  (`curl`, `wget`, `ssh`, `git clone`, `pip install`, package managers, ...).
  Nested `sh -c` / `pwsh -Command` strings are inspected with their separators
  intact, PowerShell `-EncodedCommand` payloads are decoded (an undecodable one
  fails closed, and the `-ec`/`-e`/`-en` abbreviations are recognized), and
  `cmd` caret escapes are folded before inspection. `npx`/`bunx` count as
  network clients, and `curl.cmd`/`wget.ps1` are matched by their stem. Several
  spellings that used to slip through are inspected as well: `env -S` /
  `env --split-string` (its value is the command to run), the `py` launcher,
  versioned interpreters (`python3.12 -c`, `py -3 -c`) and the attached `-m`
  form of a package install (`py -3 -m pip install`, `python -mpip install`),
  PowerShell's `(New-Object Net.WebClient).DownloadString`/`DownloadFile` both
  inside an inline command and at the command position (the Windows shell is
  PowerShell, so it is checked there as well), and a `python -c` payload that
  launches a process (`os.system`, `os.popen`, `subprocess.*`) counts as a
  network reach instead of being re-tokenized as an ordinary script. `curl`
  resolver/proxy options (`--resolve`, `--connect-to`, `--proxy`, `--socks5*`,
  `-x` in both the `-x host` and the attached `-xhost:port` form, while the
  uppercase `-X` request method is not read as a proxy) contribute their
  targets, so an allowed URL cannot be redirected to another host, and a UNC
  path contributes its host. A backend that isolates the network
  (`bwrap`, `firejail`, `sandbox-exec`, `docker`) removes it under `allowlist`
  exactly as under `deny`, because a container cannot enforce a host allowlist;
  only `allow` opens the network for those backends.
- `allow`: no network restriction.

Backends are probed at execution time, which only happens when the layer is
enabled. `auto` picks the first available of `bwrap`, `firejail`,
`sandbox-exec`, then `local`; the default is `local`. Selecting a backend that is
not installed refuses to run the command (it never silently downgrades). Path and
sensitive-file policy is enforced in-process, so the file tools stay usable even
when the execution layer is off.

`bwrap` isolates namespaces and mounts the workspace and the allowed roots
read-write with the rest of the filesystem read-only; it adds `--unshare-net`
for `deny` and `allowlist` and `--share-net` only for `allow`. `firejail` adds a
private temp directory plus `--net=none` for `deny` and `allowlist`.
`sandbox-exec` uses a profile that denies network traffic for both. `docker` runs
`sh -lc` in the configured image with the workspace mounted and no host
environment, so it refuses a PowerShell shell kind instead of handing its syntax
to `/bin/sh`; it also refuses argv-based search children, so `grep`/`find` use the
Python scanner: in-process with a deadline alarm on the main thread, or a trusted
host child interpreter when that guard is unavailable (a worker thread or
Windows). The `docker` backend is POSIX-host only (Docker Desktop cannot consume
the `C:\ws:C:\ws` mount this backend builds) and runs the container as the
invoking `uid:gid`, so workspace files do not end up owned by root.

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
| `max_parallel` | `4` | Adjacent `subagent` calls in one turn run concurrently, up to this many at a time |
| `timeout_seconds` | `900` | Default and maximum runtime for one child, further capped by `sandbox.timeout_seconds` while the execution layer is enabled |

The child inherits the parent's `--home`, provider, model, reasoning effort, and
workspace. The task text is piped through stdin, so it never appears on the
command line. The child runs with `-p --no-session --quiet`: it prints only its
final answer and leaves no session files behind. Because a child is one command,
its runtime is also bounded by `sandbox.timeout_seconds` while the execution layer
is enabled: when the subagent budget is larger, the tool clamps to the sandbox cap
and records the reason in the sandbox notes.

When one turn requests several adjacent `subagent` calls, they run in parallel
processes and their results are recorded in the original call order. Other tools
keep the request order, because a `write` followed by a `shell` command depends
on the earlier call finishing; a subagent group therefore never overlaps a
neighbouring tool call.

## Context

| Key | Default | Meaning |
|---|---:|---|
| `compact_at` | `0.8` | Compact when estimated context reaches this fraction |
| `keep_messages` | `12` | Minimum recent messages retained after compaction; a cut inside one long tool turn retains fewer so the history still shrinks |
| `max_instruction_chars` | `60000` | Combined budget for `SYSTEM.md`, global `AGENTS.md`, and project instruction files |

Compaction is lossy in the active context but the original JSONL history remains on disk until a later compaction record supersedes it for loading. A compaction that leaves the file far larger than the state it replaces rewrites the file (see Session).

Instruction files are re-read at the start of every run: global `AGENTS.md`, then `AGENTS.md` / `CLAUDE.md` from the workspace and its parents, with missing files skipped. Within one run the system prompt is built once, so a file a tool rewrites mid-turn takes effect on the next run. `/summary` uses this to persist a session summary into the workspace `AGENTS.md` between its summary markers.

## Output

| Key | Default | Meaning |
|---|---:|---|
| `markdown_highlight` | `true` | Render streamed Markdown with ANSI styling on interactive terminals |
| `theme` | `"dark"` | Palette for the highlighter: `dark` or `light` |

Highlighting only applies when stdout is a terminal. `--json`, redirected
output, `NO_COLOR`, and `--no-highlight` all keep the raw Markdown.

## Reminder

DeepSeek's own clients tell the model the current date, the user's location, the
platform, and the language. Over the API that is a `latest_reminder` message
placed immediately before the user message it belongs to, formatted as
`<date>,<weekday>,<location>,<platform>,<language>`.

| Key | Default | Meaning |
|---|---:|---|
| `enabled` | `true` | Build the reminder at all |
| `location` | `""` | Optional; may be empty and may contain commas |
| `platform` | `"App"` | `App` or `Web` |
| `language` | `"English"` | Language name written in that language, e.g. `English`, `中文` |

The weekday is written in `language` (`中文` → `星期一`); a language without a
weekday table falls back to English rather than sending a half-translated word.
The role is DeepSeek-specific, so a provider preset also has to opt in with
`"latest_reminder": true`; the built-in `deepseek-anthropic` preset does, the
`deepseek` (Responses) and `deepseek-web` presets cannot.

Reasoning deltas reported by the provider (`reasoning_content`, `reasoning`, a
Responses reasoning summary, or Anthropic thinking) are printed in the `h6`
heading style before the answer, and only when highlighting is active. They are
display-only in `--json` output. Two protocols need them in the next request of
a tool loop, so the assistant message that carried the tool calls keeps them:
Chat stores the turn's reasoning as `reasoning_content` and sends it back
(DeepSeek requires the field when a tool result follows), and Anthropic Messages
stores its thinking blocks with their signatures. A finished answer retains
neither: the next user turn does not need them, and some servers reject a stale
`reasoning_content`.

## Provider fields

| Key | Required | Meaning |
|---|---|---|
| `protocol` | yes | `"openai_chat"`, `"openai_responses"`, `"anthropic_messages"`, or `"deepseek_web"` |
| `base_url` | yes | Base URL up to `/v1`, or the complete protocol endpoint |
| `model` | no | Provider-specific default model; overrides the top-level model for this provider |
| `api_key_env` | no | Environment variable containing the API key |
| `api_key` | no | Inline API key; prefer an environment variable |
| `require_api_key` | no | Defaults to true when `api_key_env` is set; a loopback endpoint added from the page is written with `false` when no key is given |

Reserved request fields (`model`, `messages`, `stream`, `tools`, `tool_choice`) cannot be overridden through `extra_body`. The Anthropic payload also reserves `max_tokens`, `system`, and `thinking`.

`session_title`, `safety_check`, `latest_reminder`, and `server_tools` are
DeepSeek extensions, so they are opt-in per preset: a strict OpenAI-compatible
server that receives `title` or `safety_check` rejects the request. `title` and
`safety_check` are merged after `extra_body`, so a configured extra field cannot
reshape them. Only the first request of a session asks for a title — the
provider may hold the response open for up to five seconds while it generates
one — and the result is stored as its own `title` event in the JSONL session.
With `safety_check: {"level": "high"}` a refusal ends the turn and is reported as
an early stop instead of being shown as a normal answer.

`server_tools` declares the tools DeepSeek executes on its own servers:
`deepseek_web_search` (web search, and deep search while thinking) and
`deepseek_read_url` (read the links in the last user message). It is the only
default web path, and the only one that costs no per-request fee: the bundled
DeepSeek presets ship `server_tools.enabled: true` and contain no `web_tools`
block at all, so nothing here POSTs the per-request Search API. Only `read_link`
blocks are replayed in later turns, because the page body comes back encrypted
in `signature`; search results and opened pages are dropped by the service
itself. The declarations ride after the function tools and never appear for a
plain completion, so summaries and compaction never search. While a server tool
covers the same ground, the local `web_search` / `web_fetch` schemas are not
advertised and a replayed call to them is refused instead of silently billing
the standalone API. Server tools,
document parsing, the safety review, and session titles are declared on the chat
request, so they require the **chat** provider's `base_url` to be
`api.deepseek.com`; any other endpoint, including the `deepseek_web` chat-app
protocol, refuses the switch with a reason.

### Custom endpoints

`minimalcode config add-provider <name> --base-url URL` (and the page's
"自定义 API…" entry) append one `providers.<name>` block. The name must match
`[A-Za-z0-9][A-Za-z0-9._-]{0,31}` and must not exist yet: the command never
overwrites a bundled preset. `--protocol` defaults to `openai_chat`, `--model`
also becomes the default model, and the credential comes from `--api-key-env`
or `--api-key`. The page requires a key for a non-loopback endpoint and writes
`require_api_key: false` when a loopback one is saved without one. Editing
`providers.<name>` by hand stays supported; nothing else in the file changes.

### Which provider supplies the DeepSeek feature set

A session has one chat provider, but OCR, speech, and the standalone
Search/Fetch API are plain HTTP calls, so they can run on another configured
block. `deepseek.feature_source(settings, active)` looks for the first usable
`api.deepseek.com` provider: the active one when it is already DeepSeek, then the
`deepseek` and `deepseek-anthropic` presets, then the remaining names
alphabetically. A block whose `${VAR}` is unset, or that requires a key it does
not have, is skipped rather than failing startup (or advertising a tool that can
only 401). `deepseek.features()` reports `available` (a source exists), `active`
(the chat provider is the source), `provider` (its name), and `active_reason`
(why a request-side switch is hidden). The CLI and the web runtime resolve this
the same way, so switching to another vendor keeps the media tools instead of
dropping them.

`web_tools` is the opt-in paid fallback for that same capability, and no preset
ships it. Writing the whole block with `enabled: true` makes this process call
`POST /v1/tools/search` and `POST /v1/tools/fetch` itself, which bills 5 CNY per
1,000 successful requests. It stays the only web path for vendors that have no
server-side tools (OpenRouter, Groq, a local model, and so on): with
`server_tools` absent or disabled, the local `web_search` / `web_fetch` tools are
advertised exactly as before. That block does not have to belong to the chat
provider: `feature_source()` picks a configured DeepSeek block, so an OpenRouter
session can use DeepSeek's Search API with DeepSeek's key. An older settings file
that still has `web_tools.enabled: true` keeps working and prints one startup
line pointing at `server_tools.enabled`; nothing is rewritten. A leftover block
with `enabled: false` issues no request and costs nothing — delete it if you do
not want it in the file.
`prompt_cache: true` on an `anthropic_messages` provider marks one cache
breakpoint on the last tool definition, which covers the system prompt and the
tool list together; without tools the system block is marked instead. One
breakpoint avoids writing two cache entries for the same bytes on every turn. It
is opt-in because a gateway that speaks the Messages protocol but rejects
`cache_control` would fail every request. Cache reads are reported in
`cached_tokens`, and cached input counts as prompt tokens in the usage totals.

Requests that fail before a response is received (`ConnectError`, `ConnectTimeout`,
and `RemoteProtocolError` such as `Server disconnected without sending a
response.`) are retried 10 times with an exponential backoff from 0.5 s up to
8 s. `timeout_seconds` bounds the whole generation: streamed lines are checked
against it, and a non-streaming body is read under the same deadline, so a
server that trickles a byte at a time cannot hold a request open indefinitely.
A stream that already delivered data is not retried, so a partial answer is
never printed twice or billed twice; HTTP 429/503 keeps its single `Retry-After`
retry with the delay clamped to 0.2–5 s (a non-numeric or non-finite header falls
back to 1 s). Retry notices go to the tool activity stream, so `--quiet` and `--json`
stay silent.

Proxies: environment and system proxy settings apply to remote `base_url`
values. A loopback `base_url` (`127.0.0.1`, `::1`, `localhost`) ignores them
unconditionally, because routing a local provider through a proxy always fails —
a stopped Clash/corporate proxy answers 502 and a running one has no route back
to the caller's loopback interface. Set `trust_env` explicitly (per provider,
`web_tools`, or `vision`) when a target needs the opposite behaviour. The web
and vision clients share the same rules and the same retry budget.

For `openai_responses`, `include_usage` is not needed because usage is returned in the terminal response event. `send_reasoning_effort` sends `{"reasoning":{"effort": ...}}` (`none` disables thinking); reserved request fields are `model`, `input`, `stream`, `tools`, and `tool_choice`. For `openai_chat`, it sends `reasoning_effort`; `none` instead sends `{"thinking":{"type":"disabled"}}`.

For `anthropic_messages`, requests go to `base_url` plus `/v1/messages` unless
`base_url` already ends in `/v1` (then only `/messages` is appended) or already
names `/messages`. This makes both `https://api.deepseek.com/anthropic` and
`https://api.anthropic.com/v1` work. Authentication uses `x-api-key` and the
`anthropic-version: 2023-06-01` header; a custom `headers` entry with either
name overrides the generated one. Chat-style tool declarations are converted to
Anthropic's `name`/`description`/`input_schema` shape, and consecutive tool
results are merged into one user turn with every `tool_result` block ahead of
text. With `send_reasoning_effort: true`, `thinking` becomes
`{"type":"enabled","budget_tokens":N}` (`none` becomes
`{"type":"disabled"}`); the budget is capped below `max_tokens`. Thinking blocks
for an open tool call are replayed with their signatures until that tool turn
completes.

The built-in `deepseek-anthropic` provider points at DeepSeek's Anthropic
compatible endpoint and reuses `DEEPSEEK_API_KEY`:

```json
"deepseek-anthropic": {
  "protocol": "anthropic_messages",
  "base_url": "https://api.deepseek.com/anthropic",
  "api_key_env": "DEEPSEEK_API_KEY",
  "model": "deepseek-flash",
  "context_window": 1000000,
  "timeout_seconds": 120,
  "stream": true,
  "send_reasoning_effort": true,
  "max_tokens": 8192,
  "headers": {},
  "extra_body": {}
}
```

Select it per run with `minimalcode --provider deepseek-anthropic ...`. Image
blocks in stored history are converted to Anthropic `image` sources (base64 data
URLs become `type: "base64"`), though `read_image` itself still uses the
provider's `vision` endpoint.

## Built-in provider presets

`defaults.json` ships endpoint and credential presets for the providers below. A preset never guesses a model ID: pass it explicitly, for example `/model deepseek/deepseek-flash` in the interactive loop or `--provider` / `--model` on the command line. Existing `settings.json` files receive the presets through the normal defaults merge; a provider the user already defined wins for the keys it sets.

| Provider | `api_key_env` | Protocol |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `openai_responses` |
| `deepseek-anthropic` | `DEEPSEEK_API_KEY` | `anthropic_messages` |
| `deepseek-web` | `DEEPSEEK_WEB_TOKEN` | `deepseek_web` |
| `openai` | `OPENAI_API_KEY` | `openai_responses` |
| `anthropic` | `ANTHROPIC_API_KEY` | `anthropic_messages` |
| `google` | `GEMINI_API_KEY` | `openai_chat` |
| `openrouter` | `OPENROUTER_API_KEY` | `openai_chat` |
| `groq` | `GROQ_API_KEY` | `openai_chat` |
| `mistral` | `MISTRAL_API_KEY` | `openai_chat` |
| `xai` | `XAI_API_KEY` | `openai_chat` |
| `moonshot` | `MOONSHOT_API_KEY` | `openai_chat` |
| `zhipu` | `ZHIPUAI_API_KEY` | `openai_chat` |
| `dashscope` | `DASHSCOPE_API_KEY` | `openai_chat` |
| `siliconflow` | `SILICONFLOW_API_KEY` | `openai_chat` |
| `together` | `TOGETHER_API_KEY` | `openai_chat` |
| `fireworks` | `FIREWORKS_API_KEY` | `openai_chat` |
| `perplexity` | `PERPLEXITY_API_KEY` | `openai_chat` |
| `cerebras` | `CEREBRAS_API_KEY` | `openai_chat` |
| `sambanova` | `SAMBANOVA_API_KEY` | `openai_chat` |
| `nebius` | `NEBIUS_API_KEY` | `openai_chat` |
| `deepinfra` | `DEEPINFRA_TOKEN` | `openai_chat` |
| `novita` | `NOVITA_API_KEY` | `openai_chat` |
| `cohere` | `COHERE_API_KEY` | `openai_chat` |
| `github` | `GITHUB_TOKEN` | `openai_chat` |
| `volcengine` | `ARK_API_KEY` | `openai_chat` |
| `minimax` | `MINIMAX_API_KEY` | `openai_chat` |
| `hunyuan` | `HUNYUAN_API_KEY` | `openai_chat` |
| `qianfan` | `QIANFAN_API_KEY` | `openai_chat` |
| `modelscope` | `MODELSCOPE_API_KEY` | `openai_chat` |
| `baichuan` | `BAICHUAN_API_KEY` | `openai_chat` |
| `yi` | `YI_API_KEY` | `openai_chat` |
| `stepfun` | `STEP_API_KEY` | `openai_chat` |
| `spark` | `SPARK_API_PASSWORD` | `openai_chat` |
| `ppio` | `PPIO_API_KEY` | `openai_chat` |
| `ollama` | none (`require_api_key: false`) | `openai_chat` |
| `lmstudio` | none (`require_api_key: false`) | `openai_chat` |
| `vllm` | none (`require_api_key: false`) | `openai_chat` |

Presets pin the endpoint and the credential variable only. `context_window` is a conservative `32768` for the three local servers and the vendor's headline window for the rest; override it in `settings.json` when a different model needs more or less. Regional or plan-specific endpoints (MiniMax's `https://api.minimax.cn/v1`, StepFun's `https://api.stepfun.ai/v1`, Volcengine's `step_plan` endpoint) are a `base_url` override away. Cohere's compatibility API rejects `stream_options`, so its preset sets `include_usage: false`. Providers that need a signature or a query string per request (Azure OpenAI's `api-version`, Bedrock, Vertex) are not presets: configure them manually with `headers` and a full endpoint `base_url`.

`/model provider/model` switches the provider and model for the current session only; it does not rewrite `settings.json`. The switch rebuilds the web and vision clients, so a provider without those blocks stops advertising the corresponding tools, and it rebuilds the tool runtime so subagents inherit the new provider and model. Media tools follow the DeepSeek feature source instead: a provider without a `media` block keeps them while another DeepSeek block is configured. Use `/model` without arguments to list the active model and only the providers whose credentials are available: an inline `api_key`, an auth header, a set `api_key_env`, or `require_api_key: false`. Providers with a missing key are omitted instead of listed as unavailable.

The settings panel of the web UI (`minimalcode --web`) writes the same keys as
`config set-provider` / `config set-model`, plus provider-scoped overrides:
`providers.<name>.model` (pins the model for that vendor),
`providers.<name>.protocol` (changes the API format without touching `base_url`,
for a gateway that serves more than one wire format), and `providers.<name>.base_url`
(when the custom-API form defines or edits an endpoint). Choosing "自定义 API…"
writes a whole new `providers.<name>` block with `protocol`, `base_url`, and
optionally `model`, `api_key`, and `require_api_key`. An API key typed in the page
is stored as `providers.<name>.api_key`; the page only ever reports whether a key
is configured, never the key itself.

`deepseek_web` drives the consumer web endpoint rather than an API: the same
requests the browser makes, on the free account's quota. It needs the account's
web token, plus the optional wasm runtime:

```powershell
python -m pip install "minimalcode[deepseek-web]"   # wasmtime, for the proof of work
$env:DEEPSEEK_WEB_TOKEN = "<userToken from localStorage, with or without 'Bearer '>"
minimalcode --provider deepseek-web "运行测试并总结"
```

The token is the `userToken` a logged-in tab keeps in local storage (DevTools →
Application → Local Storage → `chat.deepseek.com`), not the session cookie: the
endpoint authenticates with `Authorization: Bearer <userToken>`. A cookie is only
useful behind a WAF that inspects it, and is optional.

| Key | Meaning |
|---|---|
| `tool_mode` | `"text"` (default) renders tool schemas into the prompt and parses calls back out; `"none"` disables both |
| `cookie_env` | Environment variable holding an optional Cookie header, default `DEEPSEEK_WEB_COOKIE` |
| `cookie` | Inline cookie value, for a settings file that should be self-contained |
| `model_type` | Web UI mode to request, default `"default"`; `-m` / `/model provider/<mode>` overrides it |
Every completion is gated by an `x-ds-pow-response` proof-of-work ticket, solved
locally by running DeepSeek's own wasm through wasmtime; the binary ships in the
wheel and is cached under `~/.minimalcode/cache` when it has to be fetched, and a
fetched copy must match the bundled SHA-256. The ticket is built from the
`create_pow_challenge` answer, with the prefix
`<salt>_<expire_at>_` and the module's status flag read as "non-zero means
solved". The endpoint has no function calling, so tools travel as text and both
the requested JSON block and DeepSeek's native DSML markup are parsed. Each turn
creates a fresh chat session — the id lives at `biz_data.id` — and flattens the
whole history into one prompt, so the client stays stateless and cannot
desynchronise from the server.
the requested JSON block and DeepSeek's native DSML markup are parsed. Each turn
creates a fresh chat session — the id lives at `biz_data.id` — and flattens the
whole history into one prompt, so the client stays stateless and cannot
desynchronise from the server.

## Claude Code environment

`minimalcode --anthropic-env` builds the same kind of provider entry from the
environment instead of the settings file, following the variable names Claude
Code uses. This is the intended entry point for a machine already configured
against DeepSeek's Anthropic-compatible endpoint; nothing is written to
`settings.json`, so an ordinary run keeps using the file configuration.

| Variable | Meaning |
|---|---|
| `ANTHROPIC_BASE_URL` | Endpoint root, default `https://api.deepseek.com/anthropic` |
| `ANTHROPIC_AUTH_TOKEN` | Bearer credential sent as `Authorization: Bearer`; wins when both are set |
| `ANTHROPIC_API_KEY` | Credential sent as `x-api-key`; at least one of the two is required |
| `ANTHROPIC_MODEL` | Model ID; falls back to `ANTHROPIC_DEFAULT_SONNET_MODEL`, then an error. `--model` wins |
| `CLAUDE_CODE_EFFORT_LEVEL` | Reasoning effort, same values as `--thinking`; `--thinking` wins |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Token threshold mapped to `context.compact_at` for this run |
| `ANTHROPIC_CONTEXT_WINDOW` | Context window override (MinimalCode extension) |
| `ANTHROPIC_MAX_TOKENS` | Output cap override, default `8192` (MinimalCode extension) |

A Claude Code model suffix such as `[1m]` or `[200k]` selects the context window
and is stripped before the request (`deepseek-flash[1m]` becomes
`deepseek-flash` with a 1000000-token window). Without a suffix the Anthropic
standard of 200000 applies. The flag rejects `--provider`, and it does not read
the file's `web_tools` / `vision` blocks, so `web_search`, `web_fetch`, and
`read_image` are unavailable for that run; use `--provider deepseek-anthropic`
when those tools are needed.

## Web tools

Two paths reach the same capability, and the default is the model-side one.

`server_tools` (see the provider table above) declares DeepSeek's
`deepseek_web_search` / `deepseek_read_url` on the request; the service searches
and reads pages inside the turn, only model tokens are billed, and the local
`web_search` / `web_fetch` schemas are not advertised. The bundled DeepSeek
presets ship it enabled, which is why the free path is also the default one.

`web_tools` is the standalone Search/Fetch client described in this section. No
preset ships it, so a fresh `settings.json` contains no paid endpoint at all and
nothing can call one by accident. Writing the whole block with `enabled: true` is
a deliberate choice for two cases: a vendor without server-side tools, and a
DeepSeek user who explicitly wants the local pair back. It bills 5 CNY per 1,000
successful requests:

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
  "default_need_content": false,
  "min_rerank_score": 0,
  "api_key_env": "DEEPSEEK_API_KEY",
  "headers": {}
}
```

`server_tools` wins when both are enabled: the local pair stays unadvertised and
a replayed `web_search` call is refused with a message pointing at these keys.
Set `server_tools.enabled: false` together with `web_tools.enabled: true` to use
the standalone API exclusively.

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

The result list is then ordered by `rerank_score` (descending, ties keep the
provider's order) and deduplicated by URL. Sorting happens before the
duplicate check, so a mirrored page contributes its best-scoring copy instead of
whichever copy the provider happened to return first. `min_rerank_score` drops
results the provider scored below it — 1.5 is its own threshold for a good
result, and `0` (the default) keeps everything. `default_need_content` is what
`web_search` sends when the model omits `need_content`.

The `web_search` query accepts the provider's search operators unchanged:
`site:host`, `-site:host`, `-word`, `-"exact phrase"`, `"exact phrase"`,
`lang:zh,en`, `after:2025`, `after:2025-01-01`, `before:2026`.

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

## Media tools

A `media` block adds OCR, speech recognition, speech synthesis, and document
parsing. All four share one HTTP client; a capability is enabled by naming its
path, so an endpoint without `tts_path` simply never offers `speech`:

```json
"media": {
  "enabled": true,
  "base_url": "https://api.deepseek.com",
  "ocr_path": "v1/tools/ocr",
  "asr_path": "v1/tools/asr",
  "tts_path": "v1/tools/tts",
  "files_path": "files",
  "timeout_seconds": 180,
  "max_upload_bytes": 104857600,
  "max_audio_bytes": 8388608,
  "max_pages": 1000,
  "max_chars": 120000,
  "voice_id": "stella",
  "format": "wav",
  "purpose": "text_recognition",
  "api_key_env": "DEEPSEEK_API_KEY",
  "headers": {}
}
```

| Key | Default | Meaning |
|---|---:|---|
| `enabled` | `false` | Build the client at all |
| `base_url` | provider `base_url` | API root; the tools paths are appended to it |
| `ocr_path` | `""` | Path of the OCR endpoint, enables `ocr` |
| `asr_path` | `""` | Path of the speech-recognition endpoint, enables `transcribe` |
| `tts_path` | `""` | Path of the synthesis endpoint, enables `speech` |
| `files_path` | `""` | Path of the Files API, enables `/attach` |
| `timeout_seconds` | `180` | HTTP timeout for one request |
| `max_upload_bytes` | `104857600` | Largest document sent (DeepSeek's ceiling is 100 MiB) |
| `max_audio_bytes` | `8388608` | Largest audio file sent (DeepSeek's ceiling is 8 MiB) |
| `max_pages` | `1000` | OCR pages kept from one response |
| `max_chars` | `120000` | Maximum characters returned to the model |
| `voice_id` | `"stella"` | `stella` or `tide` |
| `format` | `"wav"` | `wav` or `pcm` |
| `purpose` | `"text_recognition"` | Default parse mode: `text_recognition`, `rich_layout_recognition`, or `user_data` |

These requests are billable per page, per audio second, and per character, so
the client never retries them: a read timeout after the server started
processing would bill the same input twice. Limits are checked locally first, so
an oversized file never leaves the machine.

`ocr` and `transcribe` are read-only tools and stay available under
`--read-only`; `speech` writes an audio file and does not. `/attach` is not a
model tool: it uploads a document, and the returned `file_id` is attached to the
next user message, where each protocol wraps it in its own content block
(`file`, `document`, `input_file`). The same file name and content hash reuse a
`file_id`, so attaching a document twice costs one parse.

## Clawd on Desk

`integrations.clawd` reports session and tool lifecycle to the
[Clawd on Desk](https://github.com/rullerzhou-afk/clawd-on-desk) desktop pet.
It only sends metadata (agent id, session id, state, event name, tool name,
working directory, platform); prompts, tool arguments, and file contents never
leave the process.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Report lifecycle events at all |
| `agent_id` | `""` | Agent id Clawd recognizes; empty means use the id from `clawd install` |
| `runtime_file` | `null` | `null` reads `~/.clawd/runtime.json`; a path pins one Clawd instance |
| `timeout_seconds` | `1.0` | Timeout for one POST to Clawd's `/state` endpoint |

Reporting runs on a daemon thread with a one-second timeout, so a slow or
closed Clawd never delays the tool loop; a failed probe is not retried for five
seconds. Subagent children run with `MINIMALCODE_NO_CLAWD=1`, so only the
parent reports. `minimalcode clawd install` writes the agent into Clawd's
`clawd-prefs.json` and `remove` takes it back out; both refuse to run while
Clawd is running, because Clawd holds its preferences in memory and writes the
whole snapshot back.

## MCP

MinimalCode can attach Model Context Protocol servers and expose their tools to
the model as `mcp__<server>__<tool>`. The client speaks JSON-RPC directly, so
the only dependency is still `httpx2`.

No server is started while `mcp.servers` is empty. A server that fails to start
(no such command, handshake timeout, protocol error) is recorded and skipped:
the built-in tools and the remaining servers keep working, and the failure is
shown by `/mcp`, `/status`, and `minimalcode mcp`.

### Shared block

| Key | Type | Default | Meaning |
|---|---|---:|---|
| `enabled` | boolean | `true` | Start the configured servers; `false` keeps the definitions but starts nothing |
| `startup_timeout_seconds` | number | `20` | Handshake plus initial `tools/list` budget, 1–300 |
| `timeout_seconds` | number | `60` | Budget for one `tools/call` (and for a refresh), 1–3600 |
| `max_output_chars` | integer | `60000` | Character cap for one converted tool result; the head and tail are kept |
| `max_tools` | integer | `200` | Cap on advertised MCP tools across all servers, 1–1000 |
| `servers` | object | `{}` | Server name → definition |

Server names must match `[A-Za-z0-9][A-Za-z0-9_-]{0,31}` because the name
becomes part of every tool name the model sees.

### Server fields

| Key | Type | Default | Meaning |
|---|---|---|---|
| `transport` | string | `"stdio"` | `stdio`, `http` (streamable HTTP), or `sse` (legacy HTTP+SSE) |
| `command` | string | — | Required for `stdio`: executable to start |
| `args` | list of strings | `[]` | Arguments for `command` |
| `env` | object | `{}` | Extra environment variables; `${VAR}` is expanded at load time |
| `inherit_env` | boolean | `false` | Pass the whole parent environment instead of the base set |
| `cwd` | string or null | `null` | Working directory, resolved through the sandbox path policy (workspace by default) |
| `url` | string | — | Required for `http`/`sse`: an `http` or `https` endpoint |
| `headers` | object | `{}` | Extra request headers for `http`/`sse` |
| `enabled` | boolean | `true` | Set `false` to keep a definition without starting it |
| `read_only` | boolean | `false` | Treat every tool on this server as read-only |
| `timeout_seconds` | number | shared | Per-server override for one tool call |
| `startup_timeout_seconds` | number | shared | Per-server override for the handshake |
| `include_tools` | list or null | `null` | Only expose these server tool names |
| `exclude_tools` | list | `[]` | Never expose these server tool names |

### Transports

- `stdio` starts `command` with `args` in `cwd` and exchanges newline-delimited
  JSON-RPC on the child's pipes. Stderr is captured (last few lines) so a
  startup failure reports why.
- `http` posts one JSON-RPC message per request with
  `Accept: application/json, text/event-stream`, accepts a JSON or SSE reply,
  and echoes the server's `Mcp-Session-Id` and the negotiated
  `MCP-Protocol-Version` on later requests.
- `sse` opens a long-lived `GET` stream, reads the `endpoint` event, and posts
  messages to that URL while responses arrive on the stream.

### Tool names and policy

`tools/list` entries are converted to provider declarations. Names that contain
characters outside `[A-Za-z0-9_-]` are normalised, names longer than the
provider's 64-character limit keep a short hash of the original, and two tools
that normalise to the same name are kept apart by that hash. A missing
`inputSchema` (or a non-object one) is advertised as taking no arguments.

`mcp__<server>__<tool>` names participate in `tools.allow` exactly like
built-in names, and a server can be selected as a group:

```json
{
  "tools": {
    "allow": ["read", "grep", "mcp__github", "mcp__docs__*"]
  }
}
```

`--read-only` keeps an MCP tool only when the server's `readOnlyHint` is `true`,
or when the whole server is configured with `"read_only": true`. MCP tools
without annotations count as mutating.

`/mcp` prints the state of every server; `/mcp refresh` re-lists tools (and
retries a failed server) and rebuilds the schema list for the rest of the
session. `minimalcode mcp` prints the same information as JSON without a model
turn.

### Environment and workspace

A `stdio` server inherits only the variables it needs to start: `PATH`,
`PATHEXT`, `HOME`/`USERPROFILE`/`USERNAME`, `APPDATA`/`LOCALAPPDATA`,
`SystemRoot`/`WINDIR`/`ComSpec`, `TEMP`/`TMP`/`TMPDIR`, `LANG`/`LC_*`,
`SHELL`, `TERM`, `XDG_*`, and `PYTHONIOENCODING`. A server that needs a
credential or vendor variable gets it from `env` (or `inherit_env: true`):

```json
{
  "mcp": {
    "servers": {
      "github": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
      },
      "docs": {
        "transport": "http",
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
        "include_tools": ["search"]
      }
    }
  }
}
```

`cwd` is resolved with the same rules as `read`: the workspace (plus
`sandbox.allowed_roots`) unless `tools.allow_outside_workspace` is `true`, and
sensitive directories stay blocked unless `sandbox.allow_sensitive_files` is
set. The client also answers `roots/list` with the workspace URI, so a server
that respects roots sees the same boundary.

MCP servers are user-declared processes: they run with your privileges and are
not wrapped by the sandbox backends. The sandbox's network policy applies to
commands run through `shell`, not to an MCP server's own traffic. Treat a server
entry like any other program from that source.

`config show` redacts `headers` and `env` values the same way it redacts
provider credentials.

## See also


- [architecture.md](architecture.md): request flow, package layout, and which
  module implements each setting.
- [compatibility.md](compatibility.md): 0.6 → 0.7 import and patch-target
  migration for code that embeds MinimalCode.

