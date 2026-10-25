from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimalcode.config import load_settings
from minimalcode.shell import ShellSpec, detect_shell
from minimalcode.subagent import (
    DEPTH_ENV,
    build_command,
    child_env,
    depth_from_env,
    quote_argument,
)
from minimalcode.tools import ToolError, ToolRuntime, available_tool_schemas

POWERSHELL = ShellSpec(
    kind="pwsh7", executable="pwsh", label="PowerShell 7", is_windows=True
)
BASH = ShellSpec(kind="bash", executable="/bin/bash", label="bash", is_windows=False)


class SubagentCommandTests(unittest.TestCase):
    def test_command_targets_a_quiet_print_run(self) -> None:
        command = build_command(POWERSHELL, cwd=Path("work"), max_turns=7)
        self.assertIn("-m minimalcode", command)
        self.assertIn("-p", command.split())
        self.assertIn("--no-session", command)
        self.assertIn("--quiet", command)
        self.assertIn("--max-turns '7'", command)

    def test_powershell_uses_the_call_operator_and_keeps_the_exit_code(self) -> None:
        command = build_command(POWERSHELL, cwd=Path("work"))
        self.assertTrue(command.startswith("& '"))
        self.assertTrue(command.endswith("; exit $LASTEXITCODE"))
        self.assertNotIn("exit $LASTEXITCODE", build_command(BASH, cwd=Path("work")))

    def test_prompt_never_reaches_the_command_line(self) -> None:
        command = build_command(BASH, cwd=Path("work"))
        self.assertNotIn("review", command)

    def test_arguments_are_quoted_per_shell(self) -> None:
        text = "it's a b"
        self.assertEqual(quote_argument(text, POWERSHELL), "'it''s a b'")
        self.assertEqual(quote_argument(text, BASH), "'it'\"'\"'s a b'")

    def test_child_env_increments_depth_and_keeps_python_path(self) -> None:
        with patch.dict(os.environ, {"PYTHONPATH": "existing"}, clear=False):
            env = child_env(2, python_path=Path("root"))
        self.assertEqual(env[DEPTH_ENV], "2")
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join(["root", "existing"]))

    def test_depth_parsing_ignores_garbage(self) -> None:
        self.assertEqual(depth_from_env({}), 0)
        self.assertEqual(depth_from_env({DEPTH_ENV: "3"}), 3)
        self.assertEqual(depth_from_env({DEPTH_ENV: "x"}), 0)
        self.assertEqual(depth_from_env({DEPTH_ENV: "-4"}), 0)


class SubagentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / ".MinimalCode"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.settings = load_settings(self.home)
        self.tools = ToolRuntime(self.settings, self.workspace, detect_shell())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_schema_is_only_offered_when_enabled(self) -> None:
        names = {tool["function"]["name"] for tool in available_tool_schemas()}
        self.assertNotIn("subagent", names)
        names = {
            tool["function"]["name"]
            for tool in available_tool_schemas(None, None, True)
        }
        self.assertIn("subagent", names)

    def test_dispatch_runs_minimalcode_through_the_shell(self) -> None:
        with patch(
            "minimalcode.tools.run_shell",
            return_value={"status": "success", "exit_code": 0, "stdout": "report"},
        ) as run:
            result = self.tools.dispatch(
                "subagent", {"task": "review src/app.py", "timeout": 30}
            )

        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertIn("-m minimalcode", command)
        self.assertIn("--cwd", command)
        self.assertIn("review src/app.py", options["stdin_text"])
        self.assertEqual(options["env"][DEPTH_ENV], "1")
        self.assertEqual(options["cwd"], self.workspace.resolve())
        self.assertEqual(options["timeout"], 30)
        self.assertEqual(result["stdout"], "report")
        self.assertEqual(result["command"], command)

    def test_timeout_and_turns_are_capped_by_configuration(self) -> None:
        self.tools.subagent_timeout = 60
        self.tools.subagent_max_turns = 5
        with patch(
            "minimalcode.tools.run_shell",
            return_value={"status": "success", "exit_code": 0},
        ) as run:
            self.tools.subagent("review", timeout=3600, max_turns=500)
        command = run.call_args.args[0]
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertIn("--max-turns '5'", command)

    def test_nested_subagents_are_refused_at_the_depth_limit(self) -> None:
        with patch.dict(os.environ, {DEPTH_ENV: "2"}, clear=False):
            tools = ToolRuntime(self.settings, self.workspace, detect_shell())
        self.assertFalse(tools.subagent_enabled)
        with self.assertRaisesRegex(ToolError, "subagent is disabled"):
            tools.subagent("review")

    def test_subagent_cannot_leave_the_workspace(self) -> None:
        with self.assertRaisesRegex(ToolError, "outside workspace"):
            self.tools.subagent("review", cwd=str(self.root))

    def test_empty_task_is_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "task"):
            self.tools.subagent("   ")

    def test_activity_shows_the_task(self) -> None:
        line = ToolRuntime.activity("subagent", {"task": "review the parser"})
        self.assertEqual(line, "[subagent] review the parser")


if __name__ == "__main__":
    unittest.main()
