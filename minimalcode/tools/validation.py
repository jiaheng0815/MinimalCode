"""Argument validation shared by the tool dispatcher.

Model-supplied values are untrusted. Binding a signature only checks names and
arity, so these helpers also enforce the documented primitive types before a
handler touches the filesystem or a child process.
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from ..errors import ToolError

_UNION_ORIGINS = {Union, UnionType}
_TYPE_LABELS: dict[Any, str] = {
    str: "a string",
    bool: "a boolean",
    int: "an integer",
    float: "a number",
    type(None): "null",
}


def clip(value: str, limit: int = 180) -> str:
    """Collapse whitespace and bound a value used in one-line activity text."""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def positive_limit(
    requested: int | None,
    default: int,
    maximum: int,
    label: str,
) -> int:
    """Validate a result limit and clamp it to the schema's hard cap."""
    if requested is not None and (
        isinstance(requested, bool) or not isinstance(requested, int)
    ):
        raise ToolError(f"{label} must be an integer")
    # A configured default above the hard cap must not silently exceed it.
    limit = min(default if requested is None else requested, maximum)
    if limit < 1:
        raise ToolError(f"{label} must be at least 1")
    return limit


def _describe_annotation(annotation: Any) -> str:
    """Render a supported annotation as a short human-readable phrase."""
    if get_origin(annotation) in _UNION_ORIGINS:
        return " or ".join(_describe_annotation(arg) for arg in get_args(annotation))
    return _TYPE_LABELS.get(annotation, "the documented type")


def _matches_annotation(value: Any, annotation: Any) -> bool:
    """Return True when ``value`` satisfies the primitive annotation.

    Unknown annotations are accepted so custom handlers keep their own checks;
    this guard exists to stop obvious mismatches, not to replace them.
    """
    if get_origin(annotation) in _UNION_ORIGINS:
        return any(_matches_annotation(value, arg) for arg in get_args(annotation))
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is type(None):
        return value is None
    return True


def check_argument_types(
    name: str,
    handler: Any,
    arguments: dict[str, Any],
) -> None:
    """Reject model-supplied arguments that do not match the handler signature.

    ``bind`` only checks names and arity, so a call such as ``{"command": 5}``
    would otherwise reach the handler and raise ``AttributeError``/``TypeError``
    outside the tool-loop error handling.
    """
    try:
        hints = get_type_hints(handler)
    except (NameError, TypeError):  # pragma: no cover - stay permissive
        return
    for key, value in arguments.items():
        annotation = hints.get(key)
        if annotation is None or _matches_annotation(value, annotation):
            continue
        raise ToolError(
            f"invalid arguments for {name}: {key} must be "
            f"{_describe_annotation(annotation)}"
        )
