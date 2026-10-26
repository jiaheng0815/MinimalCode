# MinimalCode contributor notes

- Keep the runtime dependency surface minimal.
- Preserve plain-text terminal output and avoid TUI frameworks.
- Every tool that executes generated input must remain workspace-aware.
- Keep `minimalcode/{tools,sandbox,provider,highlight}/__init__.py` limited to stable `__all__` re-exports; do not add compatibility shims for internal names.
- Run `python -m unittest discover -s tests -v` before reporting completion.
- Run `python -m ruff check` and `python -m ruff format --check` with the same paths as CI.
- Add a regression test for every tool-loop, shell-selection, session, or provider protocol change.
