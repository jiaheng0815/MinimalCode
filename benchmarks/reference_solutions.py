"""Reference implementations used only to validate benchmark graders."""

from __future__ import annotations

REFERENCES: dict[str, dict[str, str]] = {}

REFERENCES["merge_intervals"] = {
    "solution.py": '''\
def merge_intervals(intervals):
    values = []
    for start, end in intervals:
        if start > end:
            raise ValueError("invalid interval")
        values.append((start, end))
    values.sort()
    merged = []
    for start, end in values:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged
''',
}

REFERENCES["topological_sort"] = {
    "solution.py": '''\
import heapq


class CycleError(ValueError):
    pass


def topological_sort(nodes, edges):
    node_list = list(nodes)
    known = set(node_list)
    adjacency = {node: set() for node in node_list}
    indegree = {node: 0 for node in node_list}
    for before, after in edges:
        if before not in known or after not in known:
            raise CycleError("unknown node")
        if after not in adjacency[before]:
            adjacency[before].add(after)
            indegree[after] += 1
    ready = [node for node in node_list if indegree[node] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        node = heapq.heappop(ready)
        result.append(node)
        for after in adjacency[node]:
            indegree[after] -= 1
            if indegree[after] == 0:
                heapq.heappush(ready, after)
    if len(result) != len(node_list):
        raise CycleError("cycle")
    return result
''',
}

REFERENCES["dijkstra"] = {
    "solution.py": '''\
import heapq


def shortest_path(graph, start, end):
    if start not in graph or end not in graph:
        raise ValueError("unknown endpoint")
    for edges in graph.values():
        for _, weight in edges:
            if weight < 0:
                raise ValueError("negative weight")
    distance = {start: 0}
    previous = {}
    queue = [(0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distance.get(node):
            continue
        if node == end:
            break
        for neighbor, weight in graph.get(node, []):
            candidate = cost + weight
            if candidate < distance.get(neighbor, float("inf")):
                distance[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    if end not in distance:
        return None
    path = [end]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return distance[end], path
''',
}

REFERENCES["lru_cache"] = {
    "solution.py": '''\
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity")
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key, default=None):
        if key not in self.data:
            return default
        value = self.data.pop(key)
        self.data[key] = value
        return value

    def put(self, key, value):
        if key in self.data:
            self.data.pop(key)
        elif len(self.data) >= self.capacity:
            self.data.popitem(last=False)
        self.data[key] = value

    def keys(self):
        return list(reversed(self.data))
''',
}

REFERENCES["ring_buffer"] = {
    "solution.py": '''\
class RingBuffer:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity")
        self.items = []
        self.capacity = capacity

    def push(self, value):
        overwritten = None
        if len(self.items) == self.capacity:
            overwritten = self.items.pop(0)
        self.items.append(value)
        return overwritten

    def oldest(self, count):
        if count < 0 or count > len(self.items):
            raise ValueError("count")
        return self.items[:count]

    def snapshot(self):
        return list(self.items)
''',
}

REFERENCES["union_find"] = {
    "solution.py": '''\
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.sizes = [1] * n

    def _index(self, value):
        if not 0 <= value < len(self.parent):
            raise IndexError(value)

    def find(self, value):
        self._index(value)
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, a, b):
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return False
        if self.sizes[root_a] < self.sizes[root_b]:
            root_a, root_b = root_b, root_a
        self.parent[root_b] = root_a
        self.sizes[root_a] += self.sizes[root_b]
        return True

    def connected(self, a, b):
        return self.find(a) == self.find(b)

    def size(self, a):
        return self.sizes[self.find(a)]

    def components(self):
        groups = {}
        for item in range(len(self.parent)):
            groups.setdefault(self.find(item), []).append(item)
        return sorted(sorted(group) for group in groups.values())
''',
}

