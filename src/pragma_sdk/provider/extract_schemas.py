"""Utilities for extracting resource schemas and metadata from provider packages.

Used during the Docker build process to extract JSON schemas and store
metadata for all resources in a provider package.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pragma_sdk.models import Config, Resource
from pragma_sdk.provider.discovery import discover_resources


METADATA_KEYS = ("display_name", "description", "author", "tags", "icon")


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


def extract_metadata() -> dict[str, Any] | None:
    """Extract provider store metadata from pyproject.toml [tool.pragma] section.

    Reads optional metadata keys (display_name, description, author, tags, icon)
    from the [tool.pragma] section. Only includes keys that are present.

    Returns:
        Dictionary of metadata fields, or None if no metadata keys found.
    """
    data = _load_pyproject()

    if data is None:
        return None

    pragma_section = data.get("tool", {}).get("pragma", {})

    metadata = {key: pragma_section[key] for key in METADATA_KEYS if key in pragma_section}

    if not metadata:
        return None

    return metadata


def extract_schemas(package_name: str) -> list[dict[str, Any]]:
    """Extract JSON schemas for all resources in a provider package.

    Discovers all Resource classes in the package and extracts their
    config schemas using Pydantic's model_json_schema().

    Args:
        package_name: Python package name to scan (e.g., "postgres_provider").

    Returns:
        List of schema dictionaries with provider, resource, and config_schema keys.
    """
    schemas: list[dict[str, Any]] = []

    resources = discover_resources(package_name)

    for (provider, resource), cls in resources.items():
        try:
            config_type = get_config_class(cls)
            config_schema = config_type.model_json_schema()
            schemas.append({
                "provider": provider,
                "resource": resource,
                "config_schema": config_schema,
            })
        except ValueError:
            continue

    return schemas
