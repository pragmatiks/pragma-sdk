"""Reference types for resource dependencies and ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, PrivateAttr
from pydantic import Field as PydanticField


if TYPE_CHECKING:
    from pragma_sdk.models.base import Resource


def _validate_project_id_segment(project_id: str) -> None:
    """Reject project IDs that would break slash-separated resource IDs.

    Args:
        project_id: Project identifier to validate.

    Raises:
        ValueError: If the project_id is empty or contains a slash.
    """
    if not project_id:
        raise ValueError("project_id must be a non-empty string")

    if "/" in project_id:
        raise ValueError(f"project_id must not contain '/': {project_id!r}")


def format_resource_id(project_id: str, provider: str, resource: str, name: str) -> str:
    """Format an external user-facing resource ID.

    Uses slash-separated format suitable for display and API paths. The
    ``project_id`` is the first segment so identities do not collapse when
    the same provider/resource/name combination exists in multiple projects.
    Slashes inside the resource type are replaced with underscores for safety.

    Args:
        project_id: Project the resource lives in.
        provider: Provider name.
        resource: Resource type name.
        name: Resource instance name.

    Returns:
        Resource ID as ``{project_id}/{provider}/{resource}/{name}``.
    """
    _validate_project_id_segment(project_id)
    resource_normalized = resource.replace("/", "_")
    return f"{project_id}/{provider}/{resource_normalized}/{name}"


def format_internal_resource_id(project_id: str, provider: str, resource: str, name: str) -> str:
    """Format an internal SurrealDB resource ID.

    Uses underscore-separated format with ``resource:`` prefix for database
    keys. The ``project_id`` is included as the first segment so keys do not
    collide across projects.

    Args:
        project_id: Project the resource lives in.
        provider: Provider name.
        resource: Resource type name.
        name: Resource instance name.

    Returns:
        Resource ID as ``resource:{project_id}_{provider}_{resource}_{name}``.
    """
    _validate_project_id_segment(project_id)
    resource_normalized = resource.replace("/", "_")
    return f"resource:{project_id}_{provider}_{resource_normalized}_{name}"


class ResourceReference(BaseModel):
    """Reference to another resource for dependency tracking.

    References carry the ``project_id`` explicitly so cross-project
    dependencies are representable. When constructed from a running
    resource, callers should pass the referring resource's ``project_id``
    to stay within the same project.
    """

    project_id: str
    provider: str
    resource: str
    name: str

    @property
    def id(self) -> str:
        """Unique resource ID for the referenced resource."""
        return format_resource_id(self.project_id, self.provider, self.resource, self.name)


class OwnerReference(BaseModel):
    """Reference to a resource that owns this resource for lifecycle coordination.

    Used for cascading deletes and ownership tracking. When an owner resource
    is deleted, owned resources can be automatically cleaned up.

    A resource can have multiple owners (rare but valid for shared resources).
    The ``project_id`` is explicit so owners in a different project from the
    owned resource can be represented.
    """

    project_id: str
    provider: str
    resource: str
    name: str

    @property
    def id(self) -> str:
        """Unique resource ID for the owner resource."""
        return format_resource_id(self.project_id, self.provider, self.resource, self.name)


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
    project_id: str
    provider: str
    resource: str
    name: str

    _resolved: ResourceT | None = PrivateAttr(default=None)

    @property
    def id(self) -> str:
        """Unique resource ID for the referenced resource."""
        return format_resource_id(self.project_id, self.provider, self.resource, self.name)

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
    with __dependency__=True and provider/resource/name keys. This function
    detects such markers regardless of whether they've been resolved.

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
