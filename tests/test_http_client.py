from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class HttpClientDependencyTests(unittest.TestCase):
    """The runtime must talk to httpx2 only, never to the legacy httpx package."""

    def test_runtime_imports_httpx2_without_legacy_httpx(self) -> None:
        # httpx and httpx2 are distinct packages with distinct classes, so a
        # single module importing both would split exception handling and
        # transports in half. Block the legacy name and import every HTTP client.
        script = (
            "import builtins\n"
            "real = builtins.__import__\n"
            "def guard(name, *args, **kwargs):\n"
            "    if name == 'httpx' or name.startswith('httpx.'):\n"
            "        raise AssertionError('legacy httpx imported: ' + name)\n"
            "    return real(name, *args, **kwargs)\n"
            "builtins.__import__ = guard\n"
            "import httpx2\n"
            "from minimalcode.provider import OpenAICompatibleProvider\n"
            "from minimalcode.vision import ProviderVisionClient\n"
            "from minimalcode.web import ProviderWebClient\n"
            "provider = OpenAICompatibleProvider(\n"
            "    name='t',\n"
            "    config={'base_url': 'https://api.example.com', 'api_key': 'k'},\n"
            "    model='m',\n"
            "    thinking='none',\n"
            ")\n"
            "assert isinstance(provider._client, httpx2.Client), type(provider._client)\n"
            "provider.close()\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "ok")

    def test_declared_dependency_is_httpx2(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"httpx2>=2.0,<3"', pyproject)
        self.assertNotIn('"httpx>=', pyproject)


if __name__ == "__main__":
    unittest.main()
