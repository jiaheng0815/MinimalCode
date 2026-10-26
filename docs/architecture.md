# Architecture

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

Pre-split module-level names are still served by temporary, warning shims. Their
replacement imports and patch targets are listed in
[compatibility.md](compatibility.md); they are removed in 0.7.0.

## Performance choices

- One reusable `httpx2.Client` maintains the connection pool.
- Tool schemas are static; the system prompt is built once per agent rather than once per turn.
- `rg` is used when available; a timeout-bounded Python fallback keeps commands working without it.
- Shell and search output is captured through bounded head/tail buffers, so a noisy command cannot exhaust memory or disk.
- Tool output is truncated by head and tail, preserving both leading context and final errors.
- Untrusted model, tool, and error text is stripped of terminal control sequences before display.
- Session writes are append-only; configuration is the only atomically rewritten file.
- The renderer treats incomplete inline markers as active styles, emits text without waiting for a newline, and keeps redirected and JSON output as raw Markdown.
