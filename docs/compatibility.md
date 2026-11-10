# Compatibility and migration

Applies to MinimalCode 1.5.4. The 0.6 → 0.7 migration below still
describes the current public surface; 0.8, 1.0, 1.1, 1.2, 1.3, 1.3.1, 1.3.2,
1.4.0, 1.4.1, 1.4.2, 1.4.3, 1.5.0, 1.5.1, 1.5.2, 1.5.3, and 1.5.4 added no
further breaking import changes (`minimalcode.httpclient` and
`minimalcode.clipboard` are new modules, not renamed ones).

The public API of a package is the set of names in its `__all__`. Everything
else is internal and may move between releases.

No `__all__` name, CLI flag, configuration key, JSON output field, session
format, or import path was removed in 1.5.4. Every change is a defect fix: the
sandbox no longer exempts a sensitive directory behind `--`, scans combined
inline flags and encoded PowerShell payloads, and the Responses protocol stops
reporting one tool call twice.

## What 1.5.4 changed

- The directory-option exemption names the options it accepts, so the `--`
  end-of-options marker no longer exempts a sensitive directory.
- Combined inline flags (`-ec`, `-xc`) are treated like `-c`, and a PowerShell
  `-EncodedCommand` payload is decoded and scanned (undecodable fails closed).
- A bare backslash escape is resolved for unquoted words (`cat \.env`), while
  quoted text stays a literal file name.
- `curl -X POST` is no longer read as `-x` (proxy) under `allowlist`, and a
  process launcher inside `python -c` counts as a network reach under `deny`.
- A local script named like a network subcommand (`npm run install`) is allowed.
- `edit` counts physical lines, `append_file` keeps a CRLF payload for a file
  with no line ending, and `write` reports the ending it actually wrote.
- The Responses parsers assemble one element id per tool call and surface
  reasoning deltas on the non-streaming SSE path.

## What 1.5.3 changed

- The sensitive-path gate inspects the value of a PowerShell `-Param:value` word
  instead of skipping the whole word as an option.
- Both shell scanners analyse the command once per quote-escape dialect
  (`\"` and `` `" ``) and union the findings, so an escaped quote can no longer
  swallow the rest of the line.
- Line continuations (`\`, `` ` ``, `^` before a newline) are joined before
  analysis.
- A missing entry in the network option table no longer ends the subcommand
  probe: `npm --loglevel verbose install x` is detected again.
- Audit redaction covers `https://<token>@host` userinfo and keyword names with
  suffixes such as `AWS_SECRET_ACCESS_KEY`.
- `grep` passes `--crlf`, decodes with `utf-8-sig`, applies
  `tools.max_file_bytes` to both engines, and refuses an explicitly named
  oversized file with a clear error.
- A PowerShell block comment no longer makes `powershell_heredoc_delimiter`
  report a bash heredoc.

## What 1.5.2 changed

Every change is a defect fix or an additive protocol:

- `protocol: "deepseek_web"` speaks the chat.deepseek.com web endpoint: the
  payload is the browser's, the reply is a JSON-patch SSE stream, and the
  `x-ds-pow-response` proof-of-work ticket is solved with DeepSeek's own wasm
  module through wasmtime.
- The session id is read from `biz_data.id` (the real shape; the nested
  `chat_session.id` still works), and the completion carries `stream: true` plus a
  `client_stream_id`, without which the server answers one non-SSE JSON body.
- The single-use PoW ticket no longer rides along on the next turn's handshake,
  and the optional cookie is attached before the handshakes instead of after them.
- A `Bearer ` prefix pasted into `DEEPSEEK_WEB_TOKEN` is stripped instead of
  being doubled, and `-m`/`/model` picks the requested `model_type` over the
  preset's `default`.
- Untyped stream fragments are shown as answer text rather than dropped, a
  `response/content` patch is counted as answer text, an ordinary JSON answer
  with a `name` field stays prose, and a tool call whose string arguments cannot
  be decoded asks the model to repair itself instead of passing raw text as
  `{"value": ...}`.