REFERENCES["env_parser"] = {
    "solution.py": '''\
import re

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _double_value(value):
    result = []
    index = 1
    escapes = {"n": "\\n", "t": "\\t", '"': '"', "\\\\": "\\\\"}
    while index < len(value):
        char = value[index]
        if char == "\\\\":
            index += 1
            if index >= len(value) or value[index] not in escapes:
                raise ValueError("bad escape")
            result.append(escapes[value[index]])
        elif char == '"':
            if value[index + 1:].strip():
                raise ValueError("trailing text")
            return "".join(result)
        else:
            result.append(char)
        index += 1
    raise ValueError("unterminated quote")


def _single_value(value):
    end = value.find("'", 1)
    if end < 0 or value[end + 1:].strip():
        raise ValueError("bad single quote")
    return value[1:end]


def parse_env(text):
    if not isinstance(text, str):
        raise TypeError("text")
    result = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("missing =")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY.fullmatch(key) or not value or key in result:
            raise ValueError("invalid entry")
        if value.startswith('"'):
            value = _double_value(value)
        elif value.startswith("'"):
            value = _single_value(value)
        result[key] = value
    return result
''',
}

REFERENCES["semver"] = {
    "solution.py": '''\
import re

PATTERN = re.compile(
    r"^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*))?"
    r"(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$"
)


def parse_semver(text):
    if not isinstance(text, str):
        raise TypeError("version")
    match = PATTERN.fullmatch(text)
    if not match:
        raise ValueError("invalid version")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    for part in prerelease:
        if part.isdigit() and len(part) > 1 and part.startswith("0"):
            raise ValueError("leading zero")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def compare_versions(a, b):
    left = parse_semver(a)
    right = parse_semver(b)
    if left[:3] != right[:3]:
        return (left[:3] > right[:3]) - (left[:3] < right[:3])
    la, rb = left[3], right[3]
    if not la or not rb:
        return (not la) - (not rb)
    for x, y in zip(la, rb):
        if x == y:
            continue
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            return (int(x) > int(y)) - (int(x) < int(y))
        if x_num != y_num:
            return -1 if x_num else 1
        return (x > y) - (x < y)
    return (len(la) > len(rb)) - (len(la) < len(rb))
''',
}

REFERENCES["url_query"] = {
    "solution.py": '''\
from urllib.parse import quote_plus, unquote_plus


def parse_query(query):
    if not isinstance(query, str):
        raise TypeError("query")
    if not query:
        return []
    result = []
    for part in query.split("&"):
        key, separator, value = part.partition("=")
        result.append((unquote_plus(key), unquote_plus(value if separator else "")))
    return result


def build_query(pairs):
    if not isinstance(pairs, (list, tuple)):
        raise TypeError("pairs")
    parts = []
    for key, value in pairs:
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("pairs")
        parts.append(quote_plus(key) + "=" + quote_plus(value))
    return "&".join(parts)
''',
}

REFERENCES["unicode_slug"] = {
    "solution.py": '''\
import re
import unicodedata


def slugify(text):
    if not isinstance(text, str):
        raise TypeError("text")
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\\w]+", "-", text, flags=re.UNICODE)
    return text.strip("-_").replace("_", "-")
''',
}

REFERENCES["safe_join"] = {
    "solution.py": '''\
from pathlib import Path


def safe_join(root, *parts):
    root_path = Path(root).resolve()
    for part in parts:
        if not isinstance(part, str):
            raise TypeError("path part")
        candidate = Path(part)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("unsafe path")
    target = root_path.joinpath(*parts).resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("outside root") from exc
    return target
''',
}

REFERENCES["secret_redaction"] = {
    "solution.py": '''\
SENSITIVE = ("password", "passwd", "secret", "token", "api_key", "authorization")


def redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and any(word in key.lower() for word in SENSITIVE):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
''',
}

REFERENCES["money_allocation"] = {
    "solution.py": '''\
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def allocate_money(total, weights):
    try:
        amount = Decimal(str(total))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("total") from exc
    if amount < 0 or not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise ValueError("total")
    if not isinstance(weights, list) or not weights:
        raise ValueError("weights")
    if any(not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0 for weight in weights):
        raise ValueError("weights")
    cents = int(amount * 100)
    denominator = sum(weights)
    bases = [cents * weight // denominator for weight in weights]
    remainders = [cents * weight % denominator for weight in weights]
    remaining = cents - sum(bases)
    order = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in order[:remaining]:
        bases[index] += 1
    return [f"{value // 100}.{value % 100:02d}" for value in bases]
''',
}

