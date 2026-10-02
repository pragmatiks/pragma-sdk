"""Utilities for loading JSON schemas from provider packages."""

from __future__ import annotations

from typing import Any

from pragma_sdk.docstrings import extract_short_description, parse_attributes_section
from pragma_sdk.models import Config, Resource
from pragma_sdk.models.base import derive_outputs_class
from pragma_sdk.protocol.introspection import ResourceTypeSchema
from pragma_sdk.provider.discovery import discover_resources


def derive_config_class(resource_class: type[Resource]) -> type[Config]:
    """Derive Config subclass from Resource's config field annotation.

    Args:
        resource_class: A Resource subclass.

    Returns:
        Config subclass type from the Resource's config field.

    Raises:
        ValueError: If Resource has no config field or wrong type.
    """
    annotations = resource_class.model_fields
    config_field = annotations.get("config")

    if config_field is None:
        raise ValueError(
            f"Resource {resource_class.__name__} has no config field. "
            "Declare it as Resource[YourConfig, YourOutputs] with a Config subclass."
        )

    config_type = config_field.annotation

    if not isinstance(config_type, type) or not issubclass(config_type, Config):
        raise ValueError(
            f"Resource {resource_class.__name__} config field is not a Config subclass. "
            "Declare it as Resource[YourConfig, YourOutputs] with a Config subclass."
        )

    return config_type


def build_resource_type_schema(resource_class: type[Resource]) -> ResourceTypeSchema:
    """Build the schemas and descriptions of a resource type.

    Args:
        resource_class: A registered Resource subclass.

    Returns:
        The resource type's config and outputs schemas, its description from
        the class docstring, and field descriptions from the ``Attributes``
        sections of the config and outputs docstrings.

    Raises:
        ValueError: If the resource class has no Config-typed config field.
        PydanticInvalidForJsonSchema: If the config or outputs type cannot be
            expressed as JSON schema.
    """  # noqa: DOC502
    config_type = derive_config_class(resource_class)
    outputs_type = derive_outputs_class(resource_class)

    outputs_schema = None
    output_descriptions: dict[str, str] = {}

    if outputs_type is not None:
        outputs_schema = outputs_type.model_json_schema()
        output_descriptions = parse_attributes_section(outputs_type.__doc__)

    return ResourceTypeSchema(
        resource=resource_class.resource,
        description=extract_short_description(resource_class.__doc__),
        config_schema=config_type.model_json_schema(),
        field_descriptions=parse_attributes_section(config_type.__doc__),
        outputs_schema=outputs_schema,
        output_descriptions=output_descriptions,
    )


def build_catalog_schema_entry(schema: ResourceTypeSchema, catalog_name: str) -> dict[str, Any]:
    """Build the catalog entry of a resource type schema.

    Args:
        schema: Schemas and descriptions of the resource type.
        catalog_name: Catalog name of the provider (e.g., "pragmatiks/gcp").

    Returns:
        The entry with ``provider``, ``resource`` and ``config_schema`` keys,
        plus ``description``, ``field_descriptions``, ``outputs_schema`` and
        ``output_descriptions`` only when they carry a value.
    """
    optional_fields = schema.model_dump(
        include={"description", "field_descriptions", "outputs_schema", "output_descriptions"}
    )

    return {
        "provider": catalog_name,
        "resource": schema.resource,
        "config_schema": schema.config_schema,
        **{name: value for name, value in optional_fields.items() if value},
    }


def load_provider_schemas(package_name: str, catalog_name: str) -> list[dict[str, Any]]:
    """Load the catalog schemas of every resource type a provider package defines.

    Args:
        package_name: Python package name to scan (e.g., "postgres_provider").
        catalog_name: Catalog name of the provider (e.g., "pragmatiks/gcp").

    Returns:
        One catalog entry per resource type, as built by
        :func:`build_catalog_schema_entry`.

    Raises:
        ValueError: If a resource class has no Config-typed config field.
        PydanticInvalidForJsonSchema: If a config or outputs type cannot be
            expressed as JSON schema.
        MissingObserveError: If a non-computed resource type in a module the
            package imports defines no ``on_observe``. Any other exception a
            module of the package raises on import propagates.
    """  # noqa: DOC502
    resource_classes = discover_resources(package_name).values()

    return [
        build_catalog_schema_entry(build_resource_type_schema(resource_class), catalog_name)
        for resource_class in resource_classes
    ]