- Streamed text is decoded without rewriting commas inside string literals, and
  the wasm solver allocates each argument at exactly its own length: the module
  frees with that length, so a reused or rounded buffer makes the call trap.
- `_read_body` maps bare `OSError`/TLS failures to `ProviderError`, so a dropped
  TLS session surfaces with the proxy hint instead of a traceback.
- `web_search` joins the boolean keys that are placeholder-expanded and
  type-checked, and `config show` redacts an inline `cookie`.

## What 1.5.1 changed

- Quotes no longer hide a sensitive path: `type ".env"` and
  `Get-Content '.ssh/id_rsa'` are refused like their unquoted forms. A directory
  named by a long option (`minimalcode --home <dir>`) is not treated as a
  credential read, which is what spawned subagents rely on.
- The network gate now sees four more spellings: curl's attached
  `-xhost:port` proxy, the `\\?\UNC\host\share` path form, a
  `(New-Object Net.WebClient).DownloadString(...)` typed straight into the shell,
  and inline scripts run by `py -3 -c` or a versioned interpreter. Under
  `allowlist` those targets also join the host comparison.
- Audit records redact quoted `-b` / `-H` / `--header` values, so
  `recent_denials` is no longer a second plaintext copy of a cookie or token.
- A Chat tool call sent as "id once, arguments in unlabelled chunks" keeps all
  of its arguments, and a server that reuses one `index` for a second call no
  longer interleaves the two. Each connection attempt is bounded by the
  remaining `timeout_seconds` budget.
- `edit` reports at most 40 lines of an out-of-range file, `write` refuses an
  existing file above `tools.max_file_bytes` instead of raising `MemoryError`,
  `grep(path=<file>, glob=…)` returns the same result with and without
  `ripgrep`, and the timeout a `subagent` reports matches the 3600-second
  ceiling that is actually enforced.
- Piped stdio is UTF-8 (children get `PYTHONUTF8=1`), `thinking` in
  `settings.json` is case-insensitive, and a multi-line paste starting with `/`
  reaches the model instead of being dropped as an unknown command.

## What 1.5.0 changed

## What 1.5.0 changed

Every change is a defect fix, and most make the sandbox stricter in a case that
used to be silently wrong:

- A `shell` command string is now checked against the sensitive-file policy. An
  unquoted word naming `.env`, `~/.ssh/id_rsa`, `.git/config`, and the rest of
  the list is refused before the command runs, in every sandbox mode, including
  the default `sandbox.enabled: false` where `read` and `write` already refuse
  the same paths. A quoted inline command (`cmd /c "type .env"`,
  `pwsh -Command "Get-Content .env"`) is scanned recursively; quoted data
  (`git commit -m "document .env"`) is left alone.
- The sensitive-path list recognizes `.envrc`, `secrets.yaml`/`.yml`/`.toml`,
  `credentials.yaml`/`.yml`/`.toml`, and `.git/config`. `secrets.py` and
  `credentials.py` stay readable, and the search exclusion globs list the new
  names individually so a source file is not hidden from `grep` either.
