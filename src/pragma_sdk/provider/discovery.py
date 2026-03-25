"""Resource discovery for provider packages."""

from __future__ import annotations

import importlib
import pkgutil

from pragma_sdk.models import Resource
from pragma_sdk.provider.provider import RESOURCE_MARKER


def discover_resources(package_name: str) -> dict[str, type[Resource]]:
    """Discover all registered Resource classes in a Python package.

    Recursively walks all modules and finds Resource classes decorated with
    @provider.resource().

    Args:
        package_name: Provider package to scan (e.g., "postgres_provider").

    Returns:
        Dictionary mapping resource names to Resource classes.

    Example:
        resources = discover_resources("postgres_provider")
        for resource_name, cls in resources.items():
            print(f"{resource_name}: {cls.__name__}")
    """
    resources: dict[str, type[Resource]] = {}
    _discover_in_module(package_name, resources)

    return resources


def is_registered_resource(cls: type) -> bool:
    """Check if a class is a Resource decorated with @provider.resource().

    Args:
        cls: Class to check.

    Returns:
        True if cls is a decorated Resource subclass, False otherwise.
    """
    return (
        isinstance(cls, type)
        and issubclass(cls, Resource)
        and cls is not Resource
        and getattr(cls, RESOURCE_MARKER, False) is True
    )


def _discover_in_module(module_name: str, resources: dict[str, type[Resource]]) -> None:
    """Recursively discover resources in a module and its submodules."""
    module = importlib.import_module(module_name)

    for name in dir(module):
        obj = getattr(module, name)
        if is_registered_resource(obj):
            key = obj.resource
            if key not in resources:
                resources[key] = obj

    if hasattr(module, "__path__"):
        for _importer, name, _is_pkg in pkgutil.iter_modules(module.__path__):
            _discover_in_module(f"{module_name}.{name}", resources)
