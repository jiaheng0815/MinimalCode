# MinimalCode vs Pi: 30-task coding suite

DeepSeek `deepseek-flash`, Responses API, thinking `high`, maximum output 2048 tokens. MinimalCode max turns: 8; Pi used its native loop with a 90-second process timeout. Each selected task was run once per agent; no automatic retries.

| Agent | Passed | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|---:|
| pi | 0/0 | 0.0 | 0.000 s | 0.000 s | $0.000000 |
| minimalcode | 25/30 | 16712.9 | 15.607 s | 13.615 s | $0.090216 |

## Category pass rate

| Category | Pi | MinimalCode |
|---|---:|---:|
| CLI integration | 0/0 | 0/1 |
| JavaScript and domain logic | 0/0 | 1/1 |
| JavaScript and event handling | 0/0 | 1/1 |
| JavaScript and serialization | 0/0 | 1/1 |
| algorithms | 0/0 | 3/3 |
| concurrency | 0/0 | 2/2 |
| concurrency and reliability | 0/0 | 1/1 |
| configuration | 0/0 | 1/1 |
| data structures | 0/0 | 3/3 |
| database | 0/0 | 1/1 |
| date and time | 0/0 | 1/1 |
| debugging and performance | 0/0 | 1/1 |
| domain and numeric | 0/0 | 0/2 |
| files and persistence | 0/0 | 2/2 |
| parsing and algorithms | 0/0 | 1/1 |
| security | 0/0 | 2/2 |
| security and API design | 0/0 | 1/1 |
| strings and parsing | 0/0 | 2/4 |
| validation | 0/0 | 1/1 |

## Task results

| # | Task | Category | Pi | MinimalCode |
|---:|---|---|---|---|
| 1 | `merge_intervals` | algorithms | not run | PASS 9306t 8.28s |
| 2 | `topological_sort` | algorithms | not run | PASS 14824t 11.70s |
| 3 | `dijkstra` | algorithms | not run | PASS 15509t 12.29s |
| 4 | `lru_cache` | data structures | not run | PASS 16824t 10.80s |
| 5 | `ring_buffer` | data structures | not run | PASS 13278t 13.02s |
| 6 | `union_find` | data structures | not run | PASS 14605t 10.18s |
| 7 | `env_parser` | strings and parsing | not run | FAIL |
| 8 | `semver` | strings and parsing | not run | FAIL |
| 9 | `url_query` | strings and parsing | not run | PASS 16631t 17.89s |
| 10 | `unicode_slug` | strings and parsing | not run | PASS 14342t 11.93s |
| 11 | `safe_join` | security | not run | PASS 15409t 16.15s |
| 12 | `secret_redaction` | security | not run | PASS 16130t 14.87s |
| 13 | `money_allocation` | domain and numeric | not run | FAIL |
| 14 | `duration_format` | domain and numeric | not run | FAIL |
| 15 | `business_day` | date and time | not run | PASS 14396t 13.29s |
| 16 | `deep_merge` | configuration | not run | PASS 20447t 16.37s |
| 17 | `json_store` | files and persistence | not run | PASS 14141t 10.05s |
| 18 | `log_rotation` | files and persistence | not run | PASS 28035t 20.64s |
| 19 | `sqlite_inventory` | database | not run | PASS 24008t 39.43s |
| 20 | `thread_counter` | concurrency | not run | PASS 13427t 9.87s |
| 21 | `async_map_limited` | concurrency | not run | PASS 18196t 18.09s |
| 22 | `async_retry` | concurrency and reliability | not run | PASS 15406t 13.08s |
| 23 | `sliding_window_bugfix` | debugging and performance | not run | PASS 12434t 9.35s |
| 24 | `arithmetic_parser` | parsing and algorithms | not run | PASS 15639t 13.77s |
| 25 | `schema_validation` | validation | not run | PASS 15705t 18.18s |
| 26 | `cursor_codec` | security and API design | not run | PASS 24282t 21.93s |
| 27 | `wordcount_cli` | CLI integration | not run | FAIL |
| 28 | `js_cart_pricing` | JavaScript and domain logic | not run | PASS 16165t 11.69s |
| 29 | `js_debounce` | JavaScript and event handling | not run | PASS 17166t 13.46s |
| 30 | `js_stable_stringify` | JavaScript and serialization | not run | PASS 14634t 13.10s |

## Failures

- `env_parser` / minimalcode: AssertionError: expected ValueError
- `semver` / minimalcode: agent exited 1
- `money_allocation` / minimalcode: agent exited 1
- `duration_format` / minimalcode: solution.DurationError: malformed duration: ' 2h 5m '
- `wordcount_cli` / minimalcode: AssertionError: json: expected {'lines': 2, 'words': 3, 'bytes': 19}, got {'lines': 2, 'words': 3, 'bytes': 21}

Recorded API cost: **$0.090216**. A timed-out agent may have used additional tokens that were not returned in its usage stream.
