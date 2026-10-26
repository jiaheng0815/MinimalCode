"""Regression tests for the temporary pre-split compatibility attributes.

The module split moved private helpers and patch points into submodules. The
facades keep those names only until 0.7.0, so every access must warn and must
resolve to the object that now owns the behavior.
"""

from __future__ import annotations

import importlib
import unittest
import warnings

# (package, deprecated name, owning module, attribute or None for the module)
CASES = (
    (
        "minimalcode.tools",
        "IGNORED_DIRECTORIES",
        "minimalcode.tools.search",
        "IGNORED_DIRECTORIES",
    ),
    ("minimalcode.tools", "IGNORED_GLOBS", "minimalcode.tools.search", "IGNORED_GLOBS"),
    ("minimalcode.tools", "Path", "pathlib", "Path"),
    (
        "minimalcode.tools",
        "_inline_python_grep",
        "minimalcode.tools.search",
        "inline_python_grep",
    ),
    (
        "minimalcode.tools",
        "_parse_python_grep",
        "minimalcode.tools.search",
        "_parse_python_grep",
    ),
    (
        "minimalcode.tools",
        "_run_bounded_command",
        "minimalcode.tools.search",
        "run_bounded_command",
    ),
    (
        "minimalcode.tools",
        "_run_python_grep",
        "minimalcode.tools.search",
        "run_python_grep",
    ),
    (
        "minimalcode.tools",
        "_smart_case_ignore",
        "minimalcode.tools.search",
        "smart_case_ignore",
    ),
    ("minimalcode.tools", "run_shell", "minimalcode.shell", "run_shell"),
    ("minimalcode.tools", "shutil", "shutil", None),
    (
        "minimalcode.sandbox",
        "_host_allowed",
        "minimalcode.sandbox.network",
        "host_allowed",
    ),
    (
        "minimalcode.sandbox",
        "_posix_limits",
        "minimalcode.sandbox.execution",
        "_posix_limits",
    ),
    ("minimalcode.sandbox", "shutil", "shutil", None),
    (
        "minimalcode.provider",
        "_normalize_usage",
        "minimalcode.provider.common",
        "normalize_usage",
    ),
    ("minimalcode.provider", "time", "time", None),
    (
        "minimalcode.highlight",
        "_PALETTES",
        "minimalcode.highlight.palette",
        "_PALETTES",
    ),
    (
        "minimalcode.highlight",
        "_supports_color",
        "minimalcode.highlight.color",
        "_supports_color",
    ),
)


class CompatibilityShimTests(unittest.TestCase):
    def test_deprecated_attributes_warn_and_resolve_to_the_owner(self) -> None:
        for package, name, owner, attribute in CASES:
            with self.subTest(package=package, name=name):
                module = importlib.import_module(package)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    value = getattr(module, name)
                warnings_seen = [
                    item
                    for item in caught
                    if issubclass(item.category, DeprecationWarning)
                ]
                self.assertEqual(len(warnings_seen), 1, caught)
                message = str(warnings_seen[0].message)
                self.assertIn(f"{package}.{name}", message)
                self.assertIn("0.7.0", message)
                owner_module = importlib.import_module(owner)
                expected = (
                    owner_module
                    if attribute is None
                    else getattr(owner_module, attribute)
                )
                self.assertIs(value, expected)

    def test_unknown_facade_attribute_is_a_normal_attribute_error(self) -> None:
        for package in (
            "minimalcode.tools",
            "minimalcode.sandbox",
            "minimalcode.provider",
            "minimalcode.highlight",
        ):
            with self.subTest(package=package):
                module = importlib.import_module(package)
                missing = "definitely_not_a_tool"
                with self.assertRaises(AttributeError):
                    getattr(module, missing)


if __name__ == "__main__":
    unittest.main()
