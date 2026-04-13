"""Core base classes for resource configuration and lifecycle."""

from __future__ import annotations

import re
import types
import typing
import warnings
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, ClassVar, ForwardRef, Literal, Union

from pydantic import BaseModel
from pydantic import Field as PydanticField
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode

from pragma_sdk.context import apply_resource, get_current_resource_owner, get_provider_name, wait_for_resource_state
from pragma_sdk.models.identity import ResourceIdentity
from pragma_sdk.models.references import (
    Dependency,
    FieldReference,
    Immutable,
    OwnerReference,
    ResourceReference,
    Sensitive,
)
from pragma_sdk.types import (
    CopyContext,
    CopyResult,
    HealthStatus,
    LifecycleState,
    LogEntry,
    PatchDefinition,
    PatchResult,
)


def _is_union_origin(origin: Any) -> bool:
    """Check if a type origin represents a Union.

    On Python 3.13, ``typing.Union`` and ``types.UnionType`` are distinct.
    PEP 604 syntax (``X | Y``) produces ``types.UnionType``, while
    ``typing.Union[X, Y]`` produces ``typing.Union``. Both must be handled.

    On Python 3.14+, they are the same object so this is a no-op safety net.

    Args:
        origin: The result of ``typing.get_origin(some_annotation)``.

    Returns:
        True if the origin is either ``typing.Union`` or ``types.UnionType``.
    """
    return origin is Union or origin is types.UnionType


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


def _has_sensitive_marker(alias: typing.TypeAliasType) -> bool:
    """Check if a TypeAliasType's underlying value carries the Sensitive marker.

    Args:
        alias: A PEP 695 type alias to inspect.

    Returns:
        True if the alias wraps an Annotated type containing a Sensitive instance.
    """
    val = alias.__value__

    if typing.get_origin(val) is not Annotated:
        return False

    return any(isinstance(arg, Sensitive) for arg in typing.get_args(val)[1:])


_DEPENDENCY_FORWARD_REF_RE = re.compile(
    r"(?<![A-Za-z_])(?:Immutable|Sensitive)?Dependency\[\s*['\"]?([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)['\"]?\s*\]"
)


def _find_dependency_forward_ref(annotation: Any) -> str | None:
    """Find a string forward reference inside a Dependency type parameter.

    Checks whether a type annotation contains a Dependency (or variant like
    ImmutableDependency, SensitiveDependency) parameterized with a string
    forward reference instead of an actual class. Recurses into list and
    Optional/Union wrappers.

    Also handles ``ForwardRef`` annotations that arise when
    ``from __future__ import annotations`` causes the entire annotation
    string to be deferred and Pydantic cannot resolve it.

    Args:
        annotation: The type annotation to inspect.

    Returns:
        The forward reference string if found, None otherwise.
    """
    if isinstance(annotation, ForwardRef):
        match = _DEPENDENCY_FORWARD_REF_RE.search(annotation.__forward_arg__)

        if match:
            return match.group(1)

        return None

    origin = typing.get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, Dependency):
        meta = getattr(annotation, "__pydantic_generic_metadata__", None)

        if meta and meta.get("args"):
            resource_arg = meta["args"][0]

            if isinstance(resource_arg, str):
                return resource_arg

        return None

    if isinstance(origin, typing.TypeAliasType):
        alias_args = typing.get_args(annotation)

        for arg in alias_args:
            if isinstance(arg, str):
                val = origin.__value__
                inner_origin = typing.get_origin(val)

                if inner_origin is Annotated:
                    inner_type = typing.get_args(val)[0]
                else:
                    inner_type = val

                dep_origin = typing.get_origin(inner_type)

                if dep_origin is not None and isinstance(dep_origin, type) and issubclass(dep_origin, Dependency):
                    return arg

                if isinstance(inner_type, type) and issubclass(inner_type, Dependency):
                    return arg

        return None

    if origin is list:
        args = typing.get_args(annotation)

        if args:
            return _find_dependency_forward_ref(args[0])

        return None

    if _is_union_origin(origin):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue

            result = _find_dependency_forward_ref(arg)

            if result is not None:
                return result

        return None

    return None


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

        if _is_union_origin(typing.get_origin(val)):
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

    if _is_union_origin(origin):
        non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

        if len(non_none_args) == 1:
            return _is_valid_config_field(non_none_args[0])

        if FieldReference in typing.get_args(annotation):
            return True

        if all(_is_valid_config_field(arg) for arg in non_none_args):
            return True

        return False

    return False


