# MinimalCode contributor notes

- Support Python 3.11–3.13 with `httpx2` as the only runtime dependency; keep that surface minimal and install `.[dev]` for tooling.
- Preserve plain-text terminal output and avoid TUI frameworks; ruff enforces a 88-column line length.
- Every tool that executes generated input must remain workspace-aware.
- Keep `minimalcode/{tools,sandbox,provider,highlight}/__init__.py` limited to stable `__all__` re-exports; do not add compatibility shims for internal names. Import internals from the submodule that defines them; `docs/architecture.md` records module ownership.
- Keep `minimalcode/defaults.json` in package data; CI asserts it is present in the built wheel.
- Add a regression test for every tool-loop, shell-selection, session, or provider protocol change.
- Before reporting completion, run the same checks as CI (Ubuntu, Windows, and macOS 15 Intel on 3.11–3.13):
  - `python -m unittest discover -s tests -v`
  - `python -m ruff check minimalcode tests benchmarks main.py`
  - `python -m ruff format --check minimalcode tests benchmarks main.py`
  - `python benchmarks/coding_suite.py --validate-only`
  - `python -m compileall -q minimalcode tests main.py`
