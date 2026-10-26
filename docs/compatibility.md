# Compatibility shims

The `tools`, `sandbox`, `provider`, and `highlight` modules were split into
packages in 0.6.0. Old import paths still work, but the module-level names that
moved are now served by a temporary alias table.

## Policy

- Shims exist only to keep pre-split imports and patch targets from failing
  outright; they are **not** part of the supported API.
- Every access emits `DeprecationWarning` naming the owning module.
- Removal target: **0.7.0**. The tables live in `minimalcode/_compat.py` and the
  four package `__init__` modules.
- Stable names remain in each package's `__all__` and are not shimmed.

## Import migration

| Old import | New import |
|---|---|
| `from minimalcode.tools import _run_python_grep` | `from minimalcode.tools.search import run_python_grep` |
| `from minimalcode.tools import _run_bounded_command` | `from minimalcode.tools.search import run_bounded_command` |
| `from minimalcode.tools import IGNORED_DIRECTORIES` | `from minimalcode.tools.search import IGNORED_DIRECTORIES` |
| `from minimalcode.sandbox import _posix_limits` | `from minimalcode.sandbox.execution import _posix_limits` |
| `from minimalcode.highlight import _PALETTES` | `from minimalcode.highlight.palette import _PALETTES` |

## Patch-target migration

Patching a facade alias no longer changes behavior, because the implementation
no longer looks the name up on the facade. Use the defining module instead:

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

Run the suite with deprecations promoted to errors:

```console
python -W error::DeprecationWarning -m unittest discover -s tests -v
```

Any remaining facade alias access fails with a message naming the replacement.
