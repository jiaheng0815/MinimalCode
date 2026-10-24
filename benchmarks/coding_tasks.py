from __future__ import annotations

from dataclasses import dataclass, field

TEST_PREFIX = '''\
import traceback

def check(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")

def raises(exc_type, fn, contains=None):
    try:
        fn()
    except exc_type as exc:
        if contains is not None and contains not in str(exc):
            raise AssertionError(f"error missing {contains!r}: {exc}")
        return
    raise AssertionError(f"expected {exc_type.__name__}")

try:
'''
TEST_SUFFIX = '''\
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
print("HIDDEN TESTS PASSED")
'''


@dataclass(slots=True)
class CodingTask:
    id: str
    category: str
    prompt: str
    files: dict[str, str]
    hidden_test: str
    language: str = "python"
    visible_command: str = "python smoke.py"
    extra: dict[str, str] = field(default_factory=dict)


def py_task(
    task_id: str,
    category: str,
    prompt: str,
    source: str,
    visible: str,
    hidden_body: str,
    extra: dict[str, str] | None = None,
) -> CodingTask:
    files = {
        "solution.py": source,
        "smoke.py": visible,
        "README.md": f"# {task_id}\n\n{category}\n\n{prompt}\n",
    }
    if extra:
        files.update(extra)
    return CodingTask(
        id=task_id,
        category=category,
        prompt=prompt,
        files=files,
        hidden_test=TEST_PREFIX + hidden_body + TEST_SUFFIX,
    )


