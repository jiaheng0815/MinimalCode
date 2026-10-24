# MinimalCode vs Pi: 30-task coding suite

DeepSeek `deepseek-flash`, Responses API, thinking `high`, maximum output 2048 tokens. MinimalCode max turns: 8; Pi used its native loop with a 90-second process timeout. Each selected task was run once per agent; no automatic retries.

| Agent | Passed | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|---:|
| pi | 23/30 | 36393.7 | 28.843 s | 27.829 s | $0.144389 |
| minimalcode | 22/30 | 15802.5 | 15.270 s | 12.700 s | $0.093795 |

## Common passes (19 tasks)

| Agent | Mean tokens | Mean time | Median time | Total cost |
|---|---:|---:|---:|---:|
| pi | 33727.7 | 28.497 s | 27.829 s | $0.086758 |
| minimalcode | 13949.4 | 12.670 s | 12.411 s | $0.051087 |

## Category pass rate

| Category | Pi | MinimalCode |
|---|---:|---:|
| CLI integration | 1/1 | 1/1 |
| JavaScript and domain logic | 0/1 | 1/1 |
| JavaScript and event handling | 0/1 | 1/1 |
| JavaScript and serialization | 0/1 | 1/1 |
| algorithms | 3/3 | 2/3 |
| concurrency | 1/2 | 1/2 |
| concurrency and reliability | 1/1 | 1/1 |
| configuration | 1/1 | 1/1 |
| data structures | 3/3 | 3/3 |
| database | 1/1 | 1/1 |
| date and time | 1/1 | 1/1 |
| debugging and performance | 1/1 | 1/1 |
| domain and numeric | 1/2 | 0/2 |
| files and persistence | 2/2 | 2/2 |
| parsing and algorithms | 1/1 | 0/1 |
| security | 1/2 | 1/2 |
| security and API design | 1/1 | 0/1 |
| strings and parsing | 3/4 | 3/4 |
| validation | 1/1 | 1/1 |

## Task results

| # | Task | Category | Pi | MinimalCode |
|---:|---|---|---|---|
| 1 | `merge_intervals` | algorithms | PASS 16283t 10.76s | FAIL |
| 2 | `topological_sort` | algorithms | PASS 17857t 13.94s | PASS 11706t 8.07s |
| 3 | `dijkstra` | algorithms | PASS 30128t 39.19s | PASS 15537t 12.36s |
| 4 | `lru_cache` | data structures | PASS 20534t 12.21s | PASS 11190t 9.18s |
| 5 | `ring_buffer` | data structures | PASS 25483t 31.96s | PASS 10338t 8.23s |
| 6 | `union_find` | data structures | PASS 23210t 32.04s | PASS 11125t 24.37s |
| 7 | `env_parser` | strings and parsing | FAIL | FAIL |
| 8 | `semver` | strings and parsing | PASS 79517t 36.59s | PASS 13989t 12.41s |
| 9 | `url_query` | strings and parsing | PASS 33782t 25.20s | PASS 13273t 12.38s |
| 10 | `unicode_slug` | strings and parsing | PASS 34178t 27.83s | PASS 11570t 11.35s |
| 11 | `safe_join` | security | PASS 27445t 32.57s | PASS 16988t 16.76s |
| 12 | `secret_redaction` | security | FAIL | FAIL |
| 13 | `money_allocation` | domain and numeric | FAIL | FAIL |
| 14 | `duration_format` | domain and numeric | PASS 66826t 28.57s | FAIL |
| 15 | `business_day` | date and time | PASS 78819t 74.78s | PASS 12856t 12.73s |
| 16 | `deep_merge` | configuration | PASS 16835t 17.31s | PASS 12604t 12.67s |
| 17 | `json_store` | files and persistence | PASS 20881t 10.11s | PASS 11466t 8.93s |
| 18 | `log_rotation` | files and persistence | PASS 45470t 29.89s | PASS 28080t 19.40s |
| 19 | `sqlite_inventory` | database | PASS 40697t 30.09s | PASS 16069t 15.62s |
| 20 | `thread_counter` | concurrency | PASS 16492t 27.41s | PASS 14230t 8.93s |
| 21 | `async_map_limited` | concurrency | FAIL | FAIL |
| 22 | `async_retry` | concurrency and reliability | PASS 20086t 16.49s | PASS 17835t 14.17s |
| 23 | `sliding_window_bugfix` | debugging and performance | PASS 19049t 8.57s | PASS 6855t 5.72s |
| 24 | `arithmetic_parser` | parsing and algorithms | PASS 68134t 41.99s | FAIL |
| 25 | `schema_validation` | validation | PASS 45856t 50.75s | PASS 16766t 12.63s |
| 26 | `cursor_codec` | security and API design | PASS 79774t 45.84s | FAIL |
| 27 | `wordcount_cli` | CLI integration | PASS 44508t 24.51s | PASS 12561t 14.81s |
| 28 | `js_cart_pricing` | JavaScript and domain logic | FAIL | PASS 16608t 11.73s |
| 29 | `js_debounce` | JavaScript and event handling | FAIL | PASS 7959t 7.74s |
| 30 | `js_stable_stringify` | JavaScript and serialization | FAIL | PASS 21726t 16.44s |

## Failures

- `merge_intervals` / minimalcode: AssertionError: merge: expected [[1, 4], [5, 7], [8, 8]], got [[1, 8]]
- `env_parser` / pi: NotImplementedError
- `env_parser` / minimalcode: AssertionError: expected ValueError
- `secret_redaction` / minimalcode: agent exited 1
- `secret_redaction` / pi: NotImplementedError
- `money_allocation` / pi: NotImplementedError
- `money_allocation` / minimalcode: agent exited 1
- `duration_format` / minimalcode: solution.DurationError: malformed duration: ' 2h 5m '
- `async_map_limited` / pi: NotImplementedError
- `async_map_limited` / minimalcode: agent exited 1
- `arithmetic_parser` / minimalcode: agent exited 1
- `cursor_codec` / minimalcode: agent exited 1
- `js_cart_pricing` / pi: timeout after 90s
- `js_debounce` / pi: }
- `js_stable_stringify` / pi: Node.js v24.14.1

Recorded API cost: **$0.238183**. A timed-out agent may have used additional tokens that were not returned in its usage stream.