- `network_policy: allowlist` now isolates the network on the namespace and
  container backends (`bwrap` `/ `firejail` / `sandbox-exec` / `docker`) exactly
  as `deny` does. Treating it as `allow` gave the backend process unrestricted
  egress and left the string gate as the only control.
- A `data:` payload split across several SSE lines is reassembled before
  parsing, for the Chat, Responses, and Anthropic parsers. Streams that put one
  complete payload per `data:` line with no blank separator keep working.
- A Chat server that omits `index` and interleaves two tool calls now matches
  each fragment by call id, so both calls keep their arguments; an object-form
  `arguments` value replaces a partially received string instead of being
  ignored.
- Captured child output that is not valid UTF-8 is decoded with the active
  Windows console/OEM code page (then the locale encoding) instead of being
  turned into U+FFFD.

## What 1.4.3 changed
No public `__all__` name, CLI flag, configuration key, JSON output field, session
format, or import path changed; nothing was added or removed either. Every
change is a defect fix, and the observable differences only appear in cases
that used to be wrong:

- The network gate sees four spellings it used to miss entirely: `env -S` /
  `env --split-string` (the option's value is the command, and it was skipped as
  an ordinary option value), the `py` launcher and attached `-m` spelling of a
  `pip` install (`py -3 -m pip install`, `python -mpip install`), PowerShell's
  `(New-Object Net.WebClient).DownloadString/DownloadFile` inside an inline
  command, and `\\host\share` UNC paths, which reach SMB with no client name and
  no URL scheme. Under `allowlist` a UNC host is also compared against
  `allowed_hosts`.
- Audit records redact credentials attached to their flag (`-uadmin:pw`,
  `--user=admin:pw`, `mysql -phunter2`); `-p` is only masked when a value is
  attached, so `docker run -p 8080:80` stays readable.
- `write` keeps a file's UTF-8 BOM, matching what `edit` and `read` already did.
- A validator that cannot run (broken ruff configuration, timeout, missing tool)
  no longer counts as a new diagnostic, so it cannot roll back and delete a
  freshly written Python file; it is reported in `warnings` instead.
- A `glob` containing `/` is matched relative to the search root. `find` and
  `grep` used to return no results for it whenever `rg` was installed, while the
  Python fallback found the files.
- `{a,b}` expansion is capped, so a pathological pattern can no longer hang a
  search; and a match line is no longer split on a form feed, U+2028, or a lone
  CR, which silently dropped the tail of the match.
- Provider connection retries, including the `Retry-After` wait, now stay inside
  `timeout_seconds`.
- An Anthropic-compatible gateway that ends a stream with `data: [DONE]` instead
  of `message_stop` is treated as a clean finish once the stop reason arrived,
  so a completed answer and its tool calls are no longer dropped as incomplete.

## What 1.4.2 changed

No public `__all__` name, CLI flag, configuration key, JSON output field, session
format, or import path changed; nothing was added or removed either. Every
change is a defect fix, and the observable differences only appear in cases
that used to be wrong:

- The network gate inspects inline `sh -c` / `pwsh -Command` strings with their
  real command head, decodes every `-EncodedCommand` abbreviation, treats
  `npx`/`bunx` and `curl.cmd`/`wget.ps1` as network clients, and fails closed
  past the nesting budget. Quoted command names inside an ordinary inline
  command no longer trigger a false denial.
- A tool group larger than `tools.max_parallel` / `subagent.max_parallel` runs
  every call; a limit of 1 used to execute only the first one.
- `edit` keeps a UTF-8 BOM, rejects NUL bytes, and patches an unterminated last
  line with the file's own line ending.
- A session file whose last line lost its terminator gets one before the next
  append; a lone surrogate is escaped instead of failing the append; a
  `--session` directory outside the store keeps its permissions.
- A `shell.path` of `cmd.exe` is rejected instead of silently running nothing,
  and the `docker` backend refuses a PowerShell command rather than handing its
  syntax to `/bin/sh`.
- `/summary` writes through the tool policy, so `--read-only` and
  `tools.allow` now apply to it.

## What 1.4.1 changed

Additive: no public `__all__` name, CLI flag, configuration key, JSON output
field, or session format was removed, and no import path moved. New surfaces:

- `read_range` and `append_file`; both join `READ_ONLY_TOOLS`.
- `edit` accepts `start_line`, `end_line`, and `new_text`; `old_text` remains as
  the exact-text fallback.
- `read` results report the detected `line_ending`.

Behavior changes, all corrections:

- `edit` replaces a line range unless `old_text` is used, and rejects mixing the
  two modes.
- `write`, `edit`, and `append_file` roll back a Python write that introduces a
  `py_compile` error or a new `ruff` diagnostic.
- `settings.json` stores only the keys the user changed, so defaults added by a
  later release still apply.
- `--read-only` keeps `read_range` enabled.

## What 1.4.0 changed

Additive: no public `__all__` name, CLI flag, configuration key, JSON output
field, or session format was removed, and no import path moved. New surfaces:

- `--read-only`, plus the `tools.allow` and `tools.max_parallel` keys.
- Provider key `prompt_cache` for `anthropic_messages`.
- `ToolRuntime(read_only=...)`, and `available_tool_schemas(..., allowed=...)`.
- `/status` gains `read_only` and `tools`.

Behavior changes, all corrections:

- `read` on a non-UTF-8 range now returns lossy content plus a `warnings` entry
  instead of U+FFFD with no signal; a leading UTF-8 BOM is stripped.
- Adjacent read-only tool calls in one turn run concurrently; results keep
  request order.
- A compaction that would leave the session file more than twice the state it
  keeps rewrites the file to `meta` plus the compact event.

## What 1.3.2 changed

A defect-fix release: no public name, CLI flag, configuration key, JSON field,
session format, or tool schema was removed, and no import path moved.

Observable behavior changes, all corrections of a wrong result:

- `grep` / `find` hide every sensitive path. With ripgrep installed, the
  exclusion globs were narrower than the authoritative policy, so `.env-prod`,
  `KEY.PEM`, and `Secrets` came back from searches that `read` refused.
  Output lines are now re-checked against `SandboxRunner.is_allowed`, and both
  engines pass `--no-config` to ripgrep so `RIPGREP_CONFIG_PATH` cannot widen
  the file set or re-enable following symlinks.
- Glob matching now follows ripgrep in both engines: `*` does not cross `/`
  (`find("src/*.py")` no longer returns `src/deep/nested.py`) and matching is
  case-sensitive on Windows (`A.PY` no longer matches `*.py`).
- `write` rejects content containing a NUL byte instead of creating a file that
  `read` classifies as binary.
- Resuming a session whose recorded provider is not in `settings.json` is an
  error. It used to fall back to the default provider, sending a history
  recorded for one wire protocol to another.
- On Windows the WSL launcher (`C:\Windows\System32\bash.exe`) is no longer
  selected as the native shell.
- Compaction and `/summary` requests are counted in `usage` / `session_usage`
  and in the `[usage]` line.

The remaining fixes only change the outcome of failing cases: non-finite
`usage` numbers and a non-finite `Retry-After` no longer escape as
`OverflowError` / `ValueError`, an SSE body starting with a BOM keeps its first
event, a non-streaming Responses request answered with SSE keeps its text and
tool calls, a reused tool-call `index` starts a new call, an endpoint with a
query string keeps it, non-streaming bodies respect `timeout_seconds`, a failed
session write no longer ends the REPL, `subagent` worker-thread exhaustion is a
tool error, a session ID from another workspace resolves, an in-flight
truncated search result cannot masquerade as a match, a fully held reasoning
block resets its sanitizer, an isolated ESC no longer joins lines, and C1
control sequences are consumed instead of printed.

## What 1.3.1 changed

A defect-fix release: no public name, CLI flag, configuration key, JSON field,
session format, or tool schema was removed, and no import path moved.

Observable behavior changes, both corrections of a wrong result:

- A closed stdout reader (`minimalcode ... | head`) exits `0`. On Windows the
  broken pipe surfaces as `OSError: EINVAL`, which used to be reported as
  `error: [Errno 22] Invalid argument` with exit code `2`; the shutdown flush
  then failed again and replaced the status with `120`.
- `minimalcode --home DIR <command>` runs the management command. It used to
  send the command word to the model as a prompt, which cost a request and could
  execute tools.

The remaining fixes only change the outcome of failing cases: non-string
`role`/`type` values in provider payloads and session files are skipped instead
of raising, `redacted_thinking` in a non-streaming Anthropic reply no longer
breaks the turn, compaction cannot re-summarize its own summary, the Python
`grep` fallback matches line content without the terminator and reports
`truncated` only past the limit, `**/` globs match zero directories, quoted
command operators are not treated as separators, `busybox`/`toybox` wrappers and
wrapped commands inside `sh -c` are detected, `web_tools.trust_env` /
`vision.trust_env` honour `${ENV}` expansion, and a rate limit without retry time
reports the HTTP status instead of `None`.

## What 1.3.0 changed

No public name, CLI flag, configuration key, JSON field, session format, or tool
schema was removed. Additions: interactive clipboard image paste (empty Enter
reads the clipboard image and routes it through the provider `vision` block),
the `minimalcode.clipboard` module, and the `ClipboardError` exception type.

Observable behavior changes for an existing installation:

- A provider, web, or vision request answered with a 3xx now fails with an
  error instead of following the redirect. Following a 307/308 replayed the
  whole request body, and `x-api-key` plus custom credential headers survived a
  cross-origin hop.
- Files created by `write` use the process umask instead of a fixed `0600`.
- `minimalcode config set-model` also rewrites the active provider's `model`,
  so the command changes the model that is actually sent.
- Resuming a session restores the provider and model recorded in it; explicit
  `--provider` / `--model` still win.
- An empty Chat reply is stored with `content: ""` instead of `null`, and null
  history from older sessions is repaired at startup.
- `requires-python` is now `>=3.11,<3.14`, matching the tested matrix.

## What 1.2.0 changed

No public name, CLI flag, configuration key, JSON field, session format, or tool
schema was removed. Additions only:

- A third wire protocol, `anthropic_messages`, alongside `openai_chat` and
  `openai_responses`. It is selected per provider, so an existing file that uses
  the other two is unaffected.
- The `--anthropic-env` flag, which synthesizes a provider from `ANTHROPIC_*` /
  `CLAUDE_CODE_*` for one run without touching `settings.json`. It is mutually
  exclusive with `--provider`.
- The interactive `/model [provider/model]` command, which switches the active
  provider and model for the current session only. `settings.json` is not
  rewritten, so the next run starts from the configured default again.
- Provider presets in `defaults.json` for 33 hosted vendors plus `ollama`,
  `lmstudio`, and `vllm`. Presets merge under existing settings, so a provider a
  user already defined keeps the keys it sets.

The only observable behavior change for an unchanged settings file is the
provider listing growing: `config show` and the `/model` listing now include
`deepseek-anthropic` and the new presets. `--json` output, exit codes, session
files, and the four package `__all__` surfaces are unchanged.

## What 1.1.0 changed

No public name, CLI flag, configuration key, JSON field, session format, or tool
schema changed. Two behavior additions: repository instructions are re-read at the
start of every run instead of being cached per process, and the interactive
`/summary` command writes a marked summary block into the workspace `AGENTS.md`.

## What changed in 0.7.0

The package split temporarily kept the pre-split module-level names as warning
shims during development. They were removed before the 0.7.0 release, so the
shipped package exposes only the names in each `__all__`:

- The four package facades only re-export their `__all__` names. Looking up any
  other pre-split name raises `AttributeError`; there is no `__getattr__`
  fallback and no `DeprecationWarning`.
- Private helpers are imported from the submodule that defines them.
- `unittest.mock.patch` must target the module whose global is looked up at call
  time. A patch on a facade name no longer reaches the implementation.

CLI flags, configuration keys, session files, and tool behavior are unchanged.

## Import migration

| Removed facade name | Import from |
|---|---|
| `minimalcode.tools.IGNORED_DIRECTORIES` | `minimalcode.tools.search` |
| `minimalcode.tools.IGNORED_GLOBS` | `minimalcode.tools.search` |
| `minimalcode.tools.Path` | `pathlib` |
| `minimalcode.tools.shutil` | `shutil` |
| `minimalcode.tools.run_shell` | `minimalcode.shell` |
| `minimalcode.tools._inline_python_grep` | `minimalcode.tools.search.inline_python_grep` |
| `minimalcode.tools._parse_python_grep` | `minimalcode.tools.search._parse_python_grep` |
| `minimalcode.tools._run_bounded_command` | `minimalcode.tools.search.run_bounded_command` |
| `minimalcode.tools._run_python_grep` | `minimalcode.tools.search.run_python_grep` |
| `minimalcode.tools._smart_case_ignore` | `minimalcode.tools.search.smart_case_ignore` |
| `minimalcode.sandbox.shutil` | `minimalcode.sandbox.backends.shutil` |
| `minimalcode.sandbox._Capture` | `minimalcode.sandbox.execution._Capture` |
| `minimalcode.sandbox._child_env` | `minimalcode.sandbox.execution._child_env` |
| `minimalcode.sandbox._decode_output` | `minimalcode.sandbox.execution._decode_output` |
| `minimalcode.sandbox._execute` | `minimalcode.sandbox.execution._execute` |
| `minimalcode.sandbox._finish_capture` | `minimalcode.sandbox.execution._finish_capture` |
| `minimalcode.sandbox._join_readers` | `minimalcode.sandbox.execution._join_readers` |
| `minimalcode.sandbox._posix_limits` | `minimalcode.sandbox.execution._posix_limits` |
| `minimalcode.sandbox._pump` | `minimalcode.sandbox.execution._pump` |
| `minimalcode.sandbox._resource_limiter` | `minimalcode.sandbox.execution._resource_limiter` |
| `minimalcode.sandbox._set_limit` | `minimalcode.sandbox.execution._set_limit` |
| `minimalcode.sandbox._terminate_tree` | `minimalcode.sandbox.execution._terminate_tree` |
| `minimalcode.sandbox._auto_backend` | `minimalcode.sandbox.backends._auto_backend` |
| `minimalcode.sandbox._docker_user` | `minimalcode.sandbox.backends._docker_user` |
| `minimalcode.sandbox._host_allowed` | `minimalcode.sandbox.network.host_allowed` |
| `minimalcode.sandbox._is_within` | `minimalcode.sandbox.policy._is_within` |
| `minimalcode.sandbox._number` | `minimalcode.sandbox.policy._number` |
| `minimalcode.sandbox._string_tuple` | `minimalcode.sandbox.policy._string_tuple` |
| `minimalcode.provider.time` | `minimalcode.provider.transport.time` |
| `minimalcode.provider._connection_backoff` | `minimalcode.httpclient.connection_backoff` |
| `minimalcode.provider._content_text` | `minimalcode.provider.common.content_text` |
| `minimalcode.provider._decode_json_body` | `minimalcode.provider.common.decode_json_body` |
| `minimalcode.provider._is_event_stream` | `minimalcode.provider.common.is_event_stream` |
| `minimalcode.provider._normalize_usage` | `minimalcode.provider.common.normalize_usage` |
| `minimalcode.provider._stream_event_error` | `minimalcode.provider.common.stream_event_error` |
| `minimalcode.provider._tool_arguments` | `minimalcode.provider.common.tool_arguments` |
| `minimalcode.highlight._PALETTES` | `minimalcode.highlight.palette._PALETTES` |
| `minimalcode.highlight._supports_color` | `minimalcode.highlight.color._supports_color` |
| `minimalcode.highlight._enable_windows_ansi` | `minimalcode.highlight.color._enable_windows_ansi` |

## Patch-target migration

Patch the module that performs the lookup, not the facade:

| Old patch target | New patch target |
|---|---|
| `minimalcode.tools.shutil.which` | `minimalcode.tools.search_tools.shutil.which` |
| `minimalcode.tools._run_bounded_command` | `minimalcode.tools.search.run_bounded_command` |
| `minimalcode.tools.run_shell` | `minimalcode.tools.process.run_shell` |
| `minimalcode.tools.Path.expanduser` | `minimalcode.sandbox.runner.Path.expanduser` |
| `minimalcode.sandbox.shutil.which` | `minimalcode.sandbox.backends.shutil.which` |
| `minimalcode.provider.time.sleep` | `minimalcode.provider.transport.time.sleep` |
| `minimalcode.highlight._supports_color` | `minimalcode.highlight.color._supports_color` |

## Finding remaining uses

Facade access now fails early instead of warning, so the test suite is the
fastest detector:

```console
python -m unittest discover -s tests -v
python -m compileall -q minimalcode tests main.py
```

To audit a checkout that imports internals, search for the facade prefixes:

```console
rg -n "minimalcode\.(tools|sandbox|provider|highlight)\." .
```

## Policy

- No new compatibility shims are added for internal names. Rename imports in the
  same release that moves a helper.
- `__all__` is the contract; documenting a name there makes it part of the
  public API and keeps it stable across releases.
- Behavior changes and removals are recorded in `CHANGELOG.md`.
