# 30-task coding suite: minimalcode

DeepSeek `deepseek-flash`, Responses API, thinking `high`, maximum output 2048 tokens. MinimalCode max turns: 8. Each selected task was run once per agent; no automatic retries.
Not run: pi. No comparison against those agents is possible from this run.

| Agent | Passed | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|---:|
| minimalcode | 25/30 | 16712.9 | 15.607 s | 13.615 s | $0.090216 |

## Category pass rate

| Category | minimalcode |
|---|---:|
| CLI integration | 0/1 |
| JavaScript and domain logic | 1/1 |
| JavaScript and event handling | 1/1 |
| JavaScript and serialization | 1/1 |
| algorithms | 3/3 |
| concurrency | 2/2 |
| concurrency and reliability | 1/1 |
| configuration | 1/1 |
| data structures | 3/3 |
| database | 1/1 |
| date and time | 1/1 |
| debugging and performance | 1/1 |
| domain and numeric | 0/2 |
| files and persistence | 2/2 |
| parsing and algorithms | 1/1 |
| security | 2/2 |
| security and API design | 1/1 |
| strings and parsing | 2/4 |
| validation | 1/1 |

## Task results

| # | Task | Category | minimalcode |
|---:|---|---|---|
| 1 | `merge_intervals` | algorithms | PASS 9306t 8.28s |
| 2 | `topological_sort` | algorithms | PASS 14824t 11.70s |
| 3 | `dijkstra` | algorithms | PASS 15509t 12.29s |
| 4 | `lru_cache` | data structures | PASS 16824t 10.80s |
| 5 | `ring_buffer` | data structures | PASS 13278t 13.02s |
| 6 | `union_find` | data structures | PASS 14605t 10.18s |
| 7 | `env_parser` | strings and parsing | FAIL |
| 8 | `semver` | strings and parsing | FAIL |
| 9 | `url_query` | strings and parsing | PASS 16631t 17.89s |
| 10 | `unicode_slug` | strings and parsing | PASS 14342t 11.93s |
| 11 | `safe_join` | security | PASS 15409t 16.15s |
| 12 | `secret_redaction` | security | PASS 16130t 14.87s |
| 13 | `money_allocation` | domain and numeric | FAIL |
| 14 | `duration_format` | domain and numeric | FAIL |
| 15 | `business_day` | date and time | PASS 14396t 13.29s |
| 16 | `deep_merge` | configuration | PASS 20447t 16.37s |
| 17 | `json_store` | files and persistence | PASS 14141t 10.05s |
| 18 | `log_rotation` | files and persistence | PASS 28035t 20.64s |
| 19 | `sqlite_inventory` | database | PASS 24008t 39.43s |
| 20 | `thread_counter` | concurrency | PASS 13427t 9.87s |
| 21 | `async_map_limited` | concurrency | PASS 18196t 18.09s |
| 22 | `async_retry` | concurrency and reliability | PASS 15406t 13.08s |
| 23 | `sliding_window_bugfix` | debugging and performance | PASS 12434t 9.35s |
| 24 | `arithmetic_parser` | parsing and algorithms | PASS 15639t 13.77s |
| 25 | `schema_validation` | validation | PASS 15705t 18.18s |
| 26 | `cursor_codec` | security and API design | PASS 24282t 21.93s |
| 27 | `wordcount_cli` | CLI integration | FAIL |
| 28 | `js_cart_pricing` | JavaScript and domain logic | PASS 16165t 11.69s |
| 29 | `js_debounce` | JavaScript and event handling | PASS 17166t 13.46s |
| 30 | `js_stable_stringify` | JavaScript and serialization | PASS 14634t 13.10s |

## Failures

- `env_parser` / minimalcode: AssertionError: expected ValueError
- `semver` / minimalcode: agent exited 1
- `money_allocation` / minimalcode: agent exited 1
- `duration_format` / minimalcode: solution.DurationError: malformed duration: ' 2h 5m '
- `wordcount_cli` / minimalcode: AssertionError: json: expected {'lines': 2, 'words': 3, 'bytes': 19}, got {'lines': 2, 'words': 3, 'bytes': 21}

Recorded API cost: **$0.090216**. A timed-out agent may have used additional tokens that were not returned in its usage stream.
