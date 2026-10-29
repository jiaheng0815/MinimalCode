# Compatibility and migration

Applies to MinimalCode 1.1.0 and later. The 0.6 → 0.7 migration below still
describes the current public surface; 0.8, 1.0, and 1.1 added no further breaking
import changes (`minimalcode.httpclient` is a new module, not a renamed one).

The public API of a package is the set of names in its `__all__`. Everything
else is internal and may move between releases.

## 1.0 compatibility promise

Within the 1.x series (from 1.0.0 onward), the public names in each package's
`__all__`, CLI flags, configuration keys, JSON output fields, and session file
format remain compatible. Removals and behavior changes are recorded in
`CHANGELOG.md`. Private helpers and module paths outside `__all__` may move in
any release; import them from the module that defines them, as listed below.

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