def _build_field_alias_map(cls: type[BaseModel], by_alias: bool) -> dict[str, str]:
    """Map Python field names to JSON property names, respecting aliases.

    Pydantic generates property keys using aliases when ``by_alias=True``.
    This function builds a mapping so that schema post-processing can look up
    properties by their aliased key instead of the Python attribute name.

    Args:
        cls: A Pydantic model class to inspect.
        by_alias: Whether the schema was generated with aliases.

    Returns:
        Mapping from Python field name to the JSON property key used in the schema.
        Fields without aliases map to themselves.
    """
    alias_map: dict[str, str] = {}

    if not by_alias:
        return alias_map

    for field_name, field_info in cls.model_fields.items():
        prop_key = field_info.serialization_alias or field_info.alias or field_name
        alias_map[field_name] = prop_key

    return alias_map


_BASE_MODEL_CLASSES = {"Config", "Outputs"}


def _has_own_fields(cls: type[BaseModel]) -> bool:
    """Check if a model class defines its own fields (not just inherited ones).

    Args:
        cls: A Pydantic BaseModel subclass to inspect.

    Returns:
        True if the class has annotations that define new model fields.
    """
    own_annotations = set(cls.__annotations__) if "__annotations__" in cls.__dict__ else set()
    parent_annotations: set[str] = set()

    for parent in cls.__mro__[1:]:
        if hasattr(parent, "__annotations__"):
            parent_annotations.update(parent.__annotations__)

    own_field_names = own_annotations - parent_annotations

    return bool(own_field_names & set(cls.model_fields))


def _validate_model_docstring(cls: type[BaseModel], kind: str) -> None:
    """Validate that a Config or Outputs subclass has a docstring.

    Skips base classes and classes with no fields of their own. Raises
    TypeError for missing docstrings and warns for missing Attributes section.

    Args:
        cls: A Config or Outputs subclass to validate.
        kind: Label for error messages ("Config" or "Outputs").

    Raises:
        TypeError: If the subclass defines fields but has no docstring.
    """
    if cls.__name__ in _BASE_MODEL_CLASSES:
        return

    if not _has_own_fields(cls):
        return

    if not cls.__doc__:
        raise TypeError(f"{kind} class '{cls.__name__}' must have a docstring.")

    if "Attributes:" not in cls.__doc__:
        warnings.warn(
            f"{kind} class '{cls.__name__}' should document fields in a Google-style Attributes section.",
            stacklevel=3,
        )


