"""Core base classes for resource configuration and lifecycle."""

from __future__ import annotations

import re
import types
import typing
import warnings
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, ClassVar, ForwardRef, Literal, Self, Union

import structlog
from pydantic import BaseModel
from pydantic import Field as PydanticField
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode

from pragma_sdk.context import apply_resource, compute_defining_provider, read_resource
from pragma_sdk.models.children import ChildrenPending
from pragma_sdk.models.declared_keys import compute_declared_keys
from pragma_sdk.models.file import FileReference
from pragma_sdk.models.identity import ResourceIdentity
from pragma_sdk.models.references import (
    Dependency,
    FieldReference,
    Immutable,
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


logger = structlog.get_logger()


def is_union_origin(origin: Any) -> bool:
    """Check whether a type origin is a union, from either ``typing.Union[X, Y]`` or ``X | Y``.

    Args:
        origin: The result of ``typing.get_origin(some_annotation)``.

    Returns:
        True if the origin is ``typing.Union`` or ``types.UnionType``.
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

    if is_union_origin(origin):
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue

            result = _find_dependency_forward_ref(arg)

            if result is not None:
                return result

        return None

    return None


def _union_contains_file_reference(args: tuple[Any, ...]) -> bool:
    """Check whether a union's args include :class:`FileReference` or a subclass.

    Args:
        args: Result of :func:`typing.get_args` on a union annotation.

    Returns:
        True when any arg is a class that subclasses ``FileReference``.
    """
    return any(isinstance(arg, type) and issubclass(arg, FileReference) for arg in args)


def _is_valid_config_field(annotation: Any) -> bool:
    """Check if a type annotation uses a valid Config field type.

    Valid types are Field[T], ImmutableField[T], Dependency[T],
    ImmutableDependency[T], FileReference, FileField, and their Optional/list
    variants. Bare types like str, int, bool are invalid.

    Args:
        annotation: The type annotation to validate.

    Returns:
        True if the annotation is a valid Config field type.
    """
    if isinstance(annotation, typing.TypeAliasType):
        return _is_valid_config_field(annotation.__value__)

    origin = typing.get_origin(annotation)

    if isinstance(origin, typing.TypeAliasType):
        val = origin.__value__

        if typing.get_origin(val) is Annotated:
            val = typing.get_args(val)[0]

        if is_union_origin(typing.get_origin(val)):
            union_args = typing.get_args(val)

            if FieldReference in union_args:
                return True

            if _union_contains_file_reference(union_args):
                return True

        if isinstance(val, type) and issubclass(val, FieldReference):
            return True

        if isinstance(val, type) and issubclass(val, FileReference):
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

    if isinstance(annotation, type) and issubclass(annotation, FileReference):
        return True

    if origin is list:
        args = typing.get_args(annotation)

        if args:
            return _is_valid_config_field(args[0])

        return False

    if is_union_origin(origin):
        non_none_args = [a for a in typing.get_args(annotation) if a is not type(None)]

        if len(non_none_args) == 1:
            return _is_valid_config_field(non_none_args[0])

        if FieldReference in typing.get_args(annotation):
            return True

        if _union_contains_file_reference(typing.get_args(annotation)):
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
        elif is_union_origin(origin):
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
        elif is_union_origin(origin):
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
        elif is_union_origin(origin):
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
    """Base class for resource outputs produced by lifecycle handlers.

    Validation ignores properties the class does not declare, so outputs a
    newer version of a provider records still read as this class.
    """

    model_config = {"extra": "ignore"}

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

    Subclasses must override ``on_observe`` and declare an ``Outputs`` class
    unless they declare ``computed = True``.
    Handlers must be idempotent, since events may be redelivered.

    A resource composed of child resources applies them with
    ``Resource.apply``, reads them with ``Resource.read`` and returns
    ``ChildrenPending()`` while one is not READY; see ``ChildrenPending``.
    """

    resource: ClassVar[str]
    computed: ClassVar[bool] = False
    """Whether this type has no external object and so needs no ``on_observe`` or ``Outputs`` class.

    Declare it as a bare ``computed = True``, without a type annotation. The
    platform never observes a computed type before acting, so its
    ``on_delete`` also runs after a create that never completed and must
    tolerate finding nothing to delete.
    """
    operation_deadline_seconds: ClassVar[int | None] = None
    """Longest time, in whole seconds, one operation of this type may run.

    Declare it as a bare ``operation_deadline_seconds = 1800``, without a
    type annotation, from 1 to ``MAXIMUM_OPERATION_DEADLINE_SECONDS``
    (3600). Any other value, or an annotated field of that name, makes
    ``@provider.resource()`` raise ``OperationDeadlineOutOfRangeError`` as
    the class is defined. Leave it ``None`` for the platform default of
    1200 seconds.

    An operation runs up to the declared value or the platform default,
    whichever is shorter. A declared value longer than the default lets it
    run past the default, never beyond the declared value, but only while
    the worker's event loop stays responsive: a handler that blocks the
    loop, with ``time.sleep`` or another blocking call, fails soon after it
    passes the default. Run blocking work under ``asyncio.to_thread``.

    At the deadline the platform cancels the handler: the ``await`` it is
    suspended in raises ``asyncio.CancelledError``. Let it propagate, and
    release what the handler holds in ``finally``. The operation fails with
    a deadline reason, whatever the handler does next.

    The cancel does not stop work running under ``asyncio.to_thread`` or
    ``loop.run_in_executor(None, ...)``. The worker answers the cancel once
    the work the handler left there has finished, and is stopped about 30 seconds after the deadline if it has
    not, which ends that work. Asyncio tasks the handler spawns, or shields
    with ``asyncio.shield``, are not cancelled with it and are not waited
    for; ending them is the handler's responsibility, for example in a
    ``TaskGroup`` or in ``finally``. Threads, processes and executors a
    handler starts itself are not tracked either: stop them in ``finally``.
    """

    project_id: str
    name: str
    config: ConfigT
    dependencies: list[ResourceReference] = PydanticField(default_factory=list)
    outputs: OutputsT | None = None
    error: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    tags: list[str] | None = None
    provider_version: str | None = None
    """Version whose operation produced the outputs, as the platform records it.

    ``None`` until an operation succeeds, and again after a deactivate.
    """
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def provider(self) -> str:
        """Catalog name of the provider whose distribution defines this resource's class.

        A resource of a type an embedded provider defines carries the
        embedded provider's catalog name, even when an embedding provider
        builds, applies or reads it, so its identity and the child resources
        it applies name the provider that manages the type.

        Returns:
            Provider catalog name (e.g., "pragmatiks/gcp").

        Raises:
            RuntimeError: If read outside a lifecycle method the Pragmatiks
                runtime or the ``ProviderHarness`` runs, or the class belongs
                neither to the provider nor to a provider it embeds.
        """  # noqa: DOC502
        return compute_defining_provider(type(self))

    @property
    def identity(self) -> ResourceIdentity:
        """Structured identity for this resource.

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

    async def on_create(self) -> OutputsT | ChildrenPending:
        """Create the external object this resource owns.

        Return only once observing the resource finds the object, with
        outputs equal to what ``on_observe`` returns. The outputs, an
        instance of the type's Outputs class or a dict, are validated against
        that class, which drops the properties it does not declare. Outputs
        that fail that validation, or differ from what ``on_observe``
        returns, fail the resource while the object stays in place, until a
        fixed provider version runs.

        A resource composed of child resources may return
        ``ChildrenPending()`` while one is not READY; see ``ChildrenPending``.

        Returns:
            Outputs of the created object, or ``ChildrenPending`` to wait
            for the child resources this operation applied.

        Raises:
            AlreadyExistsError: If the vendor refuses the create because the
                object already exists outside Pragmatiks.
        """  # noqa: DOC502
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_create()")

    async def on_observe(self) -> OutputsT | None:
        """Report the external object this resource owns.

        Locate it from identity and ``config`` alone and make no write;
        ``self.outputs`` is never populated, and platform callbacks,
        ``Resource.apply`` and ``Resource.read`` included, raise. The outputs
        are validated against the type's Outputs class and compared with what
        ``on_create`` and ``on_update`` returned, so return the same values for
        the same object. Report only an object this resource created: for one
        at the resource's identity that the create's conflict path would
        refuse, such as an object without this resource's marker or with
        another resource's, raise ``AlreadyExistsError`` with the message the
        create's conflict uses.

        Returns:
            Outputs of the existing object, or ``None`` only when it does not exist.

        Raises:
            AlreadyExistsError: If an object exists at the resource's identity
                that this resource did not create.
            NotImplementedError: If the subclass does not override it.
        """  # noqa: DOC502
        raise NotImplementedError(f"{type(self).__name__} must implement on_observe")

    async def on_update(self, previous_config: ConfigT | None) -> OutputsT | ChildrenPending:
        """Converge the existing external object to ``self.config``.

        Return only once observing the resource finds the object, with
        outputs equal to what ``on_observe`` returns. The outputs, an
        instance of the type's Outputs class or a dict, are validated against
        that class, which drops the properties it does not declare. Outputs
        that fail that validation, or differ from what ``on_observe``
        returns, fail the resource while the object stays in place, until a
        fixed provider version runs.

        A resource composed of child resources may return
        ``ChildrenPending()`` while one is not READY; see
        ``ChildrenPending``. It also runs as the second pass after a wait.

        Args:
            previous_config: Configuration the provider last completed, or
                ``None`` when unknown. In the second pass after a wait it is
                still the configuration completed before the first pass,
                ``None`` when the first pass was a create, never the first
                pass's configuration.

        Returns:
            Outputs of the converged object, or ``ChildrenPending`` to wait
            for the child resources this operation applied.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement on_update()")

    async def on_delete(self) -> None:
        """Delete the external object this resource owns.

        Return only once observing the resource finds the object absent.
        The platform may call it after a create that never completed, so it
        must tolerate an object that was never created or only partly
        created.
        """
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
        """Migrate config and outputs from the previous published version.

        Called during provider upgrade for each existing resource of this type.
        Override to transform config/outputs when the schema changes between versions.
        Must be defined on every version, even if no-op.

        Note:
            Every published version's translation between the stored and the
            target version runs in order, one version at a time. A
            translation must be pure and finish within 60 seconds. ``config``
            arrives with its field references unresolved.

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
        """Migrate config and outputs back to the previous published version.

        Called during provider downgrade for each existing resource of this type.
        Override to reverse the transformations applied by upgrade().
        Must be defined on every version, even if no-op.

        Note:
            Every published version's translation between the stored and the
            target version runs in order, one version at a time. A
            translation must be pure and finish within 60 seconds. ``config``
            arrives with its field references unresolved.

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

        Note:
            Overrides must be async generators (``async def`` with ``yield``).
            The default raises ``NotImplementedError`` on first iteration, not
            when called.
        """
        raise NotImplementedError("Subclass must implement logs()")
        yield

    async def health(self) -> HealthStatus:
        """Override to provide health status for this resource.

        Returns:
            Health status. Default implementation returns healthy.
        """
        return HealthStatus(status="healthy")

    async def apply(self) -> None:
        """Apply this resource through the platform, owned by the resource whose lifecycle method runs.

        Call it from ``on_create`` or ``on_update`` to create or update a
        child resource. The platform records the running resource as its
        owner, writes the child in the running resource's project, whatever
        ``project_id`` this instance carries, or finds it unchanged, and
        processes it through its own lifecycle. The call returns once the
        platform accepted the apply, never waiting for the child and never
        telling whether it was unchanged. Read the child's state with
        :meth:`read`, and return ``ChildrenPending()`` only after a read
        found a child not READY: a pass that returns it while every child
        it applied was already READY and unchanged fails.

        Raises:
            CallbackRefusedError: If the platform refused the apply: another
                resource owns this one, it exists and no resource owns it,
                or the running operation is no longer current. Let it
                propagate; the running resource fails with that reason.
            RuntimeError: If called outside ``on_create``, ``on_update`` or
                ``on_delete`` run by the Pragmatiks runtime, or the apply
                fails otherwise.

        Example:
            ```python
            async def on_create(self) -> AppOutputs | ChildrenPending:
                database = DatabaseResource(
                    project_id=self.project_id, name=f"{self.name}-db", config=DbConfig(port=5432)
                )
                await database.apply()
                applied = await database.read()

                if applied is None or applied.lifecycle_state != LifecycleState.READY:
                    return ChildrenPending()

                return AppOutputs(database_url=applied.outputs.connection_url)
            ```
        """  # noqa: DOC502
        resource_data: dict[str, Any] = {
            "provider": self.provider,
            "resource": self.resource,
            "name": self.name,
            "config": self.config.model_dump(),
        }

        if self.tags:
            resource_data["tags"] = self.tags

        await apply_resource(resource_data)

    async def read(self) -> Self | None:
        """Read this resource as the platform records it, when the resource whose lifecycle method runs owns it.

        Call it from ``on_create`` or ``on_update`` on a child resource the
        running resource applied, to learn its lifecycle state, its stored
        configuration and its outputs, and return ``ChildrenPending()`` while
        it is not READY. The platform reads in the running resource's
        project.

        The result is read as this class: top-level config keys a strict
        Config does not declare are dropped and logged as
        ``child_config_keys_ignored``. Its config is the one the owner
        applied, references unresolved, so a dependency or field reference
        reads back as the reference and a Pragmatiks file reference as its
        ``pragma://files/...`` text. It carries no dependencies, tags or
        error, and keeps this instance's ``project_id``.

        Returns:
            A new instance carrying the recorded ``lifecycle_state``,
            ``config`` and ``outputs``, or ``None`` when no live resource has
            this identity.

        Raises:
            CallbackRefusedError: If the platform refused the read: a live
                resource has this identity and the running resource does not
                own it, or the running operation is no longer current.
            RuntimeError: If called outside ``on_create``, ``on_update`` or
                ``on_delete`` run by the Pragmatiks runtime, such as in
                ``on_observe``, or the read fails otherwise.
            ValidationError: If the recorded resource does not match this
                class's Config or Outputs class; its message names fields,
                never values.
        """  # noqa: DOC502
        child = await read_resource(self.provider, self.resource, self.name)

        if child is None:
            return None

        config, ignored_keys = drop_undeclared_config_keys(type(self), child.config)

        if ignored_keys:
            logger.info(
                "child_config_keys_ignored",
                resource_type=type(self).__name__,
                ignored_keys=ignored_keys,
                resource_id=self.id,
            )

        return type(self).model_validate({
            "project_id": self.project_id,
            "name": self.name,
            "config": config,
            "outputs": child.outputs,
            "lifecycle_state": child.lifecycle_state,
        })


def drop_undeclared_config_keys(resource_type: type[Resource], config: Any) -> tuple[Any, list[str]]:
    """Compute a resource's config without the top-level keys a strict Config does not declare.

    The config of a resource another provider version recorded, such as a
    dependency or a child resource, may carry keys a newer version added.
    Only a Config forbidding extra keys is filtered; nested values stay
    strict. The caller logs the dropped keys by name, never by value.

    Args:
        resource_type: Resource type the config is read as.
        config: The recorded config.

    Returns:
        A copy of the config without the undeclared keys, or the config
        itself when nothing is dropped, and the dropped keys, sorted.

    Note:
        Keys are dropped before validation, so a ``mode="before"`` model
        validator on the Config does not see them. A resource type whose
        ``config`` field is not a Config subclass is left unfiltered.
    """
    config_field = resource_type.model_fields.get("config")
    config_class = config_field.annotation if config_field is not None else None

    if (
        not isinstance(config_class, type)
        or not issubclass(config_class, Config)
        or config_class.model_config.get("extra") != "forbid"
        or not isinstance(config, dict)
    ):
        return config, []

    declared_keys = compute_declared_keys(config_class)
    ignored_keys = sorted(key for key in config if key not in declared_keys)

    if not ignored_keys:
        return config, []

    return {key: value for key, value in config.items() if key in declared_keys}, ignored_keys


def derive_outputs_class(resource_class: type[Resource]) -> type[Outputs] | None:
    """Derive the Outputs subclass a Resource class's outputs field declares.

    Args:
        resource_class: A Resource subclass.

    Returns:
        The Outputs subclass, also when the field is declared
        ``OutputsT | None``, or None if the field declares none.
    """
    outputs_field = resource_class.model_fields.get("outputs")

    if outputs_field is None:
        return None

    annotation = outputs_field.annotation

    if annotation is None:
        return None

    if isinstance(annotation, type) and issubclass(annotation, Outputs):
        return annotation

    if is_union_origin(typing.get_origin(annotation)):
        for arg in typing.get_args(annotation):
            if arg is not type(None) and isinstance(arg, type) and issubclass(arg, Outputs):
                return arg

    return None
