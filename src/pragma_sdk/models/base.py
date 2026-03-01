"""Core base classes for resource configuration and lifecycle."""

from __future__ import annotations

import typing
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel
from pydantic import Field as PydanticField
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode

from pragma_sdk.context import apply_resource, get_current_resource_owner, wait_for_resource_state
from pragma_sdk.models.references import (
    Dependency,
    FieldReference,
    Immutable,
    OwnerReference,
    ResourceReference,
    format_resource_id,
)
from pragma_sdk.types import HealthStatus, LifecycleState, LogEntry


def _has_immutable_marker(alias: typing.TypeAliasType) -> bool:
    """Check if a TypeAliasType's underlying value carries the Immutable marker.

    Args:
        alias: A PEP 695 type alias to inspect.

    Returns:
        True if the alias wraps an Annotated type containing an Immutable instance.
    """
    val = alias.__value__

    if typing.get_origin(val) is not Annotated:
        return False

    return any(isinstance(arg, Immutable) for arg in typing.get_args(val)[1:])


def _is_valid_config_field(annotation: Any) -> bool:
    """Check if a type annotation uses a valid Config field type.

    Valid types are Field[T], ImmutableField[T], Dependency[T],
    ImmutableDependency[T], and their Optional/list variants.
    Bare types like str, int, bool are invalid.

    Args:
        annotation: The type annotation to validate.

    Returns:
        True if the annotation is a valid Config field type.
    """
    origin = typing.get_origin(annotation)

    if isinstance(origin, typing.TypeAliasType):
        val = origin.__value__

        if typing.get_origin(val) is Annotated:
            val = typing.get_args(val)[0]

        if typing.get_origin(val) is Union:
            if FieldReference in typing.get_args(val):
                return True

        if isinstance(val, type) and issubclass(val, FieldReference):
            return True

        dep_origin = typing.get_origin(val)

        if dep_origin is not None and isinstance(dep_origin, type) and issubclass(dep_origin, Dependency):
            return True

        if isinstance(val, type) and issubclass(val, Dependency):
            return True

        return False

    if isinstance(origin, type) and issubclass(origin, Dependency):
        return True

    if isinstance(annotation, type) and issubclass(annotation, Dependency):
        return True

    if origin is list:
        args = typing.get_args(annotation)

        if args:
            return _is_valid_config_field(args[0])

        return False

    if origin is Union:
        non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

        if len(non_none_args) == 1:
            return _is_valid_config_field(non_none_args[0])

        if FieldReference in typing.get_args(annotation):
            return True

        return False

    return False