class Config(BaseModel):
    """Base class for resource configuration schemas."""

    model_config = {"extra": "forbid"}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Validate field types and docstring on Config subclasses.

        Raises:
            TypeError: If any field uses a bare type instead of Field[T],
                ImmutableField[T], Dependency[T], or ImmutableDependency[T].
            TypeError: If any Dependency field uses a string forward reference
                instead of a direct class reference.
            TypeError: If the subclass defines fields but has no docstring.
        """
        super().__pydantic_init_subclass__(**kwargs)

        for field_name, field_info in cls.model_fields.items():
            forward_ref = _find_dependency_forward_ref(field_info.annotation)

            if forward_ref is not None:
                raise TypeError(
                    f'Forward reference "{forward_ref}" in Dependency declaration '
                    f"on {cls.__name__}.{field_name} is not supported. "
                    f"Move {forward_ref} above {cls.__name__} or import it directly."
                )

            if not _is_valid_config_field(field_info.annotation):
                raise TypeError(
                    f"Config field '{field_name}' on {cls.__name__} must use "
                    f"Field[T], ImmutableField[T], SensitiveField[T], "
                    f"ImmutableSensitiveField[T], Dependency[T], "
                    f"ImmutableDependency[T], or SensitiveDependency[T] "
                    f"— bare types are not allowed"
                )

        _validate_model_docstring(cls, "Config")

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
        """Generate JSON schema with immutable and sensitive field metadata.

        Extends Pydantic's schema generation to add ``"immutable": true``
        and/or ``"sensitive": true`` to properties that use the corresponding
        marker types.

        Args:
            by_alias: Whether to use field aliases in the schema.
            ref_template: Template for JSON schema $ref values.
            schema_generator: JSON schema generator class to use.
            mode: Validation or serialization mode.
            union_format: Format for union types in the schema.

        Returns:
            JSON schema dictionary with immutable and sensitive annotations.
        """
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )

        immutable_fields = _collect_immutable_fields(cls)
        sensitive_fields = _collect_sensitive_fields(cls)

        if not immutable_fields and not sensitive_fields:
            return schema

        alias_map = _build_field_alias_map(cls, by_alias)
        properties = schema.get("properties", {})

        for field_name in immutable_fields:
            prop_key = alias_map.get(field_name, field_name)

            if prop_key in properties:
                properties[prop_key]["immutable"] = True

        for field_name in sensitive_fields:
            prop_key = alias_map.get(field_name, field_name)

            if prop_key in properties:
                properties[prop_key]["sensitive"] = True

        aliased_immutable = {alias_map.get(f, f) for f in immutable_fields}
        aliased_sensitive = {alias_map.get(f, f) for f in sensitive_fields}

        _mark_immutable_in_defs(schema, aliased_immutable, properties)
        _mark_sensitive_in_defs(schema, aliased_sensitive, properties)

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
        elif _is_union_origin(origin):
            non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

            for arg in non_none_args:
                arg_origin = typing.get_origin(arg)

                if isinstance(arg_origin, typing.TypeAliasType) and _has_immutable_marker(arg_origin):
                    immutable_fields.add(field_name)
                    break

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


def _collect_sensitive_fields(cls: type[Config]) -> set[str]:
    """Collect field names marked with the Sensitive marker.

    Args:
        cls: A Config subclass to inspect.

    Returns:
        Set of field names that have the Sensitive marker.
    """
    sensitive_fields: set[str] = set()

    for field_name, field_info in cls.model_fields.items():
        annotation = field_info.annotation
        origin = typing.get_origin(annotation)

        if isinstance(origin, typing.TypeAliasType) and _has_sensitive_marker(origin):
            sensitive_fields.add(field_name)
        elif _is_union_origin(origin):
            non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

            for arg in non_none_args:
                arg_origin = typing.get_origin(arg)

                if isinstance(arg_origin, typing.TypeAliasType) and _has_sensitive_marker(arg_origin):
                    sensitive_fields.add(field_name)
                    break

    return sensitive_fields


def _mark_sensitive_in_defs(
    schema: dict[str, Any],
    sensitive_fields: set[str],
    properties: dict[str, Any],
) -> None:
    """Mark sensitive fields in $defs when properties use $ref.

    When a property references a $def (e.g., ``{"$ref": "#/$defs/SensitiveField_str_"}``),
    the sensitive marker must be set on the referenced definition.

    Args:
        schema: The full JSON schema dictionary.
        sensitive_fields: Set of field names marked as sensitive.
        properties: The properties section of the schema.
    """
    defs = schema.get("$defs", {})

    if not defs:
        return

    for field_name in sensitive_fields:
        prop = properties.get(field_name, {})
        ref = prop.get("$ref", "")

        if not ref.startswith("#/$defs/"):
            continue

        def_name = ref[len("#/$defs/") :]

        if def_name in defs:
            defs[def_name]["sensitive"] = True


def _collect_sensitive_output_fields(cls: type[Outputs]) -> set[str]:
    """Collect output field names marked with the Sensitive marker.

    Simpler than the Config version because output fields use ``SensitiveOutput[T]``
    which is ``Annotated[T, Sensitive()]`` without the FieldReference union.

    Args:
        cls: An Outputs subclass to inspect.

    Returns:
        Set of field names that have the Sensitive marker.
    """
    sensitive_fields: set[str] = set()

    for field_name, field_info in cls.model_fields.items():
        annotation = field_info.annotation
        origin = typing.get_origin(annotation)

        if isinstance(origin, typing.TypeAliasType) and _has_sensitive_marker(origin):
            sensitive_fields.add(field_name)
        elif origin is Annotated:
            args = typing.get_args(annotation)

            if any(isinstance(arg, Sensitive) for arg in args[1:]):
                sensitive_fields.add(field_name)
        elif _is_union_origin(origin):
            non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

            for arg in non_none_args:
                arg_origin = typing.get_origin(arg)

                if isinstance(arg_origin, typing.TypeAliasType) and _has_sensitive_marker(arg_origin):
                    sensitive_fields.add(field_name)
                    break

                if arg_origin is Annotated:
                    arg_args = typing.get_args(arg)

                    if any(isinstance(a, Sensitive) for a in arg_args[1:]):
                        sensitive_fields.add(field_name)
                        break

    return sensitive_fields


class Outputs(BaseModel):
    """Base class for resource outputs produced by lifecycle handlers."""

    model_config = {"extra": "forbid"}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Validate docstring on Outputs subclasses."""
        super().__pydantic_init_subclass__(**kwargs)
        _validate_model_docstring(cls, "Outputs")

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
        """Generate JSON schema with sensitive output field metadata.

        Extends Pydantic's schema generation to add ``"sensitive": true``
        to output properties that use SensitiveOutput.

        Args:
            by_alias: Whether to use field aliases in the schema.
            ref_template: Template for JSON schema $ref values.
            schema_generator: JSON schema generator class to use.
            mode: Validation or serialization mode.
            union_format: Format for union types in the schema.

        Returns:
            JSON schema dictionary with sensitive annotations.
        """
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )

        sensitive_fields = _collect_sensitive_output_fields(cls)

        if not sensitive_fields:
            return schema

        alias_map = _build_field_alias_map(cls, by_alias)
        properties = schema.get("properties", {})

        for field_name in sensitive_fields:
            prop_key = alias_map.get(field_name, field_name)

            if prop_key in properties:
                properties[prop_key]["sensitive"] = True

        return schema


