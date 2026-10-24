"""Small, reproducible Pi vs MinimalCode DeepSeek Responses benchmark."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MODEL = "deepseek-flash"
PROVIDER = "deepseek-responses"
BASE_URL = "https://api.deepseek.com/v1"
TASK = (
    "Use the write tool exactly once to create answer.txt containing exactly 42 "
    "with no newline. Do not read any files. Then reply DONE only."
)
MAX_OUTPUT_TOKENS = 2048
MAX_TURNS = 5
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# DeepSeek Flash catalog prices in USD per million tokens.
PRICE_INPUT = 0.30
PRICE_OUTPUT = 1.20
PRICE_CACHE_READ = 0.006


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _prepare_configs(
    root: Path,
    *,
    max_turns: int = MAX_TURNS,
) -> tuple[Path, Path, dict[str, str]]:
    pi_home = root / "pi-home"
    minimal_home = root / "minimalcode-home"
    pi_home.mkdir()
    minimal_home.mkdir()

    _write_json(
        pi_home / "models.json",
        {
            "providers": {
                PROVIDER: {
                    "baseUrl": BASE_URL,
                    "api": "openai-responses",
                    "apiKey": "${DEEPSEEK_API_KEY}",
                    "models": [
                        {
                            "id": MODEL,
                            "name": "DeepSeek V4.1 Flash",
                            "contextWindow": 1_000_000,
                            "maxTokens": MAX_OUTPUT_TOKENS,
                            "reasoning": True,
                            "thinkingLevelMap": {"high": "high"},
                            "input": ["text"],
                            "cost": {
                                "input": PRICE_INPUT,
                                "output": PRICE_OUTPUT,
                                "cacheRead": PRICE_CACHE_READ,
                                "cacheWrite": 0,
                            },
                        }
                    ],
                }
            }
        },
    )
    _write_json(
        pi_home / "settings.json",
        {
            "defaultProvider": PROVIDER,
            "defaultModel": MODEL,
            "defaultThinkingLevel": "high",
        },
    )

    from minimalcode.config import DEFAULT_SETTINGS

    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["provider"] = "deepseek"
    settings["model"] = MODEL
    settings["thinking"] = "high"
    settings["max_turns"] = max_turns
    provider = settings["providers"]["deepseek"]
    provider["protocol"] = "openai_responses"
    provider["base_url"] = BASE_URL
    provider["api_key_env"] = "DEEPSEEK_API_KEY"
    provider["context_window"] = 1_000_000
    provider["stream"] = True
    provider["include_usage"] = False
    provider["send_reasoning_effort"] = True
    provider["extra_body"] = {"max_output_tokens": MAX_OUTPUT_TOKENS}
    _write_json(minimal_home / "settings.json", settings)

    env = os.environ.copy()
    env["PI_CODING_AGENT_DIR"] = str(pi_home)
    return pi_home, minimal_home, env


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    if completed.stderr.strip():
        raise RuntimeError(f"command wrote stderr:\n{completed.stderr[-4000:]}")
    return elapsed_ms, completed.stdout


def _pi_result(stdout: str) -> dict[str, Any]:
    totals = {
        "input_uncached": 0,
        "cache_read": 0,
        "output": 0,
        "reasoning": 0,
        "total_tokens": 0,
    }
    assistant_messages = 0
    tool_calls = 0
    api_values: set[str] = set()
    final_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_execution_start":
            tool_calls += 1
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        assistant_messages += 1
        usage = message.get("usage") or {}
        totals["input_uncached"] += int(usage.get("input") or 0)
        totals["cache_read"] += int(usage.get("cacheRead") or 0)
        totals["output"] += int(usage.get("output") or 0)
        totals["reasoning"] += int(usage.get("reasoning") or 0)
        totals["total_tokens"] += int(usage.get("totalTokens") or 0)
        if message.get("api"):
            api_values.add(str(message["api"]))
        text = "".join(
            str(block.get("text") or "")
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            final_text = text
    if not assistant_messages:
        raise RuntimeError("Pi returned no completed assistant messages")
    if api_values and api_values != {"openai-responses"}:
        raise RuntimeError(f"Pi did not use Responses API: {sorted(api_values)}")
    totals["assistant_turns"] = assistant_messages
    totals["tool_calls"] = tool_calls
    totals["final_text"] = final_text
    return totals


def _minimal_result(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"MinimalCode returned invalid JSON: {stdout[-4000:]}"
        ) from exc
    usage = payload.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    return {
        "input_uncached": max(0, prompt - cached),
        "cache_read": cached,
        "output": int(usage.get("completion_tokens") or 0),
        "reasoning": int(usage.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "assistant_turns": int(payload.get("turns") or 0),
        "final_text": str(payload.get("text") or ""),
        "completed": bool(payload.get("completed")),
    }


def _cost(result: dict[str, Any]) -> float:
    return (
        result["input_uncached"] * PRICE_INPUT
        + result["output"] * PRICE_OUTPUT
        + result["cache_read"] * PRICE_CACHE_READ
    ) / 1_000_000


def _mean(results: list[dict[str, Any]], key: str) -> float:
    return statistics.mean(float(result[key]) for result in results)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.runs <= 5:
        parser.error("--runs must be between 1 and 5 to keep API cost bounded")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2

    pi_path = shutil.which("pi")
    if not pi_path:
        print("pi executable not found", file=sys.stderr)
        return 2

    orders = [
        ["pi", "minimalcode"] if run % 2 == 1 else ["minimalcode", "pi"]
        for run in range(1, args.runs + 1)
    ]
    results: dict[str, list[dict[str, Any]]] = {"pi": [], "minimalcode": []}

    with tempfile.TemporaryDirectory(prefix="minimalcode-deepseek-benchmark-") as temp:
        root = Path(temp)
        _, minimal_home, env = _prepare_configs(root)
        for run_number, order in enumerate(orders, 1):
            for agent in order:
                workspace = root / f"run-{run_number}-{agent}"
                workspace.mkdir()
                if agent == "pi":
                    command = [
                        pi_path,
                        "--mode",
                        "json",
                        "--no-session",
                        "--no-context-files",
                        "--no-extensions",
                        "--no-skills",
                        "--no-prompt-templates",
                        "--no-mcp",
                        "--no-approve",
                        "--offline",
                        "--provider",
                        PROVIDER,
                        "--model",
                        MODEL,
                        "--thinking",
                        "high",
                        TASK,
                    ]
                else:
                    command = [
                        sys.executable,
                        "-m",
                        "minimalcode",
                        "--json",
                        "--no-session",
                        "--home",
                        str(minimal_home),
                        "--cwd",
                        str(workspace),
                        TASK,
                    ]
                elapsed_ms, stdout = _run_process(command, cwd=workspace, env=env)
                result = (
                    _pi_result(stdout) if agent == "pi" else _minimal_result(stdout)
                )
                result["agent"] = agent
                result["run"] = run_number
                result["elapsed_ms"] = elapsed_ms
                result["cost_usd"] = _cost(result)
                result["verified"] = (workspace / "answer.txt").read_bytes() == b"42"
                if not result["verified"]:
                    raise RuntimeError(
                        f"{agent} failed task verification on run {run_number}"
                    )
                if result["final_text"].strip() != "DONE":
                    raise RuntimeError(
                        f"{agent} returned unexpected final text on run {run_number}: "
                        f"{result['final_text']!r}"
                    )
                results[agent].append(result)
                print(
                    f"run {run_number} {agent}: {result['total_tokens']} tokens, "
                    f"{result['elapsed_ms']:.1f} ms",
                    file=sys.stderr,
                )

    print("# MinimalCode vs Pi")
    print()
    print(
        f"DeepSeek `{MODEL}`, Responses API, thinking `high`, "
        f"{args.runs} runs each, {MAX_OUTPUT_TOKENS} max output tokens."
    )
    print()
    print(
        "| Agent | Mean tokens | Mean uncached input | Mean cache read | Mean output |"
    )
    print("|---|---:|---:|---:|---:|")
    for agent in ("pi", "minimalcode"):
        runs = results[agent]
        print(
            f"| {agent} | {_mean(runs, 'total_tokens'):.1f} | "
            f"{_mean(runs, 'input_uncached'):.1f} | "
            f"{_mean(runs, 'cache_read'):.1f} | {_mean(runs, 'output'):.1f} |"
        )
    print()
    print("| Agent | Mean time | Median time | Mean cost | Total cost |")
    print("|---|---:|---:|---:|---:|")
    for agent in ("pi", "minimalcode"):
        runs = results[agent]
        print(
            f"| {agent} | {_mean(runs, 'elapsed_ms') / 1000:.3f} s | "
            f"{statistics.median(r['elapsed_ms'] for r in runs) / 1000:.3f} s | "
            f"${_mean(runs, 'cost_usd'):.6f} | "
            f"${sum(r['cost_usd'] for r in runs):.6f} |"
        )
    print()
    print("## Per-run tokens / time")
    print()
    print("| Run | Agent | Tokens | Time | Cost |")
    print("|---:|---|---:|---:|---:|")
    for run in sorted(
        [*results["pi"], *results["minimalcode"]],
        key=lambda item: (item["run"], item["elapsed_ms"]),
    ):
        print(
            f"| {run['run']} | {run['agent']} | "
            f"{run['total_tokens']} | {run['elapsed_ms'] / 1000:.3f} s | "
            f"${run['cost_usd']:.6f} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
