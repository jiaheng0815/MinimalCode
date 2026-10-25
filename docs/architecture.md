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
| `shell.py` | Shell detection, fast subprocess execution, UTF-8 normalization |
| `prompt.py` | Concise default prompt and `AGENTS.md` discovery |
| `provider.py` | OpenAI Chat/Responses HTTP and SSE transport |
| `web.py` | Configurable provider web search and fetch client |
| `vision.py` | Configurable provider vision client for inline image understanding |
| `tools.py` | Workspace-bound file, search, shell, subagent, image, and web tool dispatch |
| `subagent.py` | Child `minimalcode -p` command construction, depth tracking |
| `session.py` | Append-only JSONL history and project-scoped discovery |
| `agent.py` | Tool loop, usage accounting, compaction, interrupted-turn repair |
| `highlight.py` | Incremental Markdown rendering and ANSI styling for streamed text |
| `cli.py` | Interactive and non-interactive command handling |

## Performance choices

- One reusable `httpx2.Client` maintains the connection pool.
- Tool schemas are static; the system prompt is built once per agent rather than once per turn.
- `rg` is used when available; a bounded Python fallback keeps commands working without it.
- Tool output is truncated by head and tail, preserving both leading context and final errors.
- Session writes are append-only; configuration is the only atomically rewritten file.
- The renderer treats incomplete inline markers as active styles, emits text without waiting for a newline, and keeps redirected and JSON output as raw Markdown.
