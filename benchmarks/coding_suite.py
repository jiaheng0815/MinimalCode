#!/usr/bin/env python3
"""Run the 30-task coding-agent comparison against DeepSeek Responses."""

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

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from coding_tasks import TASKS, CodingTask
from deepseek_responses import (
    MODEL,
    PROVIDER,
    _cost,
    _minimal_result,
    _pi_result,
    _prepare_configs,
)
from reference_solutions import REFERENCES

MAX_TURNS = 8
RUN_TIMEOUT_SECONDS = 90


def _write_files(task: CodingTask, workspace: Path) -> None:
    for relative, content in task.files.items():
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError as exc:
            raise RuntimeError(f"unsafe task file path: {relative}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _validate_tasks(node_path: str | None) -> None:
    if len(TASKS) != 30:
        raise RuntimeError(f"expected 30 tasks, found {len(TASKS)}")
    identifiers = [task.id for task in TASKS]
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("duplicate task IDs")
    with tempfile.TemporaryDirectory(prefix="minimalcode-suite-validate-") as temp:
        root = Path(temp)
        for task in TASKS:
            workspace = root / task.id
            workspace.mkdir()
            _write_files(task, workspace)
            if task.language == "python":
                for name, content in task.files.items():
                    if name.endswith(".py"):
                        compile(content, f"{task.id}/{name}", "exec")
                compile(task.hidden_test, f"{task.id}/hidden_test.py", "exec")
            elif task.language == "javascript":
                if not node_path:
                    raise RuntimeError(f"node is required for task {task.id}")
                for name, content in task.files.items():
                    if not name.endswith(".js"):
                        continue
                    path = workspace / name
                    completed = subprocess.run(
                        [node_path, "--check", str(path)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if completed.returncode != 0:
                        raise RuntimeError(f"{task.id}/{name}: {completed.stderr}")
                hidden = workspace / "hidden_test.js"
                hidden.write_text(task.hidden_test, encoding="utf-8")
                completed = subprocess.run(
                    [node_path, "--check", str(hidden)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"{task.id}/hidden_test.js: {completed.stderr}")
            else:
                raise RuntimeError(f"unknown task language: {task.language}")


def _validate_references(node_path: str | None) -> None:
    """Run every hidden grader against a known-good implementation."""
    with tempfile.TemporaryDirectory(prefix="minimalcode-reference-") as temp:
        root = Path(temp)
        graders = root / "graders"
        graders.mkdir()
        env = os.environ.copy()
        for task in TASKS:
            workspace = root / task.id
            workspace.mkdir()
            _write_files(task, workspace)
            files = REFERENCES.get(task.id)
            if not files:
                raise RuntimeError(f"missing reference implementation for {task.id}")
            for relative, content in files.items():
                path = workspace / relative
                path.write_text(content, encoding="utf-8")
            passed, output = _grade(
                task,
                workspace,
                graders,
                node_path=node_path,
                env=env,
            )
            if not passed:
                raise RuntimeError(f"reference failed {task.id}:\n{output}")


def _common_prompt(task: CodingTask) -> str:
    return (
        "Work only inside the current repository. Complete the task below, run "
        "the visible verification, and fix implementation problems you find. "
        "Do not edit or delete verification files. Keep the change focused. "
        "Finish with a short summary.\n\n"
        f"{task.prompt}"
    )


def _invoke_agent(
    *,
    agent: str,
    task: CodingTask,
    workspace: Path,
    minimal_home: Path,
    env: dict[str, str],
    pi_path: str,
) -> dict[str, Any]:
    prompt = _common_prompt(task)
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
            prompt,
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
            "--max-turns",
            str(MAX_TURNS),
            prompt,
        ]

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "agent": agent,
            "command_ok": False,
            "error": f"timeout after {RUN_TIMEOUT_SECONDS}s",
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "stderr": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    elapsed_ms = (time.perf_counter() - started) * 1000
    result: dict[str, Any] = {
        "agent": agent,
        "elapsed_ms": elapsed_ms,
        "command_ok": completed.returncode == 0,
        "usage_ok": False,
        "stderr": completed.stderr[-2000:],
    }
    try:
        parsed = _pi_result(completed.stdout) if agent == "pi" else _minimal_result(
            completed.stdout
        )
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        result.update(
            {
                "command_ok": False,
                "error": f"result parse failed: {exc}",
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
        )
        if completed.returncode != 0:
            result["error"] = f"agent exited {completed.returncode}"
        return result
    result.update(parsed)
    result["usage_ok"] = True
    result["cost_usd"] = _cost(result)
    if completed.returncode != 0:
        result["error"] = f"agent exited {completed.returncode}"
    return result


def _grade(
    task: CodingTask,
    workspace: Path,
    grader_dir: Path,
    *,
    node_path: str | None,
    env: dict[str, str],
) -> tuple[bool, str]:
    if task.language == "python":
        test_path = grader_dir / f"{task.id}.py"
        test_path.write_text(task.hidden_test, encoding="utf-8")
        grader_env = env.copy()
        old_path = grader_env.get("PYTHONPATH")
        grader_env["PYTHONPATH"] = str(workspace) + (
            os.pathsep + old_path if old_path else ""
        )
        command = [sys.executable, str(test_path)]
    else:
        if not node_path:
            return False, "node not found"
        # JavaScript requires are relative to the test file, so run the post-run
        # hidden test from the workspace after the agent has already finished.
        test_path = workspace / f".hidden-{task.id}.js"
        test_path.write_text(task.hidden_test, encoding="utf-8")
        grader_env = env.copy()
        command = [node_path, str(test_path)]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=grader_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
    finally:
        if task.language == "javascript":
            test_path.unlink(missing_ok=True)
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return completed.returncode == 0, output[-3000:]


def _mean(results: list[dict[str, Any]], key: str) -> float:
    values = [float(result[key]) for result in results if result.get("usage_ok")]
    return statistics.mean(values) if values else 0.0


def _render_results(
    results: list[dict[str, Any]],
    *,
    selected_tasks: list[CodingTask],
) -> tuple[str, dict[str, Any]]:
    agents = ("pi", "minimalcode")
    lines = [
        "# MinimalCode vs Pi: 30-task coding suite",
        "",
        (
            f"DeepSeek `{MODEL}`, Responses API, thinking `high`, "
            f"maximum output 2048 tokens. MinimalCode max turns: {MAX_TURNS}; "
            "Pi used its native loop with a 90-second process timeout. "
            "Each selected task was run once per agent; no automatic retries."
        ),
        "",
        "| Agent | Passed | Mean tokens | Mean time | Median time | Total cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    json_summary: dict[str, Any] = {"tasks": len(selected_tasks), "agents": {}}
    for agent in agents:
        runs = [result for result in results if result["agent"] == agent]
        passed = sum(bool(result.get("passed")) for result in runs)
        measured = [result for result in runs if result.get("usage_ok")]
        times = [float(result["elapsed_ms"]) for result in measured]
        total_cost = sum(float(result.get("cost_usd", 0)) for result in runs)
        lines.append(
            f"| {agent} | {passed}/{len(runs)} | {_mean(runs, 'total_tokens'):.1f} | "
            f"{_mean(runs, 'elapsed_ms') / 1000:.3f} s | "
            f"{(statistics.median(times) / 1000 if times else 0):.3f} s | "
            f"${total_cost:.6f} |"
        )
        json_summary["agents"][agent] = {
            "passed": passed,
            "tasks": len(runs),
            "mean_tokens": _mean(runs, "total_tokens"),
            "mean_time_ms": _mean(runs, "elapsed_ms"),
            "median_time_ms": statistics.median(times) if times else 0,
            "total_cost_usd": total_cost,
        }

    task_ids = sorted({result["task"] for result in results})
    by_agent_task = {
        (result["agent"], result["task"]): result for result in results
    }
    common_tasks = [
        task_id
        for task_id in task_ids
        if by_agent_task.get(("pi", task_id), {}).get("passed")
        and by_agent_task.get(("minimalcode", task_id), {}).get("passed")
    ]
    if common_tasks:
        lines.extend(
            [
                "",
                f"## Common passes ({len(common_tasks)} tasks)",
                "",
                "| Agent | Mean tokens | Mean time | Median time | Total cost |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for agent in agents:
            runs = [by_agent_task[(agent, task_id)] for task_id in common_tasks]
            times = [float(result["elapsed_ms"]) for result in runs]
            lines.append(
                f"| {agent} | {_mean(runs, 'total_tokens'):.1f} | "
                f"{_mean(runs, 'elapsed_ms') / 1000:.3f} s | "
                f"{statistics.median(times) / 1000:.3f} s | "
                f"${sum(float(result.get('cost_usd', 0)) for result in runs):.6f} |"
            )

    categories = sorted({task.category for task in selected_tasks})
    lines.extend(
        [
            "",
            "## Category pass rate",
            "",
            "| Category | Pi | MinimalCode |",
            "|---|---:|---:|",
        ]
    )
    for category in categories:
        category_ids = {task.id for task in selected_tasks if task.category == category}
        cells = []
        for agent in agents:
            relevant = [
                result
                for result in results
                if result["agent"] == agent and result["task"] in category_ids
            ]
            cells.append(f"{sum(bool(r.get('passed')) for r in relevant)}/{len(relevant)}")
        lines.append(f"| {category} | {cells[0]} | {cells[1]} |")

    lines.extend(
        [
            "",
            "## Task results",
            "",
            "| # | Task | Category | Pi | MinimalCode |",
            "|---:|---|---|---|---|",
        ]
    )
    by_key = {
        (result["task"], result["agent"]): result
        for result in results
    }
    for index, task in enumerate(selected_tasks, 1):
        cells = []
        for agent in agents:
            result = by_key.get((task.id, agent))
            if result is None:
                cells.append("not run")
            elif result.get("passed"):
                cells.append(
                    f"PASS {result['total_tokens']}t "
                    f"{result['elapsed_ms'] / 1000:.2f}s"
                )
            else:
                cells.append("FAIL")
        lines.append(f"| {index} | `{task.id}` | {task.category} | {cells[0]} | {cells[1]} |")

    failures = [
        result
        for result in results
        if not result.get("passed")
    ]
    if failures:
        lines.extend(["", "## Failures", ""])
        for failure in failures:
            detail = (
                failure.get("error")
                or failure.get("grader_output")
                or failure.get("stderr")
                or "unknown failure"
            )
            detail_lines = [line for line in str(detail).splitlines() if line.strip()]
            summary = detail_lines[-1] if detail_lines else "unknown failure"
            lines.append(
                f"- `{failure['task']}` / {failure['agent']}: "
                f"{summary[:300]}"
            )

    total_cost = sum(float(result.get("cost_usd", 0)) for result in results)
    lines.extend(
        [
            "",
            (
                f"Recorded API cost: **${total_cost:.6f}**. A timed-out agent may "
                "have used additional tokens that were not returned in its usage stream."
            ),
        ]
    )
    json_summary["total_cost_usd"] = total_cost
    json_summary["results"] = results
    return "\n".join(lines) + "\n", json_summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Comma-separated task IDs")
    parser.add_argument("--agent", choices=("pi", "minimalcode"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=0.60)
    parser.add_argument("--output", help="Write Markdown and JSON result paths")
    args = parser.parse_args()
    if args.max_budget_usd <= 0:
        parser.error("--max-budget-usd must be positive")

    node_path = shutil.which("node")
    selected = TASKS
    if args.only:
        requested = [item.strip() for item in args.only.split(",") if item.strip()]
        by_id = {task.id: task for task in TASKS}
        missing = [task_id for task_id in requested if task_id not in by_id]
        if missing:
            parser.error(f"unknown task IDs: {', '.join(missing)}")
        selected = [by_id[task_id] for task_id in requested]

    _validate_tasks(node_path)
    _validate_references(node_path)
    print(
        f"validated {len(TASKS)} task definitions and reference solutions",
        file=sys.stderr,
    )
    if args.validate_only:
        return 0
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2
    pi_path = shutil.which("pi")
    if not pi_path:
        print("pi executable not found", file=sys.stderr)
        return 2

    partial_path = (
        Path(args.output).with_suffix(".partial.json") if args.output else None
    )
    results: list[dict[str, Any]] = []
    if args.resume and partial_path and partial_path.exists():
        checkpoint = json.loads(partial_path.read_text(encoding="utf-8"))
        results = list(checkpoint.get("results", []))
        print(f"resuming with {len(results)} completed agent runs", file=sys.stderr)
    elif args.resume and args.output:
        completed_path = Path(args.output).with_suffix(".json")
        if completed_path.exists():
            checkpoint = json.loads(completed_path.read_text(encoding="utf-8"))
            results = list(checkpoint.get("results", []))
            print(
                f"resuming with {len(results)} completed agent runs",
                file=sys.stderr,
            )
    run_agents = (args.agent,) if args.agent else ("pi", "minimalcode")
    spent = sum(float(result.get("cost_usd", 0)) for result in results)
    consecutive_command_failures = {agent: 0 for agent in run_agents}
    budget_exhausted = False
    completed_keys = {(result["task"], result["agent"]) for result in results}
    with tempfile.TemporaryDirectory(prefix="minimalcode-coding-suite-") as temp:
        root = Path(temp)
        _, minimal_home, env = _prepare_configs(root, max_turns=MAX_TURNS)
        grader_dir = root / "graders"
        grader_dir.mkdir()
        for index, task in enumerate(selected, 1):
            full_order = ("pi", "minimalcode") if index % 2 else ("minimalcode", "pi")
            order = tuple(agent for agent in full_order if agent in run_agents)
            for agent in order:
                if (task.id, agent) in completed_keys:
                    continue
                workspace = root / f"{index:02d}-{task.id}-{agent}"
                workspace.mkdir()
                _write_files(task, workspace)
                result = _invoke_agent(
                    agent=agent,
                    task=task,
                    workspace=workspace,
                    minimal_home=minimal_home,
                    env=env,
                    pi_path=pi_path,
                )
                result["task"] = task.id
                result["category"] = task.category
                try:
                    passed, grader_output = _grade(
                        task,
                        workspace,
                        grader_dir,
                        node_path=node_path,
                        env=env,
                    )
                except subprocess.TimeoutExpired:
                    passed, grader_output = False, "grader timeout"
                result["grader_passed"] = passed
                result["passed"] = bool(passed and result.get("command_ok"))
                result["grader_output"] = grader_output
                if result.get("command_ok") or result.get("usage_ok"):
                    consecutive_command_failures[agent] = 0
                else:
                    consecutive_command_failures[agent] += 1
                spent += float(result.get("cost_usd", 0))
                results.append(result)
                completed_keys.add((task.id, agent))
                if partial_path:
                    partial_path.write_text(
                        json.dumps(
                            {
                                "task_ids": [task.id for task in selected],
                                "results": results,
                            },
                            indent=2,
                            ensure_ascii=False,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                print(
                    f"[{index:02d}/{len(selected)}] {task.id} {agent}: "
                    f"{'PASS' if result.get('passed') else 'FAIL'} "
                    f"{result.get('total_tokens', 0)}t "
                    f"{result.get('elapsed_ms', 0) / 1000:.2f}s "
                    f"${spent:.4f}",
                    file=sys.stderr,
                )
                if spent >= args.max_budget_usd:
                    print("budget cap reached; stopping", file=sys.stderr)
                    budget_exhausted = True
                    break
                if consecutive_command_failures[agent] >= 2:
                    print(
                        f"two consecutive {agent} process failures; stopping",
                        file=sys.stderr,
                    )
                    break
            if budget_exhausted or any(
                count >= 2 for count in consecutive_command_failures.values()
            ):
                break

    markdown, summary = _render_results(results, selected_tasks=selected)
    summary["budget_exhausted"] = budget_exhausted
    print(markdown)
    if args.output:
        markdown_path = Path(args.output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path = markdown_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if partial_path:
            partial_path.unlink(missing_ok=True)
        print(f"wrote {markdown_path} and {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