class Resource[ConfigT: Config, OutputsT: Outputs](BaseModel):
    """Base class for provider-managed resources with lifecycle handlers.

    Lifecycle handlers (on_create, on_update, on_delete) must be idempotent.
    Events may be redelivered if the runtime crashes after processing but
    before acknowledging the message. Design handlers to produce the same
    result when called multiple times with the same input.
    """

    resource: ClassVar[str]

    project_id: str
    name: str
    config: ConfigT
    dependencies: list[ResourceReference] = PydanticField(default_factory=list)
    owner_references: list[OwnerReference] = PydanticField(default_factory=list)
    outputs: OutputsT | None = None
    error: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    tags: list[str] | None = None
    provider_version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    managed_by: Literal["user", "platform"] | None = PydanticField(
        default=None,
        description=(
            "Ownership of the resource. 'user' means a human or API-key caller owns this "
            "resource and controls its lifecycle. 'platform' means pragma-os owns it "
            "(platform agents, tier resources, platform-default LLM provider) and it is "
            "off-limits to user write paths -- PATCH, PUT, DELETE, copy, and export are "
            "rejected by the API. None is the default for unmanaged legacy resources and "
            "will be backfilled by a future migration."
        ),
    )

    @property
    def provider(self) -> str:
        """Catalog name of the provider that manages this resource.

        Reads the provider name from the runtime context. The runtime sets
        this before invoking lifecycle methods.

        Returns:
            Provider catalog name (e.g., "pragmatiks/gcp").

        Raises:
            RuntimeError: If called outside a runtime context where the
                provider name has not been set.
        """
        name = get_provider_name()

        if name is None:
            raise RuntimeError("provider name not set — this must be called within a runtime context")

        return name

    @property
    def identity(self) -> ResourceIdentity:
        """Return the structured identity for this resource.

        Returns:
            :class:`ResourceIdentity` composed from ``project_id``, ``provider``,
            ``resource``, and ``name``.
        """
        return ResourceIdentity(
            project_id=self.project_id,
            provider=self.provider,
            resource=self.resource,
            name=self.name,
        )

    @property
    def id(self) -> str:
        """Canonical ``project::provider::resource::name`` identifier."""
        return self.identity.canonical

    async def on_create(self) -> OutputsT:
        """Handle resource creation."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_create()")

    async def on_update(self, previous_config: ConfigT) -> OutputsT:
        """Handle resource update with access to the previous configuration."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_update()")

    async def on_delete(self) -> None:
        """Handle resource deletion."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_delete()")

    async def on_copy(self, context: CopyContext) -> CopyResult:
        """Handle resource duplication as part of a subgraph copy operation.

        Override this method to control how the resource duplicates itself.
        Stateless resources (config-only) can return a modified config directly.
        Stateful resources (data-bearing) may need to clone underlying data
        (e.g., create a new database, copy vector indices).

        The default implementation raises NotImplementedError, indicating the
        resource type does not support copying. Use ``supports_copy()`` to check
        before calling.

        Args:
            context: Copy context including tags, target name, strategy, and
                provider-specific metadata.

        Returns:
            CopyResult with the configuration for the new copied resource.

        Raises:
            NotImplementedError: If the resource type does not support copying.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support on_copy()")

    async def on_patch(self, patch: PatchDefinition) -> PatchResult:
        """Handle applying a patch (migration script, schema diff) to this resource.

        Override this method to control how patches are applied to the resource.
        Patches have compatibility constraints that are checked before this method
        is called -- the resource is guaranteed to satisfy all constraints.

        The default implementation raises NotImplementedError, indicating the
        resource type does not support patching. Use ``supports_patch()`` to check
        before calling.

        Args:
            patch: Patch definition including the payload, constraints, and metadata.

        Returns:
            PatchResult indicating success/failure and any modified config/outputs.

        Raises:
            NotImplementedError: If the resource type does not support patching.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support on_patch()")

    @classmethod
    def supports_copy(cls) -> bool:
        """Check if this resource type implements on_copy.

        Returns:
            True if the resource subclass overrides on_copy from the base Resource.
        """
        return cls.on_copy is not Resource.on_copy

    @classmethod
    def supports_patch(cls) -> bool:
        """Check if this resource type implements on_patch.

        Returns:
            True if the resource subclass overrides on_patch from the base Resource.
        """
        return cls.on_patch is not Resource.on_patch

    @classmethod
    def upgrade(cls, config: dict, outputs: dict | None) -> tuple[dict, dict | None]:
        """Migrate config and outputs from the previous provider version.

        Called during provider upgrade for each existing resource of this type.
        Override to transform config/outputs when the schema changes between versions.
        Must be defined on every version, even if no-op.

        Args:
            config: Resource config dict from the previous version.
            outputs: Resource outputs dict from the previous version, or None
                if the resource has not yet produced outputs.

        Returns:
            Tuple of (migrated_config, migrated_outputs).
        """
        return config, outputs

    @classmethod
    def downgrade(cls, config: dict, outputs: dict | None) -> tuple[dict, dict | None]:
        """Migrate config and outputs back to the previous provider version.

        Called during provider downgrade for each existing resource of this type.
        Override to reverse the transformations applied by upgrade().
        Must be defined on every version, even if no-op.

        Args:
            config: Resource config dict from the current version.
            outputs: Resource outputs dict from the current version, or None
                if the resource has not yet produced outputs.

        Returns:
            Tuple of (downgraded_config, downgraded_outputs).
        """
        return config, outputs

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
            project_id=owner.project_id,
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
            "project_id": self.project_id,
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

        if _is_union_origin(origin):
            args = typing.get_args(annotation)
            for arg in args:
                if arg is not type(None) and isinstance(arg, type) and issubclass(arg, Outputs):
                    return arg
            return None

        if isinstance(annotation, type) and issubclass(annotation, Outputs):
            return annotation

        return None
