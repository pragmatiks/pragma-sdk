"""Utilities for loading JSON schemas from provider packages.

Used during the Docker build process to extract schemas for all
resources in a provider package.
"""

from __future__ import annotations

import types
import typing
from typing import Any

from pragma_sdk.docstrings import extract_short_description, parse_attributes_section
from pragma_sdk.models import Config, Outputs, Resource
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
        raise ValueError(f"Resource {resource_class.__name__} has no config field")

    config_type = config_field.annotation

    if not isinstance(config_type, type) or not issubclass(config_type, Config):
        raise ValueError(f"Resource {resource_class.__name__} config field is not a Config subclass")

    return config_type


def derive_outputs_class(resource_class: type[Resource]) -> type[Outputs] | None:
    """Derive Outputs subclass from Resource's outputs field annotation.

    Handles ``OutputsT | None`` union types by unwrapping the Optional.

    Args:
        resource_class: A Resource subclass.

    Returns:
        Outputs subclass type, or None if not determinable.
    """
    outputs_field = resource_class.model_fields.get("outputs")

    if outputs_field is None:
        return None

    annotation = outputs_field.annotation

    if annotation is None:
        return None

    if isinstance(annotation, type) and issubclass(annotation, Outputs):
        return annotation

    origin = typing.get_origin(annotation)

    if origin is typing.Union or origin is types.UnionType:
        for arg in typing.get_args(annotation):
            if arg is not type(None) and isinstance(arg, type) and issubclass(arg, Outputs):
                return arg

    return None


def load_provider_schemas(package_name: str, catalog_name: str) -> list[dict[str, Any]]:
    """Load JSON schemas for all resources in a provider package.

    Discovers all Resource classes in the package and loads their
    config schemas using Pydantic's model_json_schema().

    Args:
        package_name: Python package name to scan (e.g., "postgres_provider").
        catalog_name: Catalog name of the provider (e.g., "pragmatiks/gcp").

    Returns:
        List of schema dictionaries with provider, resource, and config_schema keys.
    """
    schemas: list[dict[str, Any]] = []

    resources = discover_resources(package_name)

    for resource_name, cls in resources.items():
        try:
            config_type = derive_config_class(cls)
            config_schema = config_type.model_json_schema()

            entry: dict[str, Any] = {
                "provider": catalog_name,
                "resource": resource_name,
                "config_schema": config_schema,
            }

            description = extract_short_description(cls.__doc__)

            if description is not None:
                entry["description"] = description

            config_field_descriptions = parse_attributes_section(config_type.__doc__)

            if config_field_descriptions:
                entry["field_descriptions"] = config_field_descriptions

            outputs_type = derive_outputs_class(cls)

            if outputs_type is not None:
                entry["outputs_schema"] = outputs_type.model_json_schema()

                output_field_descriptions = parse_attributes_section(outputs_type.__doc__)

                if output_field_descriptions:
                    entry["output_descriptions"] = output_field_descriptions

            schemas.append(entry)
        except ValueError:
            continue

    return schemas
