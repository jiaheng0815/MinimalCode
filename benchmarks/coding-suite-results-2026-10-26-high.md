# MinimalCode vs Pi: 30-task coding suite

DeepSeek `deepseek-flash`, Responses API, thinking `high`, maximum output 2048 tokens. MinimalCode max turns: 8; Pi used its native loop with a 90-second process timeout. Each selected task was run once per agent; no automatic retries.

| Agent | Passed | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|---:|
| pi | 1/7 | 57795.3 | 36.696 s | 27.350 s | $0.024560 |
| minimalcode | 6/6 | 15466.2 | 12.410 s | 11.964 s | $0.015289 |

## Common passes (1 tasks)

| Agent | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|
| pi | 38812.0 | 27.350 s | 27.350 s | $0.005718 |
| minimalcode | 16604.0 | 11.957 s | 11.957 s | $0.002349 |

## Category pass rate

| Category | Pi | MinimalCode |
|---|---:|---:|
| CLI integration | 0/0 | 0/0 |
| JavaScript and domain logic | 0/0 | 0/0 |
| JavaScript and event handling | 0/0 | 0/0 |
| JavaScript and serialization | 0/0 | 0/0 |
| algorithms | 1/3 | 3/3 |
| concurrency | 0/0 | 0/0 |
| concurrency and reliability | 0/0 | 0/0 |
| configuration | 0/0 | 0/0 |
| data structures | 0/3 | 3/3 |
| database | 0/0 | 0/0 |
| date and time | 0/0 | 0/0 |
| debugging and performance | 0/0 | 0/0 |
| domain and numeric | 0/0 | 0/0 |
| files and persistence | 0/0 | 0/0 |
| parsing and algorithms | 0/0 | 0/0 |
| security | 0/0 | 0/0 |
| security and API design | 0/0 | 0/0 |
| strings and parsing | 0/1 | 0/0 |
| validation | 0/0 | 0/0 |

## Task results

| # | Task | Category | Pi | MinimalCode |
|---:|---|---|---|---|
| 1 | `merge_intervals` | algorithms | PASS 38812t 27.35s | PASS 16604t 11.96s |
| 2 | `topological_sort` | algorithms | FAIL | PASS 13690t 11.34s |
| 3 | `dijkstra` | algorithms | FAIL | PASS 17689t 16.50s |
| 4 | `lru_cache` | data structures | FAIL | PASS 16460t 12.57s |
| 5 | `ring_buffer` | data structures | FAIL | PASS 14095t 11.97s |
| 6 | `union_find` | data structures | FAIL | PASS 14259t 10.12s |
| 7 | `env_parser` | strings and parsing | FAIL | not run |
| 8 | `semver` | strings and parsing | not run | not run |
| 9 | `url_query` | strings and parsing | not run | not run |
| 10 | `unicode_slug` | strings and parsing | not run | not run |
| 11 | `safe_join` | security | not run | not run |
| 12 | `secret_redaction` | security | not run | not run |
| 13 | `money_allocation` | domain and numeric | not run | not run |
| 14 | `duration_format` | domain and numeric | not run | not run |
| 15 | `business_day` | date and time | not run | not run |
| 16 | `deep_merge` | configuration | not run | not run |
| 17 | `json_store` | files and persistence | not run | not run |
| 18 | `log_rotation` | files and persistence | not run | not run |
| 19 | `sqlite_inventory` | database | not run | not run |
| 20 | `thread_counter` | concurrency | not run | not run |
| 21 | `async_map_limited` | concurrency | not run | not run |
| 22 | `async_retry` | concurrency and reliability | not run | not run |
| 23 | `sliding_window_bugfix` | debugging and performance | not run | not run |
| 24 | `arithmetic_parser` | parsing and algorithms | not run | not run |
| 25 | `schema_validation` | validation | not run | not run |
| 26 | `cursor_codec` | security and API design | not run | not run |
| 27 | `wordcount_cli` | CLI integration | not run | not run |
| 28 | `js_cart_pricing` | JavaScript and domain logic | not run | not run |
| 29 | `js_debounce` | JavaScript and event handling | not run | not run |
| 30 | `js_stable_stringify` | JavaScript and serialization | not run | not run |

## Failures

- `topological_sort` / pi: timeout after 90s
- `dijkstra` / pi: NotImplementedError
- `lru_cache` / pi: timeout after 90s
- `ring_buffer` / pi: NotImplementedError
- `union_find` / pi: timeout after 90s
- `env_parser` / pi: timeout after 90s

Recorded API cost: **$0.039849**. A timed-out agent may have used additional tokens that were not returned in its usage stream.
