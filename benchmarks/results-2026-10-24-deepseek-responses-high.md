# DeepSeek Responses high-thinking benchmark

Date: 2026-10-24

Environment:

- Windows
- Pi 1.1.0
- MinimalCode 0.1.0
- DeepSeek `deepseek-flash`
- OpenAI Responses API
- Thinking level: `high`
- Maximum output: 2048 tokens
- Three runs per agent with alternating order
- Fresh workspace, no session, no extensions, no skills, no project context
- No automatic retries

Task:

> Use the write tool exactly once to create answer.txt containing exactly 42 with
> no newline. Do not read any files. Then reply DONE only.

All six runs created the correct file and returned `DONE`.

## Token usage

| Agent | Mean total tokens | Mean uncached input | Mean cache read | Mean output |
|---|---:|---:|---:|---:|
| Pi | 3483.7 | 1536.0 | 1877.3 | 70.3 |
| MinimalCode | 2399.7 | 1285.3 | 1024.0 | 90.3 |

MinimalCode used 31.1% fewer total tokens. It used 16.3% less uncached input
and 45.5% less cache-read input, while producing more output tokens.

## Time

| Agent | Mean | Median | Range |
|---|---:|---:|---:|
| Pi | 7.705 s | 2.317 s | 2.272–18.527 s |
| MinimalCode | 2.340 s | 2.325 s | 2.248–2.446 s |

Median latency is effectively tied. Pi's mean is dominated by one 18.527-second
outlier, which is likely provider or network variance rather than tool overhead.

## Estimated cost

Prices use the DeepSeek Flash catalog rates in the local Pi installation:
$0.30/M uncached input, $1.20/M output, and $0.006/M cache-read input.

| Agent | Mean per run | Total |
|---|---:|---:|
| Pi | $0.000556 | $0.001669 |
| MinimalCode | $0.000500 | $0.001500 |

The full six-task benchmark cost approximately $0.00317.

## Per-run totals

| Run | Agent | Total tokens | Time | Cost |
|---:|---|---:|---:|---:|
| 1 | Pi | 3537 | 2.317 s | $0.000697 |
| 1 | MinimalCode | 2475 | 2.446 s | $0.000557 |
| 2 | MinimalCode | 2333 | 2.248 s | $0.000450 |
| 2 | Pi | 3457 | 2.272 s | $0.000486 |
| 3 | Pi | 3457 | 18.527 s | $0.000486 |
| 3 | MinimalCode | 2391 | 2.325 s | $0.000494 |

## Interpretation

MinimalCode has a clear token-efficiency advantage in this task and sample.
Latency is comparable under normal conditions. The sample is intentionally
small to control cost, so the long-tail timing result should not be treated as
a stable mean-latency claim.
