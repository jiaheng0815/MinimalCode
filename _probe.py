import tempfile
from pathlib import Path

from minimalcode.config import load_settings
from minimalcode.shell import detect_shell
from minimalcode.tools import ToolRuntime

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    ws = root / "ws"
    ws.mkdir()
    tools = ToolRuntime(load_settings(root / ".M"), ws, detect_shell())
    tools.write("a.txt", "hello\n")
    cases = [
        ("edit", {"path": "a.txt", "old_text": 5, "new_text": "x"}),
        ("edit", {"path": "a.txt", "old_text": "hello", "new_text": None}),
        ("shell", {"command": 5}),
        ("grep", {"pattern": ["a"]}),
        ("grep", {"pattern": "a", "glob": 5}),
        ("find", {"pattern": 5}),
        ("read", {"path": "a.txt", "start_line": "x"}),
        ("write", {"path": 5, "content": "x"}),
        ("write", {"path": "b.txt", "content": b"x"}),
    ]
    for name, args in cases:
        try:
            tools.dispatch(name, args)
            print("OK  ", name, args)
        except Exception as exc:  # noqa: BLE001
            print(type(exc).__name__, name, args, "->", exc)
