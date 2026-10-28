from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from minimalcode.config import ConfigError, load_settings
from minimalcode.importer import import_pi_settings


def _write_pi_config(root: Path, api_key: str = "local-secret") -> None:
    root.mkdir(parents=True)
    models = {
        "providers": {
            "local": {
                "baseUrl": "http://127.0.0.1:8080/v1",
                "api": "openai-responses",
                "apiKey": api_key,
                "models": [
                    {
                        "id": "bonsai2-27b",
                        "contextWindow": 262144,
                    }
                ],
            }
        }
    }
    settings = {
        "defaultProvider": "local",
        "defaultModel": "bonsai2-27b",
    }
    (root / "models.json").write_text(json.dumps(models), encoding="utf-8")
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


class ImporterTests(unittest.TestCase):
    def test_import_responses_provider_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            destination = root / ".MinimalCode"
            _write_pi_config(pi_home)
            result = import_pi_settings(
                destination_home=destination,
                pi_home=pi_home,
                include_secrets=True,
            )
            self.assertEqual(result.provider, "local")
            self.assertEqual(result.model, "bonsai2-27b")
            settings = load_settings(destination, require_file=True)
            name, provider = settings.provider_config()
            self.assertEqual(name, "local")
            self.assertEqual(provider["protocol"], "openai_responses")
            self.assertEqual(provider["context_window"], 262144)
            self.assertEqual(provider["api_key"], "local-secret")

    def test_no_secrets_uses_environment_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            destination = root / ".MinimalCode"
            _write_pi_config(pi_home)
            import_pi_settings(
                destination_home=destination,
                pi_home=pi_home,
                include_secrets=False,
            )
            settings = load_settings(destination, require_file=True)
            _, provider = settings.provider_config()
            self.assertNotIn("api_key", provider)
            self.assertEqual(provider["api_key_env"], "MINIMALCODE_LOCAL_API_KEY")

    def test_secrets_are_not_copied_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            destination = root / ".MinimalCode"
            _write_pi_config(pi_home)

            import_pi_settings(destination_home=destination, pi_home=pi_home)

            settings = load_settings(destination, require_file=True)
            _, provider = settings.provider_config()
            self.assertNotIn("api_key", provider)
            self.assertIn("api_key_env", provider)

    def test_invalid_base_url_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            _write_pi_config(pi_home)
            models_path = pi_home / "models.json"
            models = json.loads(models_path.read_text(encoding="utf-8"))
            models["providers"]["local"]["baseUrl"] = "not a url"
            models_path.write_text(json.dumps(models), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "no compatible"):
                import_pi_settings(
                    destination_home=root / ".MinimalCode",
                    pi_home=pi_home,
                )

    def test_invalid_context_window_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            _write_pi_config(pi_home)
            models_path = pi_home / "models.json"
            models = json.loads(models_path.read_text(encoding="utf-8"))
            models["providers"]["local"]["models"][0]["contextWindow"] = "large"
            models_path.write_text(json.dumps(models), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "contextWindow"):
                import_pi_settings(
                    destination_home=root / ".MinimalCode",
                    pi_home=pi_home,
                )

    def test_missing_pi_config_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ConfigError, "Pi config file not found"):
                import_pi_settings(
                    destination_home=root / ".MinimalCode",
                    pi_home=root / "missing",
                )

    def test_missing_pi_settings_still_imports_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            _write_pi_config(pi_home)
            (pi_home / "settings.json").unlink()

            result = import_pi_settings(
                destination_home=root / ".MinimalCode",
                pi_home=pi_home,
            )

            self.assertEqual(result.provider, "local")
            self.assertEqual(result.model, "bonsai2-27b")

    def test_unreadable_pi_config_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_home = root / "pi"
            _write_pi_config(pi_home)
            (pi_home / "models.json").write_bytes(b"\xff\xfe\x00")

            with self.assertRaisesRegex(ConfigError, "cannot read"):
                import_pi_settings(
                    destination_home=root / ".MinimalCode",
                    pi_home=pi_home,
                )


if __name__ == "__main__":
    unittest.main()