TASKS: list[CodingTask] = [
    py_task(
        "merge_intervals",
        "algorithms",
        """Implement `merge_intervals(intervals)` in solution.py.
Each interval is `[start, end]`. Return sorted `[start, end]` lists, merging
overlapping and touching intervals. Raise ValueError if start > end. Do not
mutate the input or use external packages. Run smoke.py.""",
        '''\
def merge_intervals(intervals):
    """Merge overlapping or touching inclusive intervals."""
    raise NotImplementedError
''',
        '''\
from solution import merge_intervals

assert merge_intervals([[1, 3], [2, 4]]) == [[1, 4]]
print("smoke ok")
''',
        '''\
    from solution import merge_intervals
    check("empty", merge_intervals([]), [])
    source = [[5, 7], [1, 3], [2, 4], [8, 8]]
    check("merge", merge_intervals(source), [[1, 4], [5, 7], [8, 8]])
    check("input unchanged", source, [[5, 7], [1, 3], [2, 4], [8, 8]])
    check("touching", merge_intervals([[1, 2], [2, 3]]), [[1, 3]])
    raises(ValueError, lambda: merge_intervals([[3, 2]]))
''',
    ),
    py_task(
        "topological_sort",
        "algorithms",
        """Implement `topological_sort(nodes, edges)` using Kahn's algorithm.
Edges are `(before, after)`. Return the lexicographically smallest valid order.
Ignore duplicate edges. Raise `CycleError` (a ValueError subclass) for a cycle
or unknown node. Keep the implementation O(V + E log V).""",
        '''\
class CycleError(ValueError):
    pass


def topological_sort(nodes, edges):
    """Return the lexicographically smallest topological order."""
    raise NotImplementedError
''',
        '''\
from solution import topological_sort

assert topological_sort(["b", "a"], [("a", "b")]) == ["a", "b"]
print("smoke ok")
''',
        '''\
    from solution import CycleError, topological_sort
    check("empty", topological_sort([], []), [])
    check(
        "lexical",
        topological_sort(["c", "a", "b", "d"], [("a", "c"), ("b", "c"), ("c", "d")]),
        ["a", "b", "c", "d"],
    )
    check("duplicates", topological_sort(["a", "b"], [("a", "b"), ("a", "b")]), ["a", "b"])
    raises(CycleError, lambda: topological_sort(["a", "b"], [("a", "b"), ("b", "a")]))
    raises(CycleError, lambda: topological_sort(["a"], [("a", "missing")]))
''',
    ),
    py_task(
        "dijkstra",
        "algorithms",
        """Implement `shortest_path(graph, start, end)` for a directed graph.
`graph` maps a node to `[(neighbor, nonnegative_weight), ...]`. Return
`(distance, path)` or None if unreachable. If start == end return `(0, [start])`.
Raise ValueError for negative weights or unknown endpoints.""",
        '''\
def shortest_path(graph, start, end):
    """Return the minimum-cost path in a directed graph."""
    raise NotImplementedError
''',
        '''\
from solution import shortest_path

graph = {"a": [("b", 1)], "b": []}
assert shortest_path(graph, "a", "b") == (1, ["a", "b"])
print("smoke ok")
''',
        '''\
    from solution import shortest_path
    graph = {"a": [("b", 3), ("c", 1)], "c": [("b", 1)], "b": [("d", 3)], "d": []}
    check("shortest", shortest_path(graph, "a", "d"), (5, ["a", "c", "b", "d"]))
    check("same", shortest_path(graph, "a", "a"), (0, ["a"]))
    check("unreachable", shortest_path({"a": [], "b": []}, "a", "b"), None)
    raises(ValueError, lambda: shortest_path({"a": [("b", -1)], "b": []}, "a", "b"))
    raises(ValueError, lambda: shortest_path({"a": []}, "a", "x"))
''',
    ),
    py_task(
        "lru_cache",
        "data structures",
        """Implement `LRUCache(capacity)` with `get(key, default=None)`,
`put(key, value)`, and `keys()` returning keys from most to least recently
used. `get` refreshes recency. `put` on an existing key replaces value and
refreshes it. Raise ValueError for capacity <= 0. Operations must be O(1).""",
        '''\
class LRUCache:
    def __init__(self, capacity):
        raise NotImplementedError

    def get(self, key, default=None):
        raise NotImplementedError

    def put(self, key, value):
        raise NotImplementedError

    def keys(self):
        raise NotImplementedError
''',
        '''\
from solution import LRUCache

cache = LRUCache(1)
cache.put("a", 1)
assert cache.get("a") == 1
print("smoke ok")
''',
        '''\
    from solution import LRUCache
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    check("initial", cache.keys(), ["b", "a"])
    check("get", cache.get("a"), 1)
    cache.put("c", 3)
    check("evicted", cache.keys(), ["c", "a"])
    check("missing", cache.get("b", 99), 99)
    cache.put("a", 7)
    check("replace", cache.get("a"), 7)
    check("order", cache.keys(), ["a", "c"])
    raises(ValueError, lambda: LRUCache(0))
''',
    ),
    py_task(
        "ring_buffer",
        "data structures",
        """Implement `RingBuffer(capacity)` with `push(value)` returning the
overwritten value when full, `oldest(count)` and `snapshot()`. Order is oldest
to newest. Reject capacity <= 0 and count outside 0..size.""",
        '''\
class RingBuffer:
    def __init__(self, capacity):
        raise NotImplementedError

    def push(self, value):
        raise NotImplementedError

    def oldest(self, count):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError
''',
        '''\
from solution import RingBuffer

buffer = RingBuffer(2)
buffer.push(1)
assert buffer.snapshot() == [1]
print("smoke ok")
''',
        '''\
    from solution import RingBuffer
    buffer = RingBuffer(3)
    check("push 1", buffer.push(1), None)
    buffer.push(2)
    buffer.push(3)
    check("full overwrite", buffer.push(4), 1)
    check("snapshot", buffer.snapshot(), [2, 3, 4])
    check("oldest", buffer.oldest(2), [2, 3])
    check("zero", buffer.oldest(0), [])
    raises(ValueError, lambda: buffer.oldest(4))
    raises(ValueError, lambda: RingBuffer(-1))
''',
    ),
    py_task(
        "union_find",
        "data structures",
        """Implement `UnionFind(n)` with `union(a, b)` returning whether two
components were merged, `connected(a, b)`, `size(a)`, and `components()`
returning sorted lists of sorted members. Use path compression and union by
size. Raise IndexError for invalid indices.""",
        '''\
class UnionFind:
    def __init__(self, n):
        raise NotImplementedError

    def union(self, a, b):
        raise NotImplementedError

    def connected(self, a, b):
        raise NotImplementedError

    def size(self, a):
        raise NotImplementedError

    def components(self):
        raise NotImplementedError
''',
        '''\
from solution import UnionFind

uf = UnionFind(3)
assert uf.union(0, 1) is True
assert uf.connected(0, 1)
print("smoke ok")
''',
        '''\
    from solution import UnionFind
    uf = UnionFind(6)
    check("merge", uf.union(0, 1), True)
    check("repeat", uf.union(1, 0), False)
    uf.union(2, 3)
    uf.union(1, 2)
    check("connected", uf.connected(0, 3), True)
    check("size", uf.size(3), 4)
    check("components", uf.components(), [[0, 1, 2, 3], [4], [5]])
    raises(IndexError, lambda: uf.connected(0, 6))
''',
    ),
    py_task(
        "env_parser",
        "strings and parsing",
        """Implement `parse_env(text)` returning a dict. Ignore blank and
full-line `#` comments. Split each entry at the first `=`. Keys must match
`[A-Za-z_][A-Za-z0-9_]*`. Unquoted values are stripped. Double-quoted values
support `\\n`, `\\t`, `\\"`, and `\\\\`; single-quoted values are literal.
Raise ValueError for malformed entries or duplicate keys.""",
        '''\
def parse_env(text):
    """Parse a small, deterministic .env subset."""
    raise NotImplementedError
''',
        '''\
from solution import parse_env

assert parse_env("A=1\\nB=two") == {"A": "1", "B": "two"}
print("smoke ok")
''',
        r'''
    from solution import parse_env
    text = """
    a = unquoted
    # comment
    B="line\\nquote\\"slash\\\\"
    C='literal\\n'

    """
    check(
        "values",
        parse_env(text),
        {"a": "unquoted", "B": 'line\nquote"slash\\', "C": r"literal\n"},
    )
    raises(ValueError, lambda: parse_env("A="))
    raises(ValueError, lambda: parse_env("1A=x"))
    raises(ValueError, lambda: parse_env("A=x\nA=y"))
    raises(ValueError, lambda: parse_env('A="x" trailing'))
''',
    ),
    py_task(
        "semver",
        "strings and parsing",
        """Implement `parse_semver(text)` and `compare_versions(a, b)`.
`parse_semver` returns `(major, minor, patch, prerelease_tuple)`.
Support `major.minor.patch` plus optional dot-separated prerelease identifiers.
A version with no prerelease sorts after the same version with prerelease.
Numeric prerelease identifiers compare numerically and sort before nonnumeric
ones; compare identifiers left to right. Raise ValueError for malformed input.
Build metadata after `+` must be ignored for precedence.""",
        '''\
def parse_semver(text):
    raise NotImplementedError


def compare_versions(a, b):
    raise NotImplementedError
''',
        '''\
from solution import compare_versions

assert compare_versions("1.2.3", "1.3.0") == -1
print("smoke ok")
''',
        '''\
    from solution import compare_versions, parse_semver
    check("parse", parse_semver("1.2.3-alpha.4+build.9")[:3], (1, 2, 3))
    check("release newer", compare_versions("1.0.0", "1.0.0-rc.1"), 1)
    check("numeric", compare_versions("1.0.0-alpha.2", "1.0.0-alpha.10"), -1)
    check("numeric before text", compare_versions("1.0.0-alpha.1", "1.0.0-alpha.beta"), -1)
    check("short list", compare_versions("1.0.0-alpha", "1.0.0-alpha.1"), -1)
    check("metadata", compare_versions("1.0.0+a", "1.0.0+b"), 0)
    raises(ValueError, lambda: parse_semver("1.2"))
    raises(ValueError, lambda: parse_semver("01.2.3"))
''',
    ),
    py_task(
        "url_query",
        "strings and parsing",
        """Implement `parse_query(query)` returning a list of `(key, value)`
tuples in original order, including repeated keys and blank values. Decode
percent escapes and `+` as spaces. Implement `build_query(pairs)` with
`urllib.parse.quote_plus`, preserving order. Invalid input types raise
TypeError.""",
        '''\
def parse_query(query):
    raise NotImplementedError


def build_query(pairs):
    raise NotImplementedError
''',
        '''\
from solution import parse_query

assert parse_query("a=1&a=2") == [("a", "1"), ("a", "2")]
print("smoke ok")
''',
        '''\
    from solution import build_query, parse_query
    check(
        "parse",
        parse_query("a=1&a=2&name=hello+world&empty=&flag"),
        [("a", "1"), ("a", "2"), ("name", "hello world"), ("empty", ""), ("flag", "")],
    )
    check("unicode", parse_query("q=%E4%BD%A0%E5%A5%BD"), [("q", "你好")])
    check("empty", parse_query(""), [])
    pairs = [("a", "x y"), ("a", "+/?"), ("empty", "")]
    check("roundtrip", parse_query(build_query(pairs)), pairs)
    raises(TypeError, lambda: parse_query(None))
    raises(TypeError, lambda: build_query([("a", 1)]))
''',
    ),
    py_task(
        "unicode_slug",
        "strings and parsing",
        """Implement `slugify(text)` using Unicode NFKC normalization and
lowercasing. Keep Unicode letters and digits, convert maximal runs of other
characters to one ASCII hyphen, and strip leading/trailing hyphens. Empty or
punctuation-only input returns an empty string. Raise TypeError for non-string
input.""",
        '''\
def slugify(text):
    raise NotImplementedError
''',
        '''\
from solution import slugify

assert slugify("Hello, World!") == "hello-world"
print("smoke ok")
''',
        '''\
    from solution import slugify
    check("ascii", slugify("  Hello,   World!  "), "hello-world")
    check("unicode", slugify("ＡＢＣ 你好"), "abc-你好")
    check("combining", slugify("Cafe\\u0301"), "café")
    check("punct", slugify("---"), "")
    check("empty", slugify(""), "")
    raises(TypeError, lambda: slugify(None))
''',
    ),
]

