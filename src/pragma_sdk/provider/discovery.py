"""Resource discovery for provider packages."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from pragma_sdk.models import Resource
from pragma_sdk.provider.provider import RESOURCE_MARKER


def discover_resources(package_name: str) -> dict[str, type[Resource]]:
    """Discover the registered Resource classes a Python package defines.

    Recursively imports every module of the package and keeps the Resource
    classes decorated with @provider.resource() whose defining module lies
    inside the package. A class the package only imports from another
    package, such as an embedded provider's resource type, is left out, and
    so is an undecorated subclass of a decorated class. When two classes
    register the same resource name, the first one found is kept.

    Args:
        package_name: Provider package to scan (e.g., "postgres_provider").

    Returns:
        Dictionary mapping resource names to Resource classes.

    Raises:
        MissingObserveError: If a non-computed resource type in an imported
            module defines no on_observe. Any other exception a module raises
            on import propagates.

    Example:
        resources = discover_resources("postgres_provider")
        for resource_name, cls in resources.items():
            print(f"{resource_name}: {cls.__name__}")
    """  # noqa: DOC502
    resources: dict[str, type[Resource]] = {}

    for resource_name, resource_class in discover_module_resources(package_name, package_name):
        resources.setdefault(resource_name, resource_class)

    return resources


def is_registered_resource(cls: type) -> bool:
    """Check if a class is a Resource decorated with @provider.resource().

    The decoration must be on the class itself; an undecorated subclass of a
    decorated class is not registered.

    Args:
        cls: Class to check.

    Returns:
        True if cls is a decorated Resource subclass, False otherwise.
    """
    return (
        isinstance(cls, type)
        and issubclass(cls, Resource)
        and cls is not Resource
        and cls.__dict__.get(RESOURCE_MARKER, False) is True
    )


def is_module_in_package(module_name: str, package_name: str) -> bool:
    """Check whether a module is a package or one of its submodules.

    Args:
        module_name: Fully qualified module name.
        package_name: Importable package name (e.g., "postgres_provider").

    Returns:
        True if the module is the package or lies below it.
    """
    return module_name == package_name or module_name.startswith(f"{package_name}.")


def discover_module_resources(module_name: str, package_name: str) -> Iterator[tuple[str, type[Resource]]]:
    """Import a module and its submodules, yielding the package's registered resources.

    Args:
        module_name: Module to import and scan.
        package_name: Package whose own Resource classes are kept.

    Yields:
        Resource name and class of each registered resource the package
        defines, in discovery order; a name can repeat.

    Raises:
        MissingObserveError: If a non-computed resource type in an imported
            module defines no on_observe. Any other exception a module raises
            on import propagates.
    """  # noqa: DOC502
    module = importlib.import_module(module_name)

    for name in dir(module):
        candidate = getattr(module, name)

        if is_registered_resource(candidate) and is_module_in_package(candidate.__module__, package_name):
            yield candidate.resource, candidate

    if hasattr(module, "__path__"):
        for _importer, name, _is_pkg in pkgutil.iter_modules(module.__path__):
            yield from discover_module_resources(f"{module_name}.{name}", package_name)
