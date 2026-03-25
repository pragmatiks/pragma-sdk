"""Utilities for extracting resource schemas and metadata from provider packages.

Used during the Docker build process to extract JSON schemas and store
metadata for all resources in a provider package.
"""

from __future__ import annotations

import tomllib
import types
import typing
from pathlib import Path
from typing import Any, TypedDict

from pragma_sdk.docstrings import extract_short_description, parse_attributes_section
from pragma_sdk.models import Config, Outputs, Resource
from pragma_sdk.provider.discovery import discover_resources


class ProviderMetadata(TypedDict, total=False):
    """Catalog metadata fields from a provider's pyproject.toml [tool.pragma] section."""

    display_name: str
    description: str
    author: str
    tags: list[str]
    icon: str


METADATA_KEYS: tuple[str, ...] = tuple(ProviderMetadata.__annotations__)


def _load_pyproject() -> dict[str, Any] | None:
    """Load and parse pyproject.toml from the current directory.

    Returns:
        Parsed TOML data, or None if file doesn't exist.
    """
    pyproject = Path("pyproject.toml")

    if not pyproject.exists():
        return None

    with open(pyproject, "rb") as f:
        return tomllib.load(f)


def get_config_class(resource_class: type[Resource]) -> type[Config]:
    """Extract Config subclass from Resource's config field annotation.

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


def get_outputs_class(resource_class: type[Resource]) -> type[Outputs] | None:
    """Extract Outputs subclass from Resource's outputs field annotation.

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


def detect_provider_package() -> str | None:
    """Detect provider package name from current directory.

    Reads pyproject.toml and checks in order:
    1. [tool.pragma] package - explicit module name
    2. [project] name - converted to underscores if ends with '-provider'

    Returns:
        Package name if found, None otherwise.
    """
    data = _load_pyproject()

    if data is None:
        return None

    pragma_package = data.get("tool", {}).get("pragma", {}).get("package")

    if pragma_package:
        return pragma_package

    name = data.get("project", {}).get("name", "")
    if name and name.endswith("-provider"):
        return name.replace("-", "_")

    return None


_EXPECTED_TYPES: dict[str, type] = {
    "display_name": str,
    "description": str,
    "author": str,
    "tags": list,
    "icon": str,
}


def extract_metadata() -> ProviderMetadata | None:
    """Extract provider catalog metadata from pyproject.toml [tool.pragma] section.

    Reads optional metadata keys (display_name, description, author, tags, icon)
    from the [tool.pragma] section. Only includes keys that are present.

    Returns:
        Typed metadata dictionary, or None if no metadata keys found.

    Raises:
        TypeError: If a metadata field has an unexpected type.
    """
    data = _load_pyproject()

    if data is None:
        return None

    pragma_section = data.get("tool", {}).get("pragma", {})

    metadata: dict[str, Any] = {}

    for key in METADATA_KEYS:
        if key not in pragma_section:
            continue

        value = pragma_section[key]
        expected = _EXPECTED_TYPES[key]

        if not isinstance(value, expected):
            raise TypeError(f"[tool.pragma] {key} must be {expected.__name__}, got {type(value).__name__}")

        if expected is list and not all(isinstance(item, str) for item in value):
            raise TypeError(f"[tool.pragma] {key} must contain only strings")

        metadata[key] = value

    if not metadata:
        return None

    result: ProviderMetadata = metadata  # type: ignore[assignment]
    return result


def extract_schemas(package_name: str, catalog_name: str) -> list[dict[str, Any]]:
    """Extract JSON schemas for all resources in a provider package.

    Discovers all Resource classes in the package and extracts their
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
            config_type = get_config_class(cls)
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

            outputs_type = get_outputs_class(cls)

            if outputs_type is not None:
                entry["outputs_schema"] = outputs_type.model_json_schema()

                output_field_descriptions = parse_attributes_section(outputs_type.__doc__)

                if output_field_descriptions:
                    entry["output_descriptions"] = output_field_descriptions

            schemas.append(entry)
        except ValueError:
            continue

    return schemas