TASKS.extend(
    [
        py_task(
            "safe_join",
            "security",
            """Implement `safe_join(root, *parts)` returning a resolved Path.
Reject absolute parts and any `..` component. Resolve symlinks and verify the
final target remains under the resolved root. Violations raise ValueError.
Reject non-string parts with TypeError.""",
            '''\
from pathlib import Path


def safe_join(root, *parts):
    raise NotImplementedError
''',
            '''\
from pathlib import Path
from solution import safe_join

root = Path.cwd() / "data"
assert safe_join(root, "a", "b") == (root / "a" / "b").resolve()
print("smoke ok")
''',
            '''\
    import tempfile
    from pathlib import Path
    from solution import safe_join
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "root"
        root.mkdir()
        check("join", safe_join(root, "a", "b.txt"), (root / "a" / "b.txt").resolve())
        check("inside dots", safe_join(root, "a", ".hidden"), (root / "a" / ".hidden").resolve())
        raises(ValueError, lambda: safe_join(root, "..", "outside"))
        raises(ValueError, lambda: safe_join(root, str(root.resolve())))
        raises(TypeError, lambda: safe_join(root, 3))
''',
        ),
        py_task(
            "secret_redaction",
            "security",
            """Implement `redact(value)` recursively. For mappings, replace the
value with `"[REDACTED]"` when the lowercased key contains `password`,
`passwd`, `secret`, `token`, `api_key`, or `authorization`. Preserve all other
values and container types. Strings under ordinary keys remain unchanged.
The input must not be mutated.""",
            '''\
def redact(value):
    raise NotImplementedError
''',
            '''\
from solution import redact

assert redact({"token": "x", "name": "ok"}) == {"token": "[REDACTED]", "name": "ok"}
print("smoke ok")
''',
            '''\
    from solution import redact
    source = {
        "name": "service",
        "db": {"Password": "p", "host": "localhost"},
        "items": [{"api_key": "k", "value": 3}],
        "AUTHORIZATION": "Bearer z",
        "note": "token text outside a sensitive key",
    }
    expected = {
        "name": "service",
        "db": {"Password": "[REDACTED]", "host": "localhost"},
        "items": [{"api_key": "[REDACTED]", "value": 3}],
        "AUTHORIZATION": "[REDACTED]",
        "note": "token text outside a sensitive key",
    }
    check("redact", redact(source), expected)
    check("input unchanged", source["db"]["Password"], "p")
    check("tuple", redact(({"secret_value": 1}, "x")), ({"secret_value": "[REDACTED]"}, "x"))
''',
        ),
        py_task(
            "money_allocation",
            "domain and numeric",
            """Implement `allocate_money(total, weights)` returning two-decimal
strings. `total` is a decimal string with at most two fractional digits; all
weights are positive integers. Allocate integer cents proportionally, then
assign remaining cents by largest fractional remainder, breaking ties by
original index. Reject invalid inputs with ValueError. Decimal arithmetic only.""",
            '''\
def allocate_money(total, weights):
    raise NotImplementedError
''',
            '''\
from solution import allocate_money

assert allocate_money("10.00", [1, 1]) == ["5.00", "5.00"]
print("smoke ok")
''',
            '''\
    from solution import allocate_money
    check("uneven", allocate_money("100.00", [1, 1, 1]), ["33.34", "33.33", "33.33"])
    check("cents", allocate_money("0.05", [1, 1]), ["0.03", "0.02"])
    check("zero", allocate_money("0.00", [2, 1]), ["0.00", "0.00"])
    check("weighted", allocate_money("10.00", [1, 2]), ["3.33", "6.67"])
    raises(ValueError, lambda: allocate_money("1.001", [1]))
    raises(ValueError, lambda: allocate_money("-1.00", [1]))
    raises(ValueError, lambda: allocate_money("1.00", [1, 0]))
    raises(ValueError, lambda: allocate_money("1.00", []))
''',
        ),
        py_task(
            "duration_format",
            "domain and numeric",
            """Implement `parse_duration(text)` and `format_duration(seconds)`.
Parse combinations such as `1w2d3h4m5s`; units are w,d,h,m,s, each at most
once, in that descending order. Return integer seconds. Format positive
integer seconds with the largest units, omit zero components, and join with a
single space. Zero formats as `"0s"`. Reject negative/non-integer seconds and
malformed durations.""",
            '''\
def parse_duration(text):
    raise NotImplementedError


def format_duration(seconds):
    raise NotImplementedError
''',
            '''\
from solution import format_duration, parse_duration

assert parse_duration("1h30m") == 5400
assert format_duration(5400) == "1h 30m"
print("smoke ok")
''',
            '''\
    from solution import format_duration, parse_duration
    check("parse all", parse_duration("1w2d3h4m5s"), 788645)
    check("parse spaces", parse_duration(" 2h 5m "), 7500)
    check("format", format_duration(788645), "1w 2d 3h 4m 5s")
    check("zero", format_duration(0), "0s")
    check("minute", format_duration(60), "1m")
    raises(ValueError, lambda: parse_duration("1h1h"))
    raises(ValueError, lambda: parse_duration("1m1h"))
    raises(ValueError, lambda: parse_duration("-1s"))
    raises(ValueError, lambda: format_duration(-1))
    raises(TypeError, lambda: format_duration(1.2))
''',
        ),
        py_task(
            "business_day",
            "date and time",
            """Implement `next_business_day(dt, holidays=())`. Accept only a
timezone-aware datetime. Add one calendar day repeatedly until the date is
Monday-Friday and not in the set of holiday `date` objects. Preserve clock time
and timezone. Return a datetime. Raised errors should be ValueError or
TypeError for invalid input.""",
            '''\
def next_business_day(dt, holidays=()):
    raise NotImplementedError
''',
            '''\
from datetime import datetime, timezone
from solution import next_business_day

dt = datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc)
assert next_business_day(dt) == datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)
print("smoke ok")
''',
            '''\
    from datetime import date, datetime, timedelta, timezone
    from solution import next_business_day
    tz = timezone(timedelta(hours=8))
    friday = datetime(2026, 10, 23, 9, 30, tzinfo=tz)
    check("weekend", next_business_day(friday), datetime(2026, 10, 26, 9, 30, tzinfo=tz))
    check(
        "holiday",
        next_business_day(friday, {date(2026, 10, 26)}),
        datetime(2026, 10, 27, 9, 30, tzinfo=tz),
    )
    raises(ValueError, lambda: next_business_day(datetime(2026, 10, 23)))
    raises(TypeError, lambda: next_business_day("2026-10-23"))
''',
        ),
        py_task(
            "deep_merge",
            "configuration",
            """Implement `deep_merge(base, override)` for nested dictionaries.
Merge recursively, replace non-dict values (including lists) wholesale, and
never mutate either input. Return a new dictionary. Raise TypeError when either
top-level argument is not a dict.""",
            '''\
def deep_merge(base, override):
    raise NotImplementedError
''',
            '''\
from solution import deep_merge

assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}
print("smoke ok")
''',
            '''\
    from solution import deep_merge
    base = {"a": {"x": 1, "y": [1]}, "b": 2, "list": [1, 2]}
    override = {"a": {"y": [3], "z": 4}, "list": [9], "new": True}
    expected = {"a": {"x": 1, "y": [3], "z": 4}, "b": 2, "list": [9], "new": True}
    check("merge", deep_merge(base, override), expected)
    check("base unchanged", base, {"a": {"x": 1, "y": [1]}, "b": 2, "list": [1, 2]})
    check("override unchanged", override["a"], {"y": [3], "z": 4})
    raises(TypeError, lambda: deep_merge([], {}))
    raises(TypeError, lambda: deep_merge({}, []))
''',
        ),
        py_task(
            "json_store",
            "files and persistence",
            """Implement `JsonStore(path)` with `save(data)` and
`load(default=None)`. Save compact UTF-8 JSON through a temporary file in the
same directory followed by `os.replace`; create parent directories. A missing
file returns `default`. Invalid existing JSON raises `StoreError` (a ValueError
subclass). `StoreError` is already defined in solution.py.""",
            '''\
class StoreError(ValueError):
    pass


class JsonStore:
    def __init__(self, path):
        raise NotImplementedError

    def save(self, data):
        raise NotImplementedError

    def load(self, default=None):
        raise NotImplementedError
''',
            '''\
import tempfile
from pathlib import Path
from solution import JsonStore

with tempfile.TemporaryDirectory() as temp:
    store = JsonStore(Path(temp) / "data.json")
    store.save({"ok": True})
    assert store.load() == {"ok": True}
print("smoke ok")
''',
            '''\
    import tempfile
    from pathlib import Path
    from solution import JsonStore, StoreError
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "nested" / "data.json"
        store = JsonStore(path)
        check("missing", store.load("fallback"), "fallback")
        store.save({"unicode": "你好", "items": [1, 2]})
        check("roundtrip", store.load(), {"unicode": "你好", "items": [1, 2]})
        path.write_text("{broken", encoding="utf-8")
        raises(StoreError, lambda: store.load())
        check("temp cleanup", list(path.parent.glob(".data.json.*")), [])
''',
        ),
        py_task(
            "log_rotation",
            "files and persistence",
            """Implement `rotate_log(path, max_bytes, backups)`. If the file is
missing, do nothing and return False. If its size is at most max_bytes, do
nothing and return False. Otherwise shift `path.1` -> `path.2`, ... up to
`backups`, move `path` to `path.1`, create an empty `path`, and return True.
When backups == 0, remove the oversized log and create an empty file. Reject
max_bytes <= 0 and backups < 0.""",
            '''\
from pathlib import Path


def rotate_log(path, max_bytes, backups):
    raise NotImplementedError
''',
            '''\
import tempfile
from pathlib import Path
from solution import rotate_log

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "app.log"
    path.write_text("abc", encoding="utf-8")
    assert rotate_log(path, 2, 1) is True
print("smoke ok")
''',
            '''\
    import tempfile
    from pathlib import Path
    from solution import rotate_log
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "app.log"
        check("missing", rotate_log(path, 2, 2), False)
        path.write_text("old", encoding="utf-8")
        Path(str(path) + ".1").write_text("older", encoding="utf-8")
        check("rotate", rotate_log(path, 2, 2), True)
        check("new empty", path.read_text(encoding="utf-8"), "")
        check("current backup", Path(str(path) + ".1").read_text(encoding="utf-8"), "old")
        check("old backup", Path(str(path) + ".2").read_text(encoding="utf-8"), "older")
        path.write_text("x", encoding="utf-8")
        check("no rotate", rotate_log(path, 10, 2), False)
        raises(ValueError, lambda: rotate_log(path, 0, 2))
        raises(ValueError, lambda: rotate_log(path, 2, -1))
''',
        ),
        py_task(
            "sqlite_inventory",
            "database",
            """Implement `init_db(path)`, `set_stock(path, sku, quantity)`,
`adjust_stock(path, sku, delta)`, and `list_stock(path)` using sqlite3.
Quantity cannot become negative; adjustment must be atomic and raise ValueError
without changing data. SKUs are unique text; list returns sorted `(sku, qty)`
tuples. Missing SKU adjustment raises KeyError. Use parameterized SQL and close
every database connection before returning.""",
            '''\
def init_db(path):
    raise NotImplementedError


def set_stock(path, sku, quantity):
    raise NotImplementedError


def adjust_stock(path, sku, delta):
    raise NotImplementedError


def list_stock(path):
    raise NotImplementedError
''',
            '''\
import tempfile
from pathlib import Path
from solution import adjust_stock, init_db, list_stock, set_stock

with tempfile.TemporaryDirectory() as temp:
    db = Path(temp) / "inventory.db"
    init_db(db)
    set_stock(db, "apple", 2)
    adjust_stock(db, "apple", 3)
    assert list_stock(db) == [("apple", 5)]
print("smoke ok")
''',
            '''\
    import sqlite3
    import tempfile
    from pathlib import Path
    from solution import adjust_stock, init_db, list_stock, set_stock
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / "inventory.db"
        init_db(db)
        init_db(db)
        set_stock(db, "b", 5)
        set_stock(db, "a", 1)
        set_stock(db, "b", 7)
        adjust_stock(db, "b", -2)
        check("list", list_stock(db), [("a", 1), ("b", 5)])
        raises(ValueError, lambda: adjust_stock(db, "b", -6))
        check("unchanged", list_stock(db), [("a", 1), ("b", 5)])
        raises(KeyError, lambda: adjust_stock(db, "missing", 1))
        raises(ValueError, lambda: set_stock(db, "bad", -1))
''',
        ),
        py_task(
            "thread_counter",
            "concurrency",
            """Implement `ThreadSafeCounter` with `increment(delta=1)` and
`value()`. Also implement `increment_if_below(limit, delta=1)` which atomically
increments only when the resulting value would be <= limit and returns whether
it changed. It must remain correct under many concurrent threads.""",
            '''\
import threading


class ThreadSafeCounter:
    def __init__(self):
        raise NotImplementedError

    def increment(self, delta=1):
        raise NotImplementedError

    def value(self):
        raise NotImplementedError

    def increment_if_below(self, limit, delta=1):
        raise NotImplementedError
''',
            '''\
from solution import ThreadSafeCounter

counter = ThreadSafeCounter()
counter.increment()
assert counter.value() == 1
print("smoke ok")
''',
            '''\
    from concurrent.futures import ThreadPoolExecutor
    from solution import ThreadSafeCounter
    counter = ThreadSafeCounter()
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: counter.increment(), range(2000)))
    check("parallel increments", counter.value(), 2000)
    bounded = ThreadSafeCounter()
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: bounded.increment_if_below(250), range(800)))
    check("bounded", bounded.value(), 250)
    check("true count", sum(results), 250)
    counter.increment(5)
    check("delta", counter.value(), 2005)
''',
        ),
    ]
)

