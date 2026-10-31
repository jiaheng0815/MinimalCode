# Compatibility and migration

Applies to MinimalCode 1.3.1. The 0.6 → 0.7 migration below still
describes the current public surface; 0.8, 1.0, 1.1, 1.2, 1.3, and 1.3.1 added
no further breaking import changes (`minimalcode.httpclient` and
`minimalcode.clipboard` are new modules, not renamed ones).

The public API of a package is the set of names in its `__all__`. Everything
else is internal and may move between releases.

## 1.0 compatibility promise

Within the 1.x series (from 1.0.0 onward), the public names in each package's
`__all__`, CLI flags, configuration keys, JSON output fields, and session file
format remain compatible. Removals and behavior changes are recorded in
`CHANGELOG.md`. Private helpers and module paths outside `__all__` may move in
any release; import them from the module that defines them, as listed below.

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
