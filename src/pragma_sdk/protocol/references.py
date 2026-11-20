"""Config reference marker shapes carried inside dispatch configs.

A ``config``, ``previous_config`` or child config value carries two marker
shapes: a dependency marker (``__dependency__: true`` with ``project_id``,
``provider``, ``resource`` and ``name``, plus ``ref`` once resolved) and a
field-reference marker (``__field_ref__: true`` with ``ref`` and
``resolved_value``). Both are part of the protocol: changing either is a new
``PROTOCOL_VERSION``.
"""

from __future__ import annotations

from typing import Any


DEPENDENCY_MARKER_KEY = "__dependency__"
"""Key whose ``True`` value marks a serialized dependency."""

FIELD_REFERENCE_MARKER_KEY = "__field_ref__"
"""Key whose ``True`` value marks a resolved field reference."""

DEPENDENCY_MARKER_REQUIRED_KEYS = frozenset({DEPENDENCY_MARKER_KEY, "project_id", "provider", "resource", "name"})
"""Keys every dependency marker carries."""


def is_dependency_marker(value: Any) -> bool:
    """Check if a value is a serialized dependency marker.

    A serialized ``Dependency[T]`` is a dict with ``__dependency__`` set to
    ``True`` and the ``project_id``, ``provider``, ``resource`` and ``name``
    keys, resolved or not.

    Args:
        value: Any value to check.

    Returns:
        True if value is a dict with the required dependency keys and ``__dependency__`` is True.
    """
    if not isinstance(value, dict):
        return False
    return DEPENDENCY_MARKER_REQUIRED_KEYS.issubset(value.keys()) and value.get(DEPENDENCY_MARKER_KEY) is True


def is_field_ref_marker(value: Any) -> bool:
    """Check if a value is a resolved field-reference marker.

    A resolved ``FieldReference`` is a dict with ``__field_ref__`` set to
    ``True``, a ``ref`` key holding the original reference and a
    ``resolved_value`` key.

    Args:
        value: Any value to check.

    Returns:
        True if value is a ``__field_ref__`` marker dict.
    """
    if not isinstance(value, dict):
        return False
    return value.get(FIELD_REFERENCE_MARKER_KEY) is True and "ref" in value and "resolved_value" in value
