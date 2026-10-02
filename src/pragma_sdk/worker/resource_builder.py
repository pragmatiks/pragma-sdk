"""Builds Resource instances from the resource payload of a dispatch or query."""

from __future__ import annotations

import re
from typing import Annotated, Any, ClassVar, get_args, get_origin

import structlog
from pydantic import ValidationError

from pragma_sdk.diagnostics.validation_errors import build_validation_error_details
from pragma_sdk.models import Config, Dependency, Resource, is_dependency_marker, is_field_ref_marker
from pragma_sdk.models.base import is_union_origin
from pragma_sdk.protocol.frames import ResourcePayload
from pragma_sdk.provider.loading import derive_config_class
from pragma_sdk.types import LifecycleState


logger = structlog.get_logger()


class MissingResolvedFileError(RuntimeError):
    """Raised when a Pragmatiks file reference has no signed URL."""


class ResourceBuilder:
    """Builds validated Resource instances from resource payloads.

    A dependency marker that cannot be turned into the resource it references
    is logged and left unresolved; the lifecycle method runs without it.
    """

    FILE_REFERENCE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^pragma://files/(.+)$")

    def build_resource(
        self, resource_class: type[Resource], payload: ResourcePayload, resolved_files: dict[str, str]
    ) -> Resource:
        """Create a Resource instance from a resource payload with validated config.

        Args:
            resource_class: Resource class to instantiate.
            payload: Resource payload carrying the name, project, config and tags.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            Instantiated Resource with validated config.

        Raises:
            ValueError: If the resource class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL in ``resolved_files``.
        """  # noqa: DOC502
        return resource_class(
            project_id=payload.project_id,
            name=payload.name,
            config=self.build_config(resource_class, payload.config, resolved_files),
            lifecycle_state=LifecycleState.PROCESSING,
            tags=payload.tags,
        )

    def build_config(
        self, resource_class: type[Resource], config_data: dict[str, Any], resolved_files: dict[str, str]
    ) -> Config:
        """Build a resource class's validated Config with dependencies resolved and field references flattened.

        Args:
            resource_class: Resource class to extract Config type from.
            config_data: Raw configuration dictionary to validate.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            Validated Config instance with Dependency fields pre-resolved.

        Raises:
            ValueError: If the resource class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL in ``resolved_files``.
        """  # noqa: DOC502
        config_class = derive_config_class(resource_class)
        resolved_instances = self.build_dependency_resources(config_class, config_data, resolved_files)
        config = config_class.model_validate(self.flatten_config(config_data, resolved_files))
        self.inject_resolved_instances(config, resolved_instances)

        return config

    def build_dependency_resources(
        self, config_class: type[Config], config_data: dict[str, Any], resolved_files: dict[str, str]
    ) -> dict[str, Resource | list[Resource | None]]:
        """Instantiate the resources referenced by a config's dependency markers.

        Args:
            config_class: Config class to introspect for Dependency fields.
            config_data: Raw config data potentially containing dependency markers.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            Resolved resources, or index-aligned lists of them, by field name;
            fields with no resolvable marker are absent.

        Raises:
            MissingResolvedFileError: If a dependency reference holds a
                Pragmatiks file reference with no signed URL.
        """  # noqa: DOC502
        resolved: dict[str, Resource | list[Resource | None]] = {}

        for field_name, field_info in config_class.model_fields.items():
            resolved_value = self.resolve_field_dependency(
                field_name, field_info.annotation, config_data, resolved_files
            )

            if resolved_value is not None:
                resolved[field_name] = resolved_value

        return resolved

    def resolve_field_dependency(
        self, field_name: str, field_type: Any, config_data: dict[str, Any], resolved_files: dict[str, str]
    ) -> Resource | list[Resource | None] | None:
        """Resolve a single Config field's dependency marker(s), if any.

        Args:
            field_name: Config field name to resolve.
            field_type: Field's type annotation, examined for a Dependency shape.
            config_data: Raw config data potentially containing dependency markers.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            Resolved Resource instance, a list of them for list[Dependency[T]]
            fields, or None if the field carries no resolvable dependency.

        Raises:
            MissingResolvedFileError: If a dependency reference holds a
                Pragmatiks file reference with no signed URL.
        """  # noqa: DOC502
        is_list, dependency_type = self.extract_dependency_type(field_type)

        if dependency_type is None:
            return None

        field_value = config_data.get(field_name)

        if is_list:
            return self.resolve_dependency_list(field_name, dependency_type, field_value, resolved_files) or None

        return self.resolve_dependency(field_name, dependency_type, field_value, resolved_files)

    def extract_dependency_type(self, field_type: Any) -> tuple[bool, type[Dependency] | list[type[Dependency]] | None]:
        """Extract Dependency type(s) from a field type annotation.

        Handles direct Dependency[T], Annotated[Dependency[T], ...] (ImmutableDependency),
        union types like Dependency[A] | Dependency[B],
        and list types like list[Dependency[T]] or list[Dependency[A] | Dependency[B]].

        Args:
            field_type: Type annotation to examine.

        Returns:
            Tuple of (is_list, dependency_type) where is_list indicates whether
            the field is a list of dependencies, and dependency_type is the
            single Dependency type, list of Dependency types for unions, or None.
        """
        field_type = self.unwrap_annotated(field_type)

        if isinstance(field_type, type) and issubclass(field_type, Dependency):
            return False, field_type

        origin = get_origin(field_type)

        if origin is list:
            return self.extract_list_dependency_type(field_type)

        if not is_union_origin(origin):
            return False, None

        return False, self.extract_union_dependency_types(field_type)

    @staticmethod
    def unwrap_annotated(field_type: Any) -> Any:
        """Strip an ``Annotated[...]`` wrapper down to its underlying type.

        Args:
            field_type: Type annotation to examine.

        Returns:
            The wrapped type if ``field_type`` is ``Annotated[...]``, otherwise
            ``field_type`` unchanged.
        """
        if get_origin(field_type) is Annotated:
            return get_args(field_type)[0]

        return field_type

    def extract_list_dependency_type(
        self, field_type: Any
    ) -> tuple[bool, type[Dependency] | list[type[Dependency]] | None]:
        """Extract the Dependency type carried by a ``list[...]`` annotation.

        Args:
            field_type: A ``list[...]`` type annotation to examine.

        Returns:
            Tuple of (is_list, dependency_type), where is_list is True only
            when the list's item type resolves to a Dependency type.
        """
        args = get_args(field_type)

        if not args:
            return False, None

        _, inner_dependency = self.extract_dependency_type(args[0])

        if inner_dependency is None:
            return False, None

        return True, inner_dependency

    def extract_union_dependency_types(self, field_type: Any) -> list[type[Dependency]] | None:
        """Collect the Dependency members of a union type annotation.

        Args:
            field_type: A union type annotation to examine.

        Returns:
            The Dependency types found in the union, or None if there are none.
        """
        dependency_types = [
            argument
            for argument in get_args(field_type)
            if argument is not type(None) and self.is_dependency_union_member(argument)
        ]

        return dependency_types or None

    @staticmethod
    def is_dependency_union_member(argument: Any) -> bool:
        """Check whether a union member is (or is generic over) a Dependency type.

        Args:
            argument: One member of a union type annotation.

        Returns:
            True if the member is a Dependency subclass or a generic alias
            whose origin is a Dependency subclass.
        """
        check_type = argument if isinstance(argument, type) else get_origin(argument)

        try:
            return check_type is not None and issubclass(check_type, Dependency)
        except TypeError:
            return False

    def resolve_dependency_list(
        self,
        field_name: str,
        dependency_type: type[Dependency] | list[type[Dependency]],
        field_value: Any,
        resolved_files: dict[str, str],
    ) -> list[Resource | None]:
        """Resolve a list of dependency markers to Resource instances, index-aligned with the input.

        Args:
            field_name: Config field name, named in log events.
            dependency_type: Dependency type(s) for items in the list.
            field_value: Raw field value (expected to be a list of dependency markers).
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            One entry per marker, None where it carries no resolvable
            reference; empty when the value is not a list.

        Raises:
            MissingResolvedFileError: If a reference holds a Pragmatiks file
                reference with no signed URL.
        """  # noqa: DOC502
        if not isinstance(field_value, list):
            return []

        return [
            self.resolve_dependency(f"{field_name}[{index}]", dependency_type, item, resolved_files)
            for index, item in enumerate(field_value)
        ]

    def resolve_dependency(
        self,
        field_name: str,
        dependency_type: type[Dependency] | list[type[Dependency]],
        field_value: Any,
        resolved_files: dict[str, str],
    ) -> Resource | None:
        """Resolve a single dependency marker to a Resource instance.

        Args:
            field_name: Config field name, named in log events.
            dependency_type: Dependency type(s) to resolve against.
            field_value: Raw field value from config data.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            Resolved Resource instance, or None if the value is not a
            dependency marker carrying a resolved reference, or the reference
            cannot be turned into the resource it names.

        Raises:
            MissingResolvedFileError: If the reference holds a Pragmatiks file
                reference with no signed URL.
        """  # noqa: DOC502
        reference_data = self.extract_dependency_reference(field_value)

        if reference_data is None:
            return None

        resource_type = self.resolve_resource_type(field_name, dependency_type, reference_data)

        if resource_type is None:
            return None

        return self.instantiate_dependency_resource(resource_type, reference_data, resolved_files, field_name)

    @staticmethod
    def extract_dependency_reference(field_value: Any) -> dict[str, Any] | None:
        """Extract the resolved reference payload from a dependency marker.

        Args:
            field_value: Raw field value from config data.

        Returns:
            The marker's ``ref`` payload, or None if the value is not a
            dependency marker or carries no resolved reference.
        """
        if not is_dependency_marker(field_value):
            return None

        return field_value.get("ref")

    def resolve_resource_type(
        self,
        field_name: str,
        dependency_type: type[Dependency] | list[type[Dependency]],
        reference_data: dict[str, Any],
    ) -> type[Resource] | None:
        """Resolve the Resource type a dependency reference validates against.

        For a union of Dependencies, the member whose resource type the
        reference names is chosen.

        Args:
            field_name: Config field name, named in log events.
            dependency_type: Single Dependency type or list of Dependency types.
            reference_data: Reference data containing resource type information.

        Returns:
            Resource type to use for validation, or None, logged, when the
            Dependency type is not parameterized with a resource type or no
            union member matches the referenced resource type.
        """
        if isinstance(dependency_type, list):
            return self.match_union_resource_type(field_name, dependency_type, reference_data.get("resource", ""))

        resource_type = self.extract_generic_resource_type(dependency_type)

        if resource_type is None:
            logger.error(
                "dependency_type_extraction_failed",
                field=field_name,
                hint="Ensure Dependency field uses parameterized type: Dependency[ResourceType]",
            )

        return resource_type

    def match_union_resource_type(
        self, field_name: str, dependency_type: list[type[Dependency]], reference_resource: str
    ) -> type[Resource] | None:
        """Find the union member whose generic Resource type matches by name.

        Args:
            field_name: Config field name, named in log events.
            dependency_type: Dependency types to search.
            reference_resource: Resource type name from the reference data.

        Returns:
            The matching Resource type, or None, logged, when no candidate
            matches the referenced resource type.
        """
        candidates = [self.extract_generic_resource_type(candidate) for candidate in dependency_type]

        for resource_type in candidates:
            if resource_type is not None and getattr(resource_type, "resource", None) == reference_resource:
                return resource_type

        logger.warning(
            "union_dependency_no_match",
            field=field_name,
            reference_resource=reference_resource,
            available_types=[getattr(resource_type, "resource", None) for resource_type in candidates],
        )

        return None

    @staticmethod
    def extract_generic_resource_type(dependency_type: type[Dependency]) -> type[Resource] | None:
        """Extract the Resource type parameter from a generic Dependency type.

        Args:
            dependency_type: A ``Dependency[T]`` type to introspect.

        Returns:
            The Resource type ``T``, or None if it cannot be determined.
        """
        metadata = getattr(dependency_type, "__pydantic_generic_metadata__", None)

        if metadata is None or not metadata.get("args"):
            return None

        return metadata["args"][0]

    def instantiate_dependency_resource(
        self,
        resource_type: type[Resource],
        reference_data: dict[str, Any],
        resolved_files: dict[str, str],
        field_name: str,
    ) -> Resource | None:
        """Flatten a dependency reference and validate it into a Resource.

        Args:
            resource_type: Resource type to validate the reference against.
            reference_data: Raw reference payload from the dependency marker.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.
            field_name: Config field name, named in log events.

        Returns:
            The validated Resource instance, or None when the reference does
            not validate as ``resource_type``; the failure is logged by field
            and message, never by value.

        Raises:
            MissingResolvedFileError: If the reference holds a Pragmatiks file
                reference with no signed URL.
        """  # noqa: DOC502
        flattened_reference = self.flatten_config(reference_data, resolved_files)
        self.apply_resolved_config_override(flattened_reference)

        try:
            return resource_type.model_validate(flattened_reference)
        except ValidationError as error:
            logger.error(
                "dependency_reference_validation_failed",
                field=field_name,
                resource_type=resource_type.__name__,
                errors=build_validation_error_details(error),
            )

            return None

    @staticmethod
    def apply_resolved_config_override(flattened_reference: dict[str, Any]) -> None:
        """Prefer a reference's resolved_config over its declared config, in place.

        Args:
            flattened_reference: Flattened dependency reference to adjust.
        """
        resolved_config = flattened_reference.get("resolved_config")

        if resolved_config is not None:
            flattened_reference["config"] = resolved_config

    def flatten_config(self, value: Any, resolved_files: dict[str, str]) -> Any:
        """Replace field-reference markers with their resolved values and file references with signed URLs.

        Dependency markers are kept as they are.

        Args:
            value: Config value to flatten (dict, list, or scalar).
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            The flattened value.

        Raises:
            MissingResolvedFileError: If a Pragmatiks file reference has no
                signed URL in ``resolved_files``.
        """  # noqa: DOC502
        if isinstance(value, dict):
            return self.flatten_dict_config(value, resolved_files)

        if isinstance(value, list):
            return [self.flatten_config(item, resolved_files) for item in value]

        if isinstance(value, str):
            return self.resolve_file_reference(value, resolved_files)

        return value

    def flatten_dict_config(self, value: dict, resolved_files: dict[str, str]) -> Any:
        """Flatten a dict-valued config node, resolving field-ref markers first.

        Args:
            value: Dict config node, possibly a __field_ref__ marker.
            resolved_files: Signed URLs keyed by original Pragmatiks file references.

        Returns:
            The flattened field-ref value, or a recursively flattened dict.

        Raises:
            MissingResolvedFileError: If a Pragmatiks file reference has no
                signed URL in ``resolved_files``.
        """  # noqa: DOC502
        if is_field_ref_marker(value):
            return self.flatten_config(value["resolved_value"], resolved_files)

        return {key: self.flatten_config(item, resolved_files) for key, item in value.items()}

    @staticmethod
    def resolve_file_reference(value: str, resolved_files: dict[str, str]) -> str:
        """Resolve a single Pragmatiks file reference to its signed download URL.

        Args:
            value: Candidate string; resolved only when it matches the
                ``pragma://files/...`` pattern, otherwise returned unchanged.
            resolved_files: Signed URLs keyed by original Pragmatiks file reference.

        Returns:
            The signed download URL for a matching reference, or the original
            value when it is not a Pragmatiks file reference.

        Raises:
            MissingResolvedFileError: If ``value`` is a Pragmatiks file reference
                with no corresponding signed URL in ``resolved_files``.
        """
        if not ResourceBuilder.FILE_REFERENCE_PATTERN.match(value):
            return value

        resolved_url = resolved_files.get(value)

        if resolved_url is None:
            raise MissingResolvedFileError(f"Missing resolved file URL for {value}")

        return resolved_url

    def inject_resolved_instances(
        self, config: Config, resolved_instances: dict[str, Resource | list[Resource | None]]
    ) -> None:
        """Inject resolved resource instances into Dependency fields.

        For list fields, each Dependency in the list receives its corresponding
        resolved Resource.

        Args:
            config: Config instance with Dependency fields.
            resolved_instances: Mapping of field names to resolved Resource instances
                or lists of them (for list[Dependency[T]] fields).
        """
        for field_name, resource_instance in resolved_instances.items():
            field_value = getattr(config, field_name, None)

            if isinstance(field_value, list) and isinstance(resource_instance, list):
                self.inject_resolved_list(field_value, resource_instance)
                continue

            if isinstance(field_value, Dependency) and isinstance(resource_instance, Resource):
                field_value.set_resolved(resource_instance)

    @staticmethod
    def inject_resolved_list(dependencies: list[Any], resolved_resources: list[Resource | None]) -> None:
        """Inject resolved Resource instances into a list[Dependency[T]] field.

        Args:
            dependencies: Dependency instances from the Config field, in order.
            resolved_resources: Resolved Resource instances, index-aligned with
                ``dependencies``.
        """
        for dependency, resolved in zip(dependencies, resolved_resources):
            if isinstance(dependency, Dependency) and resolved is not None:
                dependency.set_resolved(resolved)