class Config(BaseModel):
    """Base class for resource configuration schemas."""

    model_config = {"extra": "forbid"}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Validate that all fields use required Config field types.

        Raises:
            TypeError: If any field uses a bare type instead of Field[T],
                ImmutableField[T], Dependency[T], or ImmutableDependency[T].
        """
        super().__pydantic_init_subclass__(**kwargs)

        for field_name, field_info in cls.model_fields.items():
            if not _is_valid_config_field(field_info.annotation):
                raise TypeError(
                    f"Config field '{field_name}' on {cls.__name__} must use "
                    f"Field[T], ImmutableField[T], Dependency[T], or "
                    f"ImmutableDependency[T] — bare types are not allowed"
                )

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        """Generate JSON schema with immutable field metadata.

        Extends Pydantic's schema generation to add ``"immutable": true``
        to properties that use ImmutableField or ImmutableDependency.

        Args:
            by_alias: Whether to use field aliases in the schema.
            ref_template: Template for JSON schema $ref values.
            schema_generator: JSON schema generator class to use.
            mode: Validation or serialization mode.
            union_format: Format for union types in the schema.

        Returns:
            JSON schema dictionary with immutable annotations.
        """
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )

        immutable_fields = _collect_immutable_fields(cls)

        if not immutable_fields:
            return schema

        properties = schema.get("properties", {})

        for field_name in immutable_fields:
            if field_name in properties:
                properties[field_name]["immutable"] = True

        _mark_immutable_in_defs(schema, immutable_fields, properties)

        return schema


def _collect_immutable_fields(cls: type[Config]) -> set[str]:
    """Collect field names marked with the Immutable marker.

    Args:
        cls: A Config subclass to inspect.

    Returns:
        Set of field names that have the Immutable marker.
    """
    immutable_fields: set[str] = set()

    for field_name, field_info in cls.model_fields.items():
        annotation = field_info.annotation
        origin = typing.get_origin(annotation)

        if isinstance(origin, typing.TypeAliasType) and _has_immutable_marker(origin):
            immutable_fields.add(field_name)

    return immutable_fields


def _mark_immutable_in_defs(
    schema: dict[str, Any],
    immutable_fields: set[str],
    properties: dict[str, Any],
) -> None:
    """Mark immutable fields in $defs when properties use $ref.

    When a property references a $def (e.g., ``{"$ref": "#/$defs/ImmutableField_str_"}``),
    the immutable marker must be set on the referenced definition.

    Args:
        schema: The full JSON schema dictionary.
        immutable_fields: Set of field names marked as immutable.
        properties: The properties section of the schema.
    """
    defs = schema.get("$defs", {})

    if not defs:
        return

    for field_name in immutable_fields:
        prop = properties.get(field_name, {})
        ref = prop.get("$ref", "")

        if not ref.startswith("#/$defs/"):
            continue

        def_name = ref[len("#/$defs/") :]

        if def_name in defs:
            defs[def_name]["immutable"] = True


class Outputs(BaseModel):
    """Base class for resource outputs produced by lifecycle handlers."""

    model_config = {"extra": "forbid"}


class Resource[ConfigT: Config, OutputsT: Outputs](BaseModel):
    """Base class for provider-managed resources with lifecycle handlers.

    Lifecycle handlers (on_create, on_update, on_delete) must be idempotent.
    Events may be redelivered if the runtime crashes after processing but
    before acknowledging the message. Design handlers to produce the same
    result when called multiple times with the same input.
    """

    provider: ClassVar[str]
    resource: ClassVar[str]

    name: str
    config: ConfigT
    dependencies: list[ResourceReference] = PydanticField(default_factory=list)
    owner_references: list[OwnerReference] = PydanticField(default_factory=list)
    outputs: OutputsT | None = None
    error: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    tags: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def id(self) -> str:
        """Unique resource ID: resource:{provider}_{resource}_{name}."""
        return format_resource_id(self.provider, self.resource, self.name)

    async def on_create(self) -> OutputsT:
        """Handle resource creation."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_create()")

    async def on_update(self, previous_config: ConfigT) -> OutputsT:
        """Handle resource update with access to the previous configuration."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_update()")

    async def on_delete(self) -> None:
        """Handle resource deletion."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_delete()")

    async def logs(
        self,
        since: datetime | None = None,
        tail: int = 100,
    ) -> AsyncIterator[LogEntry]:
        """Override to provide logs for this resource.

        Args:
            since: Only return logs after this timestamp.
            tail: Maximum number of log entries to return.

        Yields:
            Log entries for this resource.

        Raises:
            NotImplementedError: Subclass must implement this method.
        """
        raise NotImplementedError("Subclass must implement logs()")
        yield  # For type checker

    async def health(self) -> HealthStatus:
        """Override to provide health status for this resource.

        Returns:
            Health status. Default implementation returns healthy.
        """
        return HealthStatus(status="healthy")

    def set_owner(self, owner: Resource) -> Resource:
        """Set this resource's owner for lifecycle management.

        Establishes an ownership relationship where the owner resource controls
        this resource's lifecycle. When the owner is deleted, owned resources
        can be automatically cleaned up via cascading deletes.

        Args:
            owner: Parent resource that will own this resource.

        Returns:
            Self for method chaining.
        """
        ref = OwnerReference(
            provider=owner.provider,
            resource=owner.resource,
            name=owner.name,
        )
        if ref not in self.owner_references:
            self.owner_references.append(ref)
        return self

    async def apply(self) -> Resource[ConfigT, OutputsT]:
        """Apply this resource through the API.

        Sends the resource to the API for creation or update. The API will
        validate, persist, and emit lifecycle events for provider processing.
        The resource's lifecycle_state will be set to PENDING by the API.

        Call this from within provider lifecycle handlers to create subresources.
        The owner is automatically set from the current runtime context (the
        resource whose lifecycle handler is executing). After apply(), call
        wait_ready() to wait for the resource to be processed.

        Returns:
            Self for method chaining.

        Example:
            ```python
            async def on_create(self):
                db = DatabaseResource(name=f"{self.name}-db", config=DbConfig(...))
                await db.apply()  # Owner automatically set from context
                await db.wait_ready(timeout=120.0)
                return AppOutputs(db_url=db.outputs.connection_url)
            ```
        """
        current_owner = get_current_resource_owner()
        if current_owner is not None and current_owner not in self.owner_references:
            self.owner_references.append(current_owner)

        resource_data = {
            "provider": self.provider,
            "resource": self.resource,
            "name": self.name,
            "config": self.config.model_dump(),
            "owner_references": [ref.model_dump() for ref in self.owner_references],
        }
        if self.tags:
            resource_data["tags"] = self.tags

        await apply_resource(resource_data)
        self.lifecycle_state = LifecycleState.PENDING
        return self

    async def wait_ready(self, timeout: float = 60.0) -> Resource[ConfigT, OutputsT]:
        """Wait for this resource to reach READY state.

        Subscribes to NATS state notifications and waits for the resource
        to transition to READY. Updates self with the outputs from the
        state notification.

        Args:
            timeout: Maximum seconds to wait before raising TimeoutError.

        Returns:
            Self with updated outputs and lifecycle_state.

        Example:
            ```python
            async def on_create(self):
                db = DatabaseResource(name=f"{self.name}-db", config=DbConfig(...))
                await db.apply()  # Owner automatically set from context
                await db.wait_ready(timeout=120.0)
                return AppOutputs(db_url=db.outputs.connection_url)
            ```
        """
        data = await wait_for_resource_state(self.id, LifecycleState.READY, timeout)

        self.lifecycle_state = LifecycleState(data.get("lifecycle_state", "ready"))

        outputs_data = data.get("outputs")
        if outputs_data is not None:
            outputs_type = self._outputs_type()
            if outputs_type is not None:
                self.outputs = outputs_type.model_validate(outputs_data)  # type: ignore[assignment]
            else:
                self.outputs = outputs_data

        return self

    def _outputs_type(self) -> type[Outputs] | None:
        """Get the OutputsT type from the model fields annotation.

        Returns:
            The Outputs subclass type or None if not determinable.
        """
        outputs_field = self.__class__.model_fields.get("outputs")
        if outputs_field is None:
            return None

        annotation = outputs_field.annotation
        if annotation is None:
            return None

        origin = typing.get_origin(annotation)
        if origin is type(None):
            return None

        if origin is typing.Union:
            args = typing.get_args(annotation)
            for arg in args:
                if arg is not type(None) and isinstance(arg, type) and issubclass(arg, Outputs):
                    return arg
            return None

        if isinstance(annotation, type) and issubclass(annotation, Outputs):
            return annotation

        return None
