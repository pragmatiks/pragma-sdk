"""The provider an installed wheel distribution declares through its entry point.

A provider wheel declares itself with one entry point in the
``pragma.provider`` group, named after the provider and pointing at its
importable package::

    [project.entry-points."pragma.provider"]
    postgres = "postgres_provider"
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
from dataclasses import dataclass

from pragma_sdk.models import Resource
from pragma_sdk.protocol.constants import OFFERED_PROTOCOL_VERSIONS
from pragma_sdk.protocol.frames import HandshakeParams, ResourceTypeCapabilities
from pragma_sdk.provider.discovery import discover_resources, is_module_in_package
from pragma_sdk.provider.provider import MissingObserveError


PROVIDER_ENTRY_POINT_GROUP = "pragma.provider"

SDK_DISTRIBUTION = "pragmatiks-sdk"

ENTRY_POINT_DECLARATION = '[project.entry-points."pragma.provider"] <name> = "<package>"'


class ProviderDeclarationError(Exception):
    """Raised when a distribution declares no loadable provider through its ``pragma.provider`` entry point."""


@dataclass(frozen=True)
class DeclaredProvider:
    """A provider distribution and the resource types its package defines.

    Attributes:
        distribution: PEP 503-normalized wheel distribution name.
        short_name: Entry-point name the distribution declares.
        package: Importable package the entry point names.
        distribution_version: Installed version of the distribution.
        sdk_version: Installed version of the Pragmatiks SDK.
        resource_types: Resource classes the package defines, by resource name.
    """

    distribution: str
    short_name: str
    package: str
    distribution_version: str
    sdk_version: str
    resource_types: dict[str, type[Resource]]

    def build_handshake_params(self) -> HandshakeParams:
        """Build the handshake a worker serving this provider sends first.

        Returns:
            The handshake offering :data:`OFFERED_PROTOCOL_VERSIONS`, with every resource
            type and its capabilities.
        """
        return HandshakeParams(
            protocol_versions=OFFERED_PROTOCOL_VERSIONS,
            sdk_version=self.sdk_version,
            distribution=self.distribution,
            short_name=self.short_name,
            package=self.package,
            resource_types=[
                build_resource_type_capabilities(resource, resource_class)
                for resource, resource_class in sorted(self.resource_types.items())
            ],
        )


def build_resource_type_capabilities(resource: str, resource_class: type[Resource]) -> ResourceTypeCapabilities:
    """Build what a resource type implements.

    Args:
        resource: Registered resource type name.
        resource_class: The resource class.

    Returns:
        Whether the class defines ``on_observe``, is declared computed, and
        overrides ``logs`` and ``health``.
    """
    return ResourceTypeCapabilities(
        resource=resource,
        observe=resource_class.on_observe is not Resource.on_observe,
        computed=resource_class.computed,
        logs=resource_class.logs is not Resource.logs,
        health=resource_class.health is not Resource.health,
    )


def load_declared_provider(distribution: str) -> DeclaredProvider:
    """Load the provider an installed distribution declares and import its package.

    Args:
        distribution: Wheel distribution name of the provider.

    Returns:
        The declared provider with its discovered resource types.

    Raises:
        ProviderDeclarationError: If the distribution is not installed, does
            not declare exactly one ``pragma.provider`` entry point, names an
            object or a plain module instead of a package, defines no
            resource types, or a resource type the package imports from
            outside itself defines no ``on_observe``.
        MissingObserveError: If a non-computed resource type the package
            defines has no ``on_observe``. Any other exception the provider
            package raises while it is imported propagates unchanged.
    """  # noqa: DOC502
    normalized_distribution = normalize_distribution_name(distribution)

    try:
        distribution_version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as error:
        raise ProviderDeclarationError(f"Distribution {normalized_distribution!r} is not installed") from error

    sdk_version = importlib.metadata.version(SDK_DISTRIBUTION)
    entry_points = importlib.metadata.entry_points(group=PROVIDER_ENTRY_POINT_GROUP)
    entry_point = find_declared_entry_point(normalized_distribution, entry_points)

    resource_types = discover_declared_resources(normalized_distribution, entry_point.module)

    return DeclaredProvider(
        distribution=normalized_distribution,
        short_name=entry_point.name,
        package=entry_point.module,
        distribution_version=distribution_version,
        sdk_version=sdk_version,
        resource_types=resource_types,
    )


def find_declared_entry_point(
    normalized_distribution: str, entry_points: importlib.metadata.EntryPoints
) -> importlib.metadata.EntryPoint:
    """Find the one ``pragma.provider`` entry point a distribution declares.

    Args:
        normalized_distribution: PEP 503-normalized distribution name.
        entry_points: Every installed entry point of the ``pragma.provider`` group.

    Returns:
        The distribution's entry point.

    Raises:
        ProviderDeclarationError: If the distribution declares no entry point,
            more than one, or one naming an object instead of a package.
    """
    declared = [
        entry_point
        for entry_point in entry_points
        if read_entry_point_distribution_name(entry_point) == normalized_distribution
    ]

    if not declared:
        raise ProviderDeclarationError(
            f"Distribution {normalized_distribution!r} declares no {PROVIDER_ENTRY_POINT_GROUP!r} entry point. "
            f"Add {ENTRY_POINT_DECLARATION} to pyproject.toml."
        )

    if len(declared) > 1:
        names = sorted(entry_point.name for entry_point in declared)
        raise ProviderDeclarationError(
            f"Distribution {normalized_distribution!r} declares {len(declared)} {PROVIDER_ENTRY_POINT_GROUP!r} "
            f"entry points ({', '.join(names)}). Keep exactly one."
        )

    entry_point = declared[0]

    if entry_point.attr is not None:
        raise ProviderDeclarationError(
            f"Entry point {entry_point.name!r} of {normalized_distribution!r} must name a package, "
            f"not {entry_point.value!r}. Declare it as {ENTRY_POINT_DECLARATION}."
        )

    return entry_point


def discover_declared_resources(normalized_distribution: str, package: str) -> dict[str, type[Resource]]:
    """Import a declared provider's package and discover the resource types it defines.

    Args:
        normalized_distribution: PEP 503-normalized name of the declaring distribution.
        package: Importable package the entry point names.

    Returns:
        Resource classes the package defines, by resource name.

    Raises:
        ProviderDeclarationError: If the entry point names a plain module
            instead of a package, the package defines no resource types, or a
            resource type outside the package defines no ``on_observe``.
        MissingObserveError: If a non-computed resource type the package
            defines has no ``on_observe``. Any other exception the package
            raises while it is imported propagates unchanged.
    """  # noqa: DOC502
    try:
        resource_types = discover_resources(package)
    except MissingObserveError as error:
        if is_module_in_package(error.module_name, package):
            raise

        raise ProviderDeclarationError(
            f"Resource type {error.resource!r} in module {error.module_name!r}, which {package!r} imports from "
            "outside the package, defines no on_observe. Add on_observe to it, or, if it comes from another "
            "provider, depend on a version of that provider built with SDK 14 or later."
        ) from error

    imported_package = importlib.import_module(package)

    if not hasattr(imported_package, "__path__"):
        raise ProviderDeclarationError(
            f"Entry point of {normalized_distribution!r} names module {package!r}, not a package. "
            "Point the entry point at the package holding the @provider.resource() classes."
        )

    if not resource_types:
        raise ProviderDeclarationError(
            f"Package {package!r} declared by {normalized_distribution!r} defines no resource types. "
            "Point the entry point at the package holding the @provider.resource() classes."
        )

    return resource_types


def read_entry_point_distribution_name(entry_point: importlib.metadata.EntryPoint) -> str | None:
    """Read the normalized name of the distribution that declares an entry point from its metadata.

    Args:
        entry_point: An installed entry point.

    Returns:
        The PEP 503-normalized distribution name, or ``None`` when the entry
        point carries no distribution.
    """
    if entry_point.dist is None:
        return None

    return normalize_distribution_name(entry_point.dist.name)


def normalize_distribution_name(name: str) -> str:
    """Normalize a distribution name the way PEP 503 compares them.

    Args:
        name: Distribution name.

    Returns:
        The name lowercased, with runs of ``-``, ``_`` and ``.`` collapsed to ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).lower()
