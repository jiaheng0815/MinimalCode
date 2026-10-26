"""Temporary compatibility shims for the module-split facades.

Before the packages were split, ``minimalcode.tools`` and friends were single
modules, so callers could import or patch any module-level name there. The
packages now keep a small, explicitly temporary alias table for those names.
Every access emits :class:`DeprecationWarning` and points at the module that
actually owns the object; the table is scheduled for removal in 0.7.0.
"""

from __future__ import annotations

import importlib
import warnings
from typing import Any

# The next minor release is the removal target. Keep this in sync with
# ``docs/compatibility.md`` and the CHANGELOG entry.
REMOVAL_TARGET = "0.7.0"

# Each alias maps a removed facade name to ``(module, attribute)``. An empty
# attribute means the module object itself is the value.
AliasTable = dict[str, tuple[str, str | None]]


def deprecated_attribute(package: str, name: str, aliases: AliasTable) -> Any:
    """Resolve a deprecated facade name and warn about the new import path.

    Called from a package-level ``__getattr__`` (PEP 562). Raising
    ``AttributeError`` for unknown names keeps ``hasattr``-style probing and
    ``getattr(obj, name, default)`` behaving normally.
    """
    target = aliases.get(name)
    if target is None:
        raise AttributeError(f"module {package!r} has no attribute {name!r}")
    module_name, attribute = target
    module = importlib.import_module(module_name)
    value = module if attribute is None else getattr(module, attribute)
    replacement = module_name if attribute is None else f"{module_name}.{attribute}"
    warnings.warn(
        f"{package}.{name} is a temporary compatibility attribute and will be "
        f"removed in {REMOVAL_TARGET}; use {replacement} instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    return value
