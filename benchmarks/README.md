# DeepSeek Responses benchmark

This benchmark compares Pi and MinimalCode with the same small tool-use task.
It uses `deepseek-flash`, OpenAI Responses, `high` thinking, a 2048-token output
cap, fresh workspaces, no sessions, no extensions, and no project context.

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','Machine')
python benchmarks/deepseek_responses.py --runs 3
```

The script alternates agent order, stops on the first failure, and never retries
automatically. It deletes temporary configs and workspaces when complete.

Current high-thinking result:
[results-2026-10-24-deepseek-responses-high.md](results-2026-10-24-deepseek-responses-high.md).

Thirty-task medium coding result:
[coding-suite-results-2026-10-24-high.md](coding-suite-results-2026-10-24-high.md).

## Thirty-task coding suite

The coding suite contains 30 independent medium tasks across algorithms, data
structures, parsing, domain logic, security, persistence, databases,
concurrency, CLI behavior, and JavaScript. Validate it without API calls:

```powershell
python benchmarks/coding_suite.py --validate-only
```

Run the complete comparison with a hard estimated-cost stop:

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','Machine')
python benchmarks/coding_suite.py --max-budget-usd 0.60 `
  --output benchmarks/coding-suite-results-2026-10-24-high.md
```
