"""Reference types for resource dependencies and ownership.

References carry the four segments of a :class:`ResourceIdentity` directly
as fields so that wire serialization stays flat, and expose a computed
:attr:`identity` property plus a ``canonical`` string for consumers that
want the structured form or a flat key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, PrivateAttr, field_validator
from pydantic import Field as PydanticField

from pragma_sdk.models.identity import ResourceIdentity, _validate_segment


if TYPE_CHECKING:
    from pragma_sdk.models.base import Resource


class _ResourceIdentityFields(BaseModel):
    """Mixin that validates the four identity segments on construction.

    Reference models carry ``project_id``, ``provider``, ``resource``, and
    ``name`` as flat fields for wire compatibility. This mixin ensures each
    reference type enforces the same segment rules as
    :class:`ResourceIdentity` without duplicating validators.
    """

    project_id: str
    provider: str
    resource: str
    name: str

    @field_validator("project_id", "provider", "resource", "name")
    @classmethod
    def _check_segments(cls, value: str, info: Any) -> str:
        return _validate_segment(value, info.field_name)

    @property
    def identity(self) -> ResourceIdentity:
        """Return the structured :class:`ResourceIdentity` for this reference."""
        return ResourceIdentity(
            project_id=self.project_id,
            provider=self.provider,
            resource=self.resource,
            name=self.name,
        )

    @property
    def canonical(self) -> str:
        """Return the canonical ``project::provider::resource::name`` string."""
        return self.identity.canonical


class ResourceReference(_ResourceIdentityFields):
    """Reference to another resource for dependency tracking."""

    @property
    def id(self) -> str:
        """Canonical identifier for the referenced resource."""
        return self.canonical


class OwnerReference(_ResourceIdentityFields):
    """Reference to a resource that owns this resource for lifecycle coordination.

    Used for cascading deletes and ownership tracking. When an owner resource
    is deleted, owned resources can be automatically cleaned up.

    A resource can have multiple owners (rare but valid for shared resources).
    """

    @property
    def id(self) -> str:
        """Canonical identifier for the owner resource."""
        return self.canonical


class FieldReference(ResourceReference):
    """Reference to a specific output field of another resource."""

    field: str


class Dependency[ResourceT: "Resource"](_ResourceIdentityFields):
    """Typed dependency on another resource for whole-instance access.

    Use this when you need access to the full resource object (config, outputs,
    methods) rather than just a single field value. Call resolve() in lifecycle
    handlers to get the typed resource instance.

    Example:
        ```python
        class AppConfig(Config):
            database: Dependency[DatabaseResource]

        async def on_create(self):
            db = await self.config.database.resolve()
            print(db.outputs.connection_url)
        ```
    """

    model_config = {"populate_by_name": True}

    dependency_marker: bool = PydanticField(
        default=True,
        alias="__dependency__",
        serialization_alias="__dependency__",
    )

    _resolved: ResourceT | None = PrivateAttr(default=None)

    @property
    def id(self) -> str:
        """Canonical identifier for the referenced resource."""
        return self.canonical

    async def resolve(self) -> ResourceT:
        """Get the resolved resource instance.

        The runtime injects resolved dependencies before calling lifecycle
        handlers. This method returns that pre-resolved instance.

        Returns:
            The typed resource with access to its config, outputs, and methods.

        Raises:
            RuntimeError: If the dependency was not resolved by the runtime.
                This happens when the dependent resource is not yet READY.
        """
        if self._resolved is not None:
            return self._resolved
        raise RuntimeError(f"Dependency '{self.id}' not resolved. The dependent resource may not be READY yet.")


type Field[T] = T | FieldReference
"""Config field that accepts a direct value or a FieldReference."""


class Immutable:
    """Marker for config fields that cannot be changed after resource creation.

    Used as metadata in Annotated types to flag fields as immutable.
    The API enforces immutability by rejecting updates that modify these fields.
    """


class Sensitive:
    """Marker for fields that contain sensitive data (secrets, credentials, tokens).

    Used as metadata in Annotated types to flag fields as sensitive.
    The API redacts sensitive fields in responses unless explicitly revealed.
    """


type ImmutableField[T] = Annotated[T | FieldReference, Immutable()]
"""Config field that accepts a direct value or FieldReference and is immutable after creation."""

type ImmutableDependency[ResourceT: "Resource"] = Annotated[Dependency[ResourceT], Immutable()]
"""Typed dependency that is immutable after resource creation."""

type SensitiveField[T] = Annotated[T | FieldReference, Sensitive()]
"""Config field that accepts a direct value or FieldReference and is redacted in responses."""

type SensitiveDependency[ResourceT: "Resource"] = Annotated[Dependency[ResourceT], Sensitive()]
"""Typed dependency that is redacted in responses."""

type ImmutableSensitiveField[T] = Annotated[T | FieldReference, Immutable(), Sensitive()]
"""Config field that is both immutable after creation and redacted in responses."""

type SensitiveOutput[T] = Annotated[T, Sensitive()]
"""Output field that is redacted in responses. No FieldReference union (outputs are produced, not configured)."""


def is_dependency_marker(value: Any) -> bool:
    """Check if a value is a serialized Dependency marker.

    When Dependency[T] is serialized (e.g., sent via API), it becomes a dict
    with __dependency__=True and project_id/provider/resource/name keys. This
    function detects such markers regardless of whether they've been resolved.

    Args:
        value: Any value to check.

    Returns:
        True if value is a dict with the required dependency keys and __dependency__=True.
    """
    if not isinstance(value, dict):
        return False
    required = {"__dependency__", "project_id", "provider", "resource", "name"}
    return required.issubset(value.keys()) and value.get("__dependency__") is True


def is_field_ref_marker(value: Any) -> bool:
    """Check if a value is a serialized FieldReference marker.

    When a FieldReference is resolved, it becomes a dict with __field_ref__=True,
    a 'ref' key containing the original reference, and a 'resolved_value' key.
    This function detects such markers for re-resolution during propagation.

    Args:
        value: Any value to check.

    Returns:
        True if value is a __field_ref__ marker dict.
    """
    if not isinstance(value, dict):
        return False
    return value.get("__field_ref__") is True and "ref" in value and "resolved_value" in value
