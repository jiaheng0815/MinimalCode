# MinimalCode contributor notes

- Keep the runtime dependency surface minimal.
- Preserve plain-text terminal output and avoid TUI frameworks.
- Every tool that executes generated input must remain workspace-aware.
- Run `python -m unittest discover -s tests -v` before reporting completion.
- Add a regression test for every tool-loop, shell-selection, session, or provider protocol change.
