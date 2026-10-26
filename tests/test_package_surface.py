"""Regression tests for the 0.7.0 package surface.

The package split kept the pre-split module-level names as temporary warning
shims; 0.7.0 ships without them. These tests lock the stable ``__all__`` surface
in place and make sure a shim cannot be reintroduced silently.
"""

from __future__ import annotations

import importlib
import unittest
import warnings

# Names that lived on the four single modules before the package split. They are
# not re-exported anymore; each one now belongs to a submodule.
REMOVED_NAMES = {
    "minimalcode.tools": (
        "IGNORED_DIRECTORIES",
        "IGNORED_GLOBS",
        "Path",
        "_inline_python_grep",
        "_parse_python_grep",
        "_run_bounded_command",
        "_run_python_grep",
        "_smart_case_ignore",
        "run_shell",
        "shutil",
    ),
    "minimalcode.sandbox": (
        "_Capture",
        "_auto_backend",
        "_child_env",
        "_decode_output",
        "_docker_user",
        "_execute",
        "_finish_capture",
        "_host_allowed",
        "_is_within",
        "_join_readers",
        "_number",
        "_posix_limits",
        "_pump",
        "_resource_limiter",
        "_set_limit",
        "_string_tuple",
        "_terminate_tree",
        "shutil",
    ),
    "minimalcode.provider": (
        "_connection_backoff",
        "_content_text",
        "_decode_json_body",
        "_is_event_stream",
        "_normalize_usage",
        "_stream_event_error",
        "_tool_arguments",
        "time",
    ),
    "minimalcode.highlight": (
        "_PALETTES",
        "_enable_windows_ansi",
        "_supports_color",
    ),
}


class PackageSurfaceTests(unittest.TestCase):
    def test_public_names_are_importable(self) -> None:
        for package in REMOVED_NAMES:
            with self.subTest(package=package):
                module = importlib.import_module(package)
                self.assertTrue(module.__all__)
                for name in module.__all__:
                    self.assertTrue(
                        hasattr(module, name), f"{package}.{name} is missing"
                    )

    def test_pre_split_names_are_not_re_exported(self) -> None:
        for package, names in REMOVED_NAMES.items():
            module = importlib.import_module(package)
            for name in names:
                with (
                    self.subTest(package=package, name=name),
                    self.assertRaises(AttributeError),
                ):
                    getattr(module, name)

    def test_unknown_attributes_use_normal_attribute_errors(self) -> None:
        for package in REMOVED_NAMES:
            with (
                self.subTest(package=package),
                self.assertRaises(AttributeError),
            ):
                module = importlib.import_module(package)
                missing = "not_a_real_attribute"
                getattr(module, missing)

    def test_package_imports_do_not_emit_deprecation_warnings(self) -> None:
        for package in REMOVED_NAMES:
            with (
                self.subTest(package=package),
                warnings.catch_warnings(record=True) as caught,
            ):
                warnings.simplefilter("always")
                importlib.reload(importlib.import_module(package))
                deprecations = [
                    item
                    for item in caught
                    if issubclass(item.category, DeprecationWarning)
                ]
                self.assertEqual(deprecations, [])


if __name__ == "__main__":
    unittest.main()