REFERENCES["duration_format"] = {
    "solution.py": '''\
import re

UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
ORDER = "wdhms"


def parse_duration(text):
    if not isinstance(text, str):
        raise TypeError("text")
    compact = "".join(text.split())
    if not compact or not re.fullmatch(r"(?:\\d+[wdhms])+", compact):
        raise ValueError("duration")
    seen = []
    total = 0
    for value, unit in re.findall(r"(\\d+)([wdhms])", compact):
        seen.append(ORDER.index(unit))
        total += int(value) * UNITS[unit]
    if seen != sorted(set(seen)):
        raise ValueError("unit order")
    return total


def format_duration(seconds):
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise TypeError("seconds")
    if seconds < 0:
        raise ValueError("seconds")
    if seconds == 0:
        return "0s"
    parts = []
    remaining = seconds
    for unit in ORDER:
        value, remaining = divmod(remaining, UNITS[unit])
        if value:
            parts.append(f"{value}{unit}")
    return " ".join(parts)
''',
}

REFERENCES["business_day"] = {
    "solution.py": '''\
from datetime import datetime, timedelta


def next_business_day(dt, holidays=()):
    if not isinstance(dt, datetime):
        raise TypeError("dt")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone required")
    holidays = set(holidays)
    candidate = dt + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.date() in holidays:
        candidate += timedelta(days=1)
    return candidate
''',
}

REFERENCES["deep_merge"] = {
    "solution.py": '''\
import copy


def deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise TypeError("dictionaries required")
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
''',
}

REFERENCES["json_store"] = {
    "solution.py": '''\
import json
import os
import tempfile
from pathlib import Path


class StoreError(ValueError):
    pass


class JsonStore:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def load(self, default=None):
        if not self.path.exists():
            return default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(str(exc)) from exc
''',
}

REFERENCES["log_rotation"] = {
    "solution.py": '''\
from pathlib import Path


def rotate_log(path, max_bytes, backups):
    if max_bytes <= 0:
        raise ValueError("max_bytes")
    if backups < 0:
        raise ValueError("backups")
    path = Path(path)
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    if backups == 0:
        path.unlink()
        path.touch()
        return True
    for number in range(backups, 1, -1):
        source = Path(str(path) + f".{number - 1}")
        destination = Path(str(path) + f".{number}")
        if source.exists():
            destination.unlink(missing_ok=True)
            source.replace(destination)
    Path(str(path) + ".1").unlink(missing_ok=True)
    path.replace(Path(str(path) + ".1"))
    path.touch()
    return True
''',
}

REFERENCES["sqlite_inventory"] = {
    "solution.py": '''\
import sqlite3
from contextlib import closing
from pathlib import Path


def _connect(path):
    return sqlite3.connect(Path(path))


def init_db(path):
    with closing(_connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS inventory "
            "(sku TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0))"
        )


def set_stock(path, sku, quantity):
    if not isinstance(quantity, int) or quantity < 0:
        raise ValueError("quantity")
    init_db(path)
    with closing(_connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO inventory(sku, quantity) VALUES(?, ?) "
            "ON CONFLICT(sku) DO UPDATE SET quantity=excluded.quantity",
            (str(sku), quantity),
        )


def adjust_stock(path, sku, delta):
    init_db(path)
    with closing(_connect(path)) as connection, connection:
        row = connection.execute(
            "SELECT quantity FROM inventory WHERE sku=?", (str(sku),)
        ).fetchone()
        if row is None:
            raise KeyError(sku)
        updated = row[0] + delta
        if updated < 0:
            raise ValueError("negative stock")
        connection.execute(
            "UPDATE inventory SET quantity=? WHERE sku=?", (updated, str(sku))
        )


def list_stock(path):
    init_db(path)
    with closing(_connect(path)) as connection, connection:
        return connection.execute(
            "SELECT sku, quantity FROM inventory ORDER BY sku"
        ).fetchall()
''',
}

REFERENCES["thread_counter"] = {
    "solution.py": '''\
import threading


class ThreadSafeCounter:
    def __init__(self):
        self._lock = threading.Lock()
        self._value = 0

    def increment(self, delta=1):
        with self._lock:
            self._value += delta
        return self._value

    def value(self):
        with self._lock:
            return self._value

    def increment_if_below(self, limit, delta=1):
        with self._lock:
            if self._value + delta <= limit:
                self._value += delta
                return True
            return False
''',
}

