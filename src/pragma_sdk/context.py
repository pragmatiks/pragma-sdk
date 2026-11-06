"""Runtime context for provider lifecycle methods.

This module provides the interface between SDK code and the runtime that
executes provider lifecycle methods. ``Resource.apply`` and
``Resource.read`` reach the platform through :func:`apply_resource` and
:func:`read_resource`; the runtime injects its implementation of the
RuntimeContext protocol before calling lifecycle methods.

Provider code uses ``Resource.apply`` and ``Resource.read``, never
RuntimeContext or this module's callback functions.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

from pragma_sdk.provider_entry_point import is_module_in_package


if TYPE_CHECKING:
    from collections.abc import Generator

    from pragma_sdk.protocol.frames import ChildResourceStatePayload


class RuntimeContext(Protocol):
    """Interface that runtime must implement for SDK functions.

    The runtime injects an object implementing this protocol into the
    context before calling lifecycle methods. SDK functions delegate
    to this context.
    """

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        """Apply a resource through the API, owned by the dispatched resource.

        Sends the resource to the API for creation or update. The API will
        validate, persist, and emit lifecycle events for provider processing.

        Args:
            resource_data: Serialized resource data: provider, resource,
                name, config and tags.

        Raises:
            CallbackRefusedError: If the platform refused the apply, with its
                reason as the text.
            RuntimeError: If the resource could not be applied.
        """
        ...

    async def read_resource(self, provider: str, resource: str, name: str) -> ChildResourceStatePayload | None:
        """Read a resource the dispatched resource owns, in its project.

        Args:
            provider: Provider catalog name of the resource.
            resource: Resource type name of the resource.
            name: Name of the resource.

        Returns:
            The resource as the platform records it, or ``None`` when no live
            resource has that identity.

        Raises:
            CallbackRefusedError: If the platform refused the read, such as
                for a live resource the dispatched resource does not own.
            RuntimeError: If the resource could not be read.
        """
        ...


_runtime_context: ContextVar[RuntimeContext | None] = ContextVar("_runtime_context", default=None)
_package_catalog_names: ContextVar[dict[str, str] | None] = ContextVar("_package_catalog_names", default=None)


@contextmanager
def runtime_context_scope(context: RuntimeContext) -> Generator[None]:
    """Bind the runtime context for the duration of the ``with`` block.

    Called by the runtime before invoking lifecycle methods. The previous
    context is restored on exit, even if the block raises.

    Args:
        context: Runtime context implementing the RuntimeContext protocol.
    """
    token = _runtime_context.set(context)
    try:
        yield
    finally:
        _runtime_context.reset(token)


def runtime_context() -> RuntimeContext | None:
    """Get the current runtime context, if any.

    Returns:
        Current RuntimeContext or None if not in a lifecycle handler.
    """
    return _runtime_context.get()


def require_runtime_context(callback_name: str) -> RuntimeContext:
    """Return the bound runtime context, refusing a callback made outside a lifecycle method.

    Args:
        callback_name: Name of the callback being made, named in the error.

    Returns:
        The bound runtime context.

    Raises:
        RuntimeError: If no runtime context is bound, such as in
            ``on_observe`` or outside a lifecycle method the Pragmatiks
            runtime runs.
    """
    context = _runtime_context.get()

    if context is None:
        raise RuntimeError(
            f"{callback_name} is available only in on_create, on_update and on_delete, "
            "while the Pragmatiks runtime runs them"
        )

    return context


async def apply_resource(resource_data: dict[str, Any]) -> None:
    """Apply a resource through the API, owned by the resource whose lifecycle method runs.

    Provider code calls ``Resource.apply`` instead. The platform names the
    dispatched resource as the owner and applies the resource in its
    project.

    Args:
        resource_data: Serialized resource data: provider, resource, name,
            config and tags.

    Raises:
        CallbackRefusedError: If the platform refused the apply, such as for
            a resource another resource owns, with its reason as the text.
        RuntimeError: If called outside ``on_create``, ``on_update`` or
            ``on_delete`` run by the Pragmatiks runtime, or if the resource
            could not be applied.
    """  # noqa: DOC502
    await require_runtime_context("apply_resource").apply_resource(resource_data)


async def read_resource(provider: str, resource: str, name: str) -> ChildResourceStatePayload | None:
    """Read a resource the resource whose lifecycle method runs owns.

    Provider code calls ``Resource.read`` instead. The platform reads in
    the running resource's project.

    Args:
        provider: Provider catalog name of the resource.
        resource: Resource type name of the resource.
        name: Name of the resource.

    Returns:
        The resource as the platform records it, or ``None`` when no live
        resource has that identity.

    Raises:
        CallbackRefusedError: If the platform refused the read, such as for a
            live resource the running resource does not own.
        RuntimeError: If called outside ``on_create``, ``on_update`` or
            ``on_delete`` run by the Pragmatiks runtime, or if the resource
            could not be read.
    """  # noqa: DOC502
    return await require_runtime_context("read_resource").read_resource(provider, resource, name)


@contextmanager
def package_catalog_names_scope(package_catalog_names: dict[str, str]) -> Generator[None]:
    """Bind which provider catalog defines the resource classes of each package, for the ``with`` block.

    The previous binding is restored on exit, even if the block raises.

    Args:
        package_catalog_names: Provider catalog name (e.g., "pragmatiks/gcp")
            of each importable package a provider distribution declares,
            keyed by package (e.g., "gcp_provider").
    """
    token = _package_catalog_names.set(package_catalog_names)
    try:
        yield
    finally:
        _package_catalog_names.reset(token)


def compute_defining_provider(resource_class: type) -> str:
    """Compute the catalog name of the provider whose distribution defines a resource class.

    The class's module is matched against the packages bound by
    :func:`package_catalog_names_scope`; the longest package that is the
    module or one of its parents wins, so a class keeps its defining
    provider wherever an embedding provider uses it.

    Args:
        resource_class: The resource class.

    Returns:
        Provider catalog name (e.g., "pragmatiks/gcp").

    Raises:
        RuntimeError: If no package catalog names are bound, or no bound
            package holds the class's module.
    """
    package_catalog_names = _package_catalog_names.get()

    if package_catalog_names is None:
        raise RuntimeError(
            f"The provider of {resource_class.__qualname__} is known only inside a lifecycle method the "
            "Pragmatiks runtime or the ProviderHarness runs. Read it from such a lifecycle method."
        )

    module = resource_class.__module__
    package = max(
        (package for package in package_catalog_names if is_module_in_package(module, package)),
        key=len,
        default=None,
    )

    if package is None:
        raise RuntimeError(
            f"{resource_class.__qualname__} is defined in module {module!r}, which belongs neither to the "
            "provider nor to a provider it embeds. Depend on the provider defining it, so that provider is "
            "declared under pragma.embeds, then publish again."
        )

    return package_catalog_names[package]