TASKS.extend(
    [
        py_task(
            "async_map_limited",
            "concurrency",
            """Implement `async map_limited(items, worker, limit)` where worker is
an async callable. Preserve input order, run at most `limit` workers at once,
and propagate the first exception while awaiting all started tasks. Reject
limit < 1. Do not depend on `asyncio.TaskGroup` so Python 3.10 remains supported.""",
            '''\
async def map_limited(items, worker, limit):
    raise NotImplementedError
''',
            '''\
import asyncio
from solution import map_limited

result = asyncio.run(map_limited([1, 2], lambda x: asyncio.sleep(0, result=x * 2), 1))
assert result == [2, 4]
print("smoke ok")
''',
            '''\
    import asyncio
    from solution import map_limited

    async def scenario():
        active = 0
        maximum = 0
        lock = asyncio.Lock()
        async def worker(value):
            nonlocal active, maximum
            async with lock:
                active += 1
                maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1
            return value * value
        result = await map_limited(list(range(12)), worker, 3)
        return result, maximum

    result, maximum = asyncio.run(scenario())
    check("ordered", result, [x * x for x in range(12)])
    check("limit", maximum, 3)
    raises(ValueError, lambda: asyncio.run(map_limited([], lambda x: x, 0)))
''',
        ),
        py_task(
            "async_retry",
            "concurrency and reliability",
            """Implement
`async retry_async(operation, attempts=3, base_delay=0.1, factor=2.0, sleep=None)`.
Call the async operation up to `attempts` times. On failure, await the injected
async sleep with delay `base_delay * factor**retry_index`, then retry. If all
attempts fail, re-raise the last exception. Validate arguments and support a
custom sleep coroutine function for deterministic tests.""",
            '''\
async def retry_async(operation, attempts=3, base_delay=0.1, factor=2.0, sleep=None):
    raise NotImplementedError
''',
            '''\
import asyncio
from solution import retry_async

async def op():
    return 7

assert asyncio.run(retry_async(op, attempts=1)) == 7
print("smoke ok")
''',
            '''\
    import asyncio
    from solution import retry_async

    async def success():
        delays = []
        calls = 0
        async def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise OSError("temporary")
            return "ok"
        async def fake_sleep(delay):
            delays.append(delay)
        result = await retry_async(operation, 4, 0.5, 2.0, fake_sleep)
        return result, calls, delays

    result, calls, delays = asyncio.run(success())
    check("result", result, "ok")
    check("calls", calls, 3)
    check("delays", delays, [0.5, 1.0])

    async def failure():
        calls = 0
        async def operation():
            nonlocal calls
            calls += 1
            raise RuntimeError(f"fail-{calls}")
        async def fake_sleep(delay):
            return None
        try:
            await retry_async(operation, 2, 0.1, 2, fake_sleep)
        except RuntimeError as exc:
            return calls, str(exc)
        raise AssertionError("expected failure")

    check("last error", asyncio.run(failure()), (2, "fail-2"))
    raises(ValueError, lambda: asyncio.run(retry_async(lambda: None, 0)))
''',
        ),
        py_task(
            "sliding_window_bugfix",
            "debugging and performance",
            """Fix `max_sum_fixed(values, k)` in solution.py. It must return the
maximum sum of any contiguous window of exactly k values in O(n) time. Raise
ValueError for k <= 0 or k > len(values). Empty input is valid only when k is
invalid. Do not replace it with an O(n*k) implementation and do not edit smoke.py.""",
            '''\
def max_sum_fixed(values, k):
    if k <= 0 or k > len(values):
        raise ValueError("invalid window")
    window = sum(values[:k])
    best = window
    for right in range(k, len(values)):
        window += values[right]
        best = max(best, window)
    return best
''',
            '''\
from solution import max_sum_fixed

assert max_sum_fixed([1, 2, 3, 4], 2) == 7
print("smoke ok")
''',
            '''\
    import random
    import time
    from solution import max_sum_fixed
    check("simple", max_sum_fixed([1, 2, 3, 4], 2), 7)
    check("negative", max_sum_fixed([-5, 2, 3, -1, 4], 2), 5)
    check("whole", max_sum_fixed([1, 2, 3], 3), 6)
    raises(ValueError, lambda: max_sum_fixed([], 1))
    raises(ValueError, lambda: max_sum_fixed([1], 0))
    values = [random.Random(7).randint(-5, 9) for _ in range(250000)]
    window = sum(values[:17])
    expected = window
    for index in range(17, len(values)):
        window += values[index] - values[index - 17]
        expected = max(expected, window)
    started = time.perf_counter()
    actual = max_sum_fixed(values, 17)
    check("large", actual, expected)
    assert time.perf_counter() - started < 3.0
''',
        ),
        py_task(
            "arithmetic_parser",
            "parsing and algorithms",
            """Implement `evaluate(expression)` for decimal numbers, unary plus
and minus, parentheses, and left-associative +, -, *, /. Return a float.
Whitespace is ignored. Raise ValueError for invalid syntax and ZeroDivisionError
for division by zero. Do not use eval().""",
            '''\
def evaluate(expression):
    raise NotImplementedError
''',
            '''\
from solution import evaluate

assert evaluate("2 + 3 * 4") == 14.0
print("smoke ok")
''',
            '''\
    from solution import evaluate
    check("precedence", evaluate("2 + 3 * 4"), 14.0)
    check("parens", evaluate("(2 + 3) * 4"), 20.0)
    check("left assoc", evaluate("20 / 5 / 2"), 2.0)
    check("unary", evaluate("-2 * -(3 + 1)"), 8.0)
    check("decimal", evaluate(" 1.5 + 2.25 "), 3.75)
    raises(ZeroDivisionError, lambda: evaluate("1 / (2 - 2)"))
    raises(ValueError, lambda: evaluate("1 +"))
    raises(ValueError, lambda: evaluate("(1 + 2"))
    raises(ValueError, lambda: evaluate("2 ** 3"))
''',
        ),
        py_task(
            "schema_validation",
            "validation",
            """Implement `validate(data, schema)` returning a list of error strings.
The schema maps field names to rules: type (`str`,`int`,`float`,`bool`,`list`,
`dict`), required (default False), min, max, min_len, and choices. Validate in
schema insertion order. Error format is `field: required`, `field: expected
<type>`, `field: below minimum`, `field: above maximum`, `field: too short`, or
`field: invalid choice`. Treat bool as distinct from int. A non-dict input
returns `["$: expected object"]`.""",
            '''\
def validate(data, schema):
    raise NotImplementedError
''',
            '''\
from solution import validate

assert validate({"name": "ok"}, {"name": {"type": "str"}}) == []
print("smoke ok")
''',
            '''\
    from solution import validate
    schema = {
        "name": {"type": "str", "required": True, "min_len": 3},
        "age": {"type": "int", "min": 18, "max": 99},
        "role": {"type": "str", "choices": ["admin", "user"]},
    }
    check("valid", validate({"name": "Ada", "age": 30, "role": "admin"}, schema), [])
    check(
        "multiple",
        validate({"age": 17, "role": "guest"}, schema),
        ["name: required", "age: below minimum", "role: invalid choice"],
    )
    check("bool not int", validate({"name": "Ada", "age": True}, schema), ["age: expected int"])
    check("short", validate({"name": "A"}, schema), ["name: too short"])
    check("root", validate([], schema), ["$: expected object"])
''',
        ),
        py_task(
            "cursor_codec",
            "security and API design",
            """Implement `CursorCodec(secret)`. Use compact JSON, URL-safe base64
without padding, and an HMAC-SHA256 signature over the payload. `encode(payload)`
returns a string. `decode(token, max_age=None, now=None)` returns the original
dict and raises ValueError for invalid/tampered/expired tokens. `now` and
`max_age` are integer seconds; expire when age > max_age. Reject an empty secret
and payloads that cannot be JSON encoded.""",
            '''\
class CursorCodec:
    def __init__(self, secret):
        raise NotImplementedError

    def encode(self, payload):
        raise NotImplementedError

    def decode(self, token, max_age=None, now=None):
        raise NotImplementedError
''',
            '''\
from solution import CursorCodec

codec = CursorCodec("test-secret")
token = codec.encode({"page": 2})
assert codec.decode(token) == {"page": 2}
print("smoke ok")
''',
            '''\
    from solution import CursorCodec
    codec = CursorCodec("test-secret")
    payload = {"page": 2, "q": "你好", "items": [1, 2]}
    import time
    token = codec.encode(payload)
    now = int(time.time())
    check("roundtrip", codec.decode(token), payload)
    check("urlsafe", all(char not in token for char in "/+="), True)
    middle = len(token) // 2
    tampered = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1:]
    raises(ValueError, lambda: codec.decode(tampered))
    raises(ValueError, lambda: CursorCodec("").encode({}))
    check("fresh", codec.decode(token, max_age=30, now=now), payload)
    raises(ValueError, lambda: codec.decode(token, max_age=30, now=now + 100))
''',
        ),
        py_task(
            "wordcount_cli",
            "CLI integration",
            """Implement cli.py so `python cli.py FILE --json` prints one compact
JSON object with integer `lines`, `words`, and `bytes`. Read UTF-8 text;
`lines` is `len(text.splitlines())`, `words` is `len(text.split())`, and bytes
is the UTF-8 byte length. On errors, print a JSON object with `error` to stderr
and exit nonzero. Use argparse. Do not edit smoke.py.""",
            '''\
import argparse


def main():
    raise NotImplementedError


if __name__ == "__main__":
    main()
''',
            '''\
import subprocess
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "sample.txt"
    path.write_text("hello world\\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "cli.py", str(path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
print("smoke ok")
''',
            '''\
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "sample.txt"
        path.write_text("hello world\\n你好\\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "cli.py", str(path), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        check("exit", completed.returncode, 0)
        check(
            "json",
            json.loads(completed.stdout),
            {"lines": 2, "words": 3, "bytes": len("hello world\\n你好\\n".encode("utf-8"))},
        )
        missing = subprocess.run(
            [sys.executable, "cli.py", str(Path(temp) / "missing"), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert missing.returncode != 0
        assert "error" in json.loads(missing.stderr)
''',
        ),
        CodingTask(
            id="js_cart_pricing",
            category="JavaScript and domain logic",
            prompt="""Implement `calculateCart(items, options={})` in solution.js.
Each item has nonnegative integer `priceCents`, positive integer `quantity`,
and optional `taxable` (default true). Options allow nonnegative integer
`discountCents` and `taxBps`. The discount is capped at subtotal and reduces
the taxable base first, capped at taxable subtotal. Return integer
`subtotalCents`, `discountCents`, `taxableCents`, `taxCents` (rounded), and
`totalCents`. Reject invalid values by throwing TypeError or RangeError.
Run `node smoke.js`.""",
            files={
                "solution.js": '''\
function calculateCart(items, options = {}) {
  throw new Error("Not implemented");
}

module.exports = { calculateCart };
''',
                "smoke.js": '''\
const assert = require("node:assert");
const { calculateCart } = require("./solution");

assert.deepStrictEqual(
  calculateCart([{ priceCents: 500, quantity: 2 }], { taxBps: 1000 }),
  {
    subtotalCents: 1000,
    discountCents: 0,
    taxableCents: 1000,
    taxCents: 100,
    totalCents: 1100,
  },
);
console.log("smoke ok");
''',
                "README.md": "# JavaScript cart pricing\n\nImplement the documented cart pricing rules.\n",
            },
            hidden_test='''\
const assert = require("node:assert");
const { calculateCart } = require("./solution");

const result = calculateCart(
  [
    { priceCents: 1000, quantity: 2, taxable: true },
    { priceCents: 500, quantity: 1, taxable: false },
  ],
  { discountCents: 300, taxBps: 1000 },
);
assert.deepStrictEqual(result, {
  subtotalCents: 2500,
  discountCents: 300,
  taxableCents: 1700,
  taxCents: 170,
  totalCents: 2370,
});
assert.throws(() => calculateCart([{ priceCents: -1, quantity: 1 }]));
assert.throws(() => calculateCart([], { taxBps: -1 }));
console.log("HIDDEN TESTS PASSED");
''',
            language="javascript",
            visible_command="node smoke.js",
        ),
        CodingTask(
            id="js_debounce",
            category="JavaScript and event handling",
            prompt="""Implement `debounce(fn, wait)` in solution.js. It returns a
function that postpones invocation until `wait` ms after the latest call,
using the latest arguments and `this`. The wrapper has `cancel()` and `flush()`.
`flush()` immediately invokes a pending call and returns its result; otherwise
returns undefined. `cancel()` prevents a pending call. Reject negative/nonfinite
wait and nonfunction fn. Do not use external packages. Run `node smoke.js`.""",
            files={
                "solution.js": '''\
function debounce(fn, wait) {
  throw new Error("Not implemented");
}

module.exports = { debounce };
''',
                "smoke.js": '''\
const assert = require("node:assert");
const { debounce } = require("./solution");

let calls = 0;
const wrapped = debounce(() => { calls += 1; }, 5);
wrapped();
assert.strictEqual(wrapped.flush(), undefined);
assert.strictEqual(calls, 1);
console.log("smoke ok");
''',
                "README.md": "# JavaScript debounce\n\nImplement trailing-edge debounce with cancel and flush.\n",
            },
            hidden_test='''\
const assert = require("node:assert");
const { debounce } = require("./solution");

(async () => {
  const calls = [];
  const target = {
    value: 10,
    run: debounce(function (x) { calls.push([this.value, x]); return x * 2; }, 20),
  };
  target.run(1);
  target.run(2);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepStrictEqual(calls, [[10, 2]]);

  target.run(3);
  assert.strictEqual(target.run.flush(), 6);
  assert.deepStrictEqual(calls, [[10, 2], [10, 3]]);
  assert.strictEqual(target.run.flush(), undefined);

  target.run(4);
  target.run.cancel();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.strictEqual(calls.length, 2);
  assert.throws(() => debounce(() => {}, -1));
  assert.throws(() => debounce("x", 1));
  console.log("HIDDEN TESTS PASSED");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
''',
            language="javascript",
            visible_command="node smoke.js",
        ),
        CodingTask(
            id="js_stable_stringify",
            category="JavaScript and serialization",
            prompt="""Implement `stableStringify(value)` in solution.js. Recursively
sort object keys lexicographically, preserve array order, and produce compact
JSON. Reject circular references and non-finite numbers. Convert undefined
object properties the same way JSON.stringify does (omit them), but undefined
array entries become null and a top-level undefined returns undefined. Do not
use external packages. Run `node smoke.js`.""",
            files={
                "solution.js": '''\
function stableStringify(value) {
  throw new Error("Not implemented");
}

module.exports = { stableStringify };
''',
                "smoke.js": '''\
const assert = require("node:assert");
const { stableStringify } = require("./solution");

assert.strictEqual(stableStringify({ b: 1, a: 2 }), '{"a":2,"b":1}');
console.log("smoke ok");
''',
                "README.md": "# Stable JSON stringify\n\nImplement deterministic compact JSON serialization.\n",
            },
            hidden_test='''\
const assert = require("node:assert");
const { stableStringify } = require("./solution");

assert.strictEqual(
  stableStringify({ z: { b: 2, a: 1 }, list: [{ y: 2, x: 1 }, 3] }),
  '{"list":[{"x":1,"y":2},3],"z":{"a":1,"b":2}}',
);
assert.strictEqual(stableStringify({ a: undefined, b: 1 }), '{"b":1}');
assert.strictEqual(stableStringify([undefined, 2]), '[null,2]');
assert.strictEqual(stableStringify(undefined), undefined);
assert.throws(() => stableStringify({ n: Infinity }));
const cyclic = {};
cyclic.self = cyclic;
assert.throws(() => stableStringify(cyclic));
console.log("HIDDEN TESTS PASSED");
''',
            language="javascript",
            visible_command="node smoke.js",
        ),
    ]
)

assert len(TASKS) == 30, f"expected 30 tasks, got {len(TASKS)}"