REFERENCES["async_map_limited"] = {
    "solution.py": '''\
import asyncio


async def map_limited(items, worker, limit):
    if limit < 1:
        raise ValueError("limit")
    semaphore = asyncio.Semaphore(limit)

    async def run(item):
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(run(item) for item in items))
''',
}

REFERENCES["async_retry"] = {
    "solution.py": '''\
import asyncio


async def retry_async(operation, attempts=3, base_delay=0.1, factor=2.0, sleep=None):
    if attempts < 1:
        raise ValueError("attempts")
    if base_delay < 0 or factor <= 0:
        raise ValueError("delay")
    sleep = asyncio.sleep if sleep is None else sleep
    last = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                await sleep(base_delay * factor**attempt)
    raise last
''',
}

REFERENCES["sliding_window_bugfix"] = {
    "solution.py": '''\
def max_sum_fixed(values, k):
    if k <= 0 or k > len(values):
        raise ValueError("invalid window")
    window = sum(values[:k])
    best = window
    for index in range(k, len(values)):
        window += values[index] - values[index - k]
        if window > best:
            best = window
    return best
''',
}

REFERENCES["arithmetic_parser"] = {
    "solution.py": '''\
class Parser:
    def __init__(self, text):
        self.text = text
        self.index = 0

    def skip(self):
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def peek(self):
        self.skip()
        return self.text[self.index] if self.index < len(self.text) else ""

    def take(self):
        char = self.peek()
        if char:
            self.index += 1
        return char

    def number(self):
        self.skip()
        start = self.index
        dots = 0
        while self.index < len(self.text) and (
            self.text[self.index].isdigit() or self.text[self.index] == "."
        ):
            if self.text[self.index] == ".":
                dots += 1
                if dots > 1:
                    raise ValueError("number")
            self.index += 1
        try:
            return float(self.text[start:self.index])
        except ValueError as exc:
            raise ValueError("number") from exc

    def factor(self):
        char = self.peek()
        if char == "+":
            self.take()
            return self.factor()
        if char == "-":
            self.take()
            return -self.factor()
        if char == "(":
            self.take()
            value = self.expr()
            if self.take() != ")":
                raise ValueError("missing )")
            return value
        return self.number()

    def term(self):
        value = self.factor()
        while self.peek() in ("*", "/"):
            operator = self.take()
            right = self.factor()
            value = value * right if operator == "*" else value / right
        return value

    def expr(self):
        value = self.term()
        while self.peek() in ("+", "-"):
            operator = self.take()
            right = self.term()
            value = value + right if operator == "+" else value - right
        return value


def evaluate(expression):
    if not isinstance(expression, str):
        raise TypeError("expression")
    parser = Parser(expression)
    value = parser.expr()
    parser.skip()
    if parser.index != len(expression):
        raise ValueError("trailing input")
    return value
''',
}

REFERENCES["schema_validation"] = {
    "solution.py": '''\
TYPES = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict}


def validate(data, schema):
    if not isinstance(data, dict):
        return ["$: expected object"]
    errors = []
    for field, rules in schema.items():
        if field not in data:
            if rules.get("required"):
                errors.append(f"{field}: required")
            continue
        value = data[field]
        expected = rules.get("type")
        if expected:
            expected_type = TYPES[expected]
            valid = isinstance(value, expected_type)
            if expected in {"int", "float"} and isinstance(value, bool):
                valid = False
            if not valid:
                errors.append(f"{field}: expected {expected}")
                continue
        if "min" in rules and value < rules["min"]:
            errors.append(f"{field}: below minimum")
        if "max" in rules and value > rules["max"]:
            errors.append(f"{field}: above maximum")
        if "min_len" in rules and len(value) < rules["min_len"]:
            errors.append(f"{field}: too short")
        if "choices" in rules and value not in rules["choices"]:
            errors.append(f"{field}: invalid choice")
    return errors
''',
}

