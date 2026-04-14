"""Canonical resource identity.

Defines the structured tuple that uniquely identifies a resource on the
platform and the flat canonical string used for display, database keys,
and log binds.

The four segments are ``project_id``, ``provider``, ``resource``, and
``name``. They are joined with ``::`` to produce a canonical string. The
``::`` separator is reserved and forbidden inside segments. This lets
``split("::", 3)`` parse any canonical string unambiguously and lets
segments contain ``/`` (e.g. ``pragmatiks/agno``, ``models/anthropic``)
without collision.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


_RESERVED_SEPARATOR = "::"

_ALLOWED_SEGMENT_NAMES = ("project_id", "provider", "resource", "name")


class InvalidResourceIdentityError(ValueError):
    """Raised when a resource identity is malformed or contains illegal characters."""


def _validate_segment(value: str, field: str) -> str:
    """Validate a single identity segment.

    Args:
        value: Segment value to validate.
        field: Field name for error messages.

    Returns:
        The validated segment.

    Raises:
        InvalidResourceIdentityError: If the segment is empty, contains the
            reserved separator, or contains control characters.
    """
    if not isinstance(value, str):
        raise InvalidResourceIdentityError(f"{field} must be a string, got {type(value).__name__}")

    if not value or not value.strip():
        raise InvalidResourceIdentityError(f"{field} must be a non-empty string")

    if _RESERVED_SEPARATOR in value:
        raise InvalidResourceIdentityError(
            f"{field} must not contain the reserved separator '{_RESERVED_SEPARATOR}': {value!r}"
        )

    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise InvalidResourceIdentityError(f"{field} must not contain control characters: {value!r}")

    return value


class ResourceIdentity(BaseModel):
    """Structured identity for a platform resource.

    A ResourceIdentity is the canonical source of truth for the four
    segments that uniquely identify a resource. Code that needs a flat
    string (log binds, SurrealDB keys, display) uses the ``canonical``
    property. Code that needs structured access uses the fields directly.

    Attributes:
        project_id: Project the resource belongs to.
        provider: Provider catalog name (e.g. ``pragmatiks/gcp``).
        resource: Resource type name (e.g. ``cloudsql/instance``).
        name: Resource instance name.
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    provider: str
    resource: str
    name: str

    @field_validator("project_id", "provider", "resource", "name")
    @classmethod
    def _check_segments(cls, value: str, info: Any) -> str:
        """Reject empty, reserved-separator, and control-character segments.

        Args:
            value: Segment value to validate.
            info: Pydantic validation info carrying the field name.

        Returns:
            The validated segment.
        """
        return _validate_segment(value, info.field_name)

    @model_validator(mode="after")
    def _normalize(self) -> ResourceIdentity:
        """Re-validate every segment after construction.

        Returns:
            The validated :class:`ResourceIdentity` instance.
        """
        for field in _ALLOWED_SEGMENT_NAMES:
            _validate_segment(getattr(self, field), field)
        return self

    @property
    def canonical(self) -> str:
        """Return the canonical ``project::provider::resource::name`` string.

        Returns:
            Flat canonical identifier suitable for display, database keys,
            and log binds.
        """
        return _RESERVED_SEPARATOR.join((self.project_id, self.provider, self.resource, self.name))

    def __str__(self) -> str:
        """Return the canonical string for display purposes."""
        return self.canonical

    def __hash__(self) -> int:
        """Hash on the canonical string so identities can be used in sets.

        Returns:
            Hash of the canonical identity string.
        """
        return hash(self.canonical)

    @classmethod
    def parse(cls, value: str) -> ResourceIdentity:
        """Parse a canonical ``project::provider::resource::name`` string.

        This is the only supported entry point for reading a flat resource
        identifier. There is no legacy slash format and no dual-read path.

        Args:
            value: Canonical identity string.

        Returns:
            Parsed ResourceIdentity.

        Raises:
            InvalidResourceIdentityError: If the value is not exactly four
                reserved-separator-delimited segments or any segment fails
                validation.
        """
        if not isinstance(value, str):
            raise InvalidResourceIdentityError(f"ResourceIdentity.parse expects a string, got {type(value).__name__}")

        parts = value.split(_RESERVED_SEPARATOR)

        if len(parts) != 4:
            raise InvalidResourceIdentityError(
                f"ResourceIdentity.parse expects exactly 4 '::'-delimited segments, got {len(parts)}: {value!r}"
            )

        project_id, provider, resource, name = parts

        return cls(project_id=project_id, provider=provider, resource=resource, name=name)
