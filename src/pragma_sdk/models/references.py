"""Reference types for resource dependencies and ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, PrivateAttr
from pydantic import Field as PydanticField


if TYPE_CHECKING:
    from pragma_sdk.models.base import Resource


def format_resource_id(provider: str, resource: str, name: str) -> str:
    """Format a unique resource ID.

    Slashes in resource type are replaced with underscores for URL safety.

    Returns:
        Resource ID as `resource:{provider}_{resource}_{name}`.
    """
    resource_normalized = resource.replace("/", "_")
    return f"resource:{provider}_{resource_normalized}_{name}"


class ResourceReference(BaseModel):
    """Reference to another resource for dependency tracking."""

    provider: str
    resource: str
    name: str

    @property
    def id(self) -> str:
        """Unique resource ID for the referenced resource."""
        return format_resource_id(self.provider, self.resource, self.name)


class OwnerReference(BaseModel):
    """Reference to a resource that owns this resource for lifecycle coordination.

    Used for cascading deletes and ownership tracking. When an owner resource
    is deleted, owned resources can be automatically cleaned up.

    A resource can have multiple owners (rare but valid for shared resources).
    """

    provider: str
    resource: str
    name: str

    @property
    def id(self) -> str:
        """Unique resource ID for the owner resource."""
        return format_resource_id(self.provider, self.resource, self.name)


class FieldReference(ResourceReference):
    """Reference to a specific output field of another resource."""

    field: str


class Dependency[ResourceT: "Resource"](BaseModel):
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

    dependency_marker: bool = PydanticField(default=True, alias="__dependency__", serialization_alias="__dependency__")
    provider: str
    resource: str
    name: str

    _resolved: ResourceT | None = PrivateAttr(default=None)

    @property
    def id(self) -> str:
        """Unique resource ID for the referenced resource."""
        return format_resource_id(self.provider, self.resource, self.name)

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


type ImmutableField[T] = Annotated[T | FieldReference, Immutable()]
"""Config field that accepts a direct value or FieldReference and is immutable after creation."""

type ImmutableDependency[ResourceT: "Resource"] = Annotated[Dependency[ResourceT], Immutable()]
"""Typed dependency that is immutable after resource creation."""


def is_dependency_marker(value: Any) -> bool:
    """Check if a value is a serialized Dependency marker.

    When Dependency[T] is serialized (e.g., sent via API), it becomes a dict
    with __dependency__=True and provider/resource/name keys. This function
    detects such markers regardless of whether they've been resolved.

    Args:
        value: Any value to check.

    Returns:
        True if value is a dict with the required dependency keys and __dependency__=True.
    """
    if not isinstance(value, dict):
        return False
    required = {"__dependency__", "provider", "resource", "name"}
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