REFERENCES["cursor_codec"] = {
    "solution.py": '''\
import base64
import hashlib
import hmac
import json
import time


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CursorCodec:
    def __init__(self, secret):
        if not secret:
            raise ValueError("secret")
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)

    def encode(self, payload):
        body = {"payload": payload, "iat": int(time.time())}
        encoded = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        signature = _b64(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return encoded + "." + signature

    def decode(self, token, max_age=None, now=None):
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64(
                hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            body = json.loads(_unb64(encoded))
            payload = body["payload"]
            if max_age is not None:
                current = int(time.time()) if now is None else int(now)
                if current - int(body["iat"]) > max_age:
                    raise ValueError("expired")
            return payload
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if str(exc) == "expired":
                raise
            raise ValueError("invalid token") from exc
''',
}

REFERENCES["wordcount_cli"] = {
    "cli.py": '''\
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        text = Path(args.path).read_text(encoding="utf-8")
        result = {
            "lines": len(text.splitlines()),
            "words": len(text.split()),
            "bytes": len(text.encode("utf-8")),
        }
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
''',
}

REFERENCES["js_cart_pricing"] = {
    "solution.js": '''\
function calculateCart(items, options = {}) {
  if (!Array.isArray(items)) throw new TypeError("items must be an array");
  const discountCents = options.discountCents ?? 0;
  const taxBps = options.taxBps ?? 0;
  if (!Number.isInteger(discountCents) || discountCents < 0) throw new RangeError("discount");
  if (!Number.isInteger(taxBps) || taxBps < 0) throw new RangeError("tax");
  let subtotalCents = 0;
  let taxableSubtotal = 0;
  for (const item of items) {
    if (!Number.isInteger(item.priceCents) || item.priceCents < 0) throw new RangeError("price");
    if (!Number.isInteger(item.quantity) || item.quantity <= 0) throw new RangeError("quantity");
    const line = item.priceCents * item.quantity;
    subtotalCents += line;
    if (item.taxable !== false) taxableSubtotal += line;
  }
  const appliedDiscount = Math.min(discountCents, subtotalCents);
  const taxableCents = Math.max(0, taxableSubtotal - Math.min(appliedDiscount, taxableSubtotal));
  const taxCents = Math.round(taxableCents * taxBps / 10000);
  return {
    subtotalCents,
    discountCents: appliedDiscount,
    taxableCents,
    taxCents,
    totalCents: subtotalCents - appliedDiscount + taxCents,
  };
}

module.exports = { calculateCart };
''',
}

REFERENCES["js_debounce"] = {
    "solution.js": '''\
function debounce(fn, wait) {
  if (typeof fn !== "function") throw new TypeError("fn");
  if (typeof wait !== "number" || !Number.isFinite(wait) || wait < 0) throw new RangeError("wait");
  let timer = null;
  let lastArgs = null;
  let lastThis = null;
  let result;

  function invoke() {
    timer = null;
    const args = lastArgs;
    const context = lastThis;
    lastArgs = null;
    lastThis = null;
    result = fn.apply(context, args);
    return result;
  }

  function wrapped(...args) {
    lastArgs = args;
    lastThis = this;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(invoke, wait);
    return result;
  }

  wrapped.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    lastArgs = null;
    lastThis = null;
  };
  wrapped.flush = () => {
    if (timer === null) return undefined;
    clearTimeout(timer);
    return invoke();
  };
  return wrapped;
}

module.exports = { debounce };
''',
}

REFERENCES["js_stable_stringify"] = {
    "solution.js": '''\
function stableStringify(value) {
  const stack = new Set();
  function encode(input, inArray = false) {
    if (input === undefined) return inArray ? "null" : undefined;
    if (input === null) return "null";
    if (typeof input === "number") {
      if (!Number.isFinite(input)) throw new TypeError("non-finite number");
      return JSON.stringify(input);
    }
    if (typeof input === "string" || typeof input === "boolean") return JSON.stringify(input);
    if (typeof input !== "object") {
      return inArray ? "null" : undefined;
    }
    if (stack.has(input)) throw new TypeError("circular reference");
    stack.add(input);
    let output;
    if (Array.isArray(input)) {
      output = "[" + input.map((item) => encode(item, true)).join(",") + "]";
    } else {
      const parts = [];
      for (const key of Object.keys(input).sort()) {
        const encoded = encode(input[key], false);
        if (encoded !== undefined) parts.push(JSON.stringify(key) + ":" + encoded);
      }
      output = "{" + parts.join(",") + "}";
    }
    stack.delete(input);
    return output;
  }
  return encode(value, false);
}

module.exports = { stableStringify };
''',
}
