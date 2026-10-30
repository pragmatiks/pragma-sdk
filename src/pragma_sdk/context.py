"""Runtime context for provider lifecycle methods.

This module provides the interface between SDK code and the runtime that
executes provider lifecycle methods. Provider authors use SDK functions
like wait_for_resource_state(), and the runtime injects its implementation
before calling lifecycle methods.

The RuntimeContext Protocol defines what the runtime must implement.
Provider code should NOT import RuntimeContext directly - use the
exported functions instead.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

from pragma_sdk.provider_entry_point import is_module_in_package
from pragma_sdk.types import LifecycleState


if TYPE_CHECKING:
    from collections.abc import Generator

    from pragma_sdk.models.references import OwnerReference


class RuntimeContext(Protocol):
    """Interface that runtime must implement for SDK functions.

    The runtime injects an object implementing this protocol into the
    context before calling lifecycle methods. SDK functions delegate
    to this context.
    """

    async def wait_for_state(
        self,
        resource_id: str,
        target_state: LifecycleState,
        timeout: float,
    ) -> dict[str, Any]:
        """Wait for a resource to reach a specific lifecycle state.

        Args:
            resource_id: Unique resource ID (e.g., "provider/type/name").
            target_state: State to wait for (typically READY or FAILED).
            timeout: Maximum seconds to wait.

        Returns:
            A dict with exactly two keys: ``lifecycle_state``, the state the
            resource reached, and ``outputs``, its outputs or ``None``.

        Raises:
            RuntimeError: If the host's wait fails, including when the target
                state is not reached within ``timeout``.
        """
        ...

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        """Apply a resource through the API.

        Sends the resource to the API for creation or update. The API will
        validate, persist, and emit lifecycle events for provider processing.

        Args:
            resource_data: Serialized resource data including provider, resource,
                name, config, owner_references, etc.

        Raises:
            RuntimeError: If the resource could not be applied.
        """
        ...


_runtime_context: ContextVar[RuntimeContext | None] = ContextVar("_runtime_context", default=None)
_resource_owner: ContextVar[OwnerReference | None] = ContextVar("_resource_owner", default=None)
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


async def wait_for_resource_state(
    resource_id: str,
    target_state: LifecycleState,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Wait for a resource to reach a specific lifecycle state.

    Call this from within provider lifecycle handlers (on_create, on_update,
    on_delete) to wait for another resource's state change. Useful for
    coordinating between resources where one needs another to be READY.

    Args:
        resource_id: Unique resource ID (e.g., "provider/type/name").
        target_state: State to wait for (typically READY or FAILED).
        timeout: Maximum seconds to wait.

    Returns:
        A dict with exactly two keys: ``lifecycle_state``, the state the
        resource reached, and ``outputs``, its outputs or ``None``.

    Raises:
        RuntimeError: If called outside a lifecycle handler context, or the
            host's wait fails, including when the target state is not reached
            within ``timeout``.

    Example:
        ```python
        from pragma_sdk import Resource, LifecycleState
        from pragma_sdk.context import wait_for_resource_state

        class MyResource(Resource[MyConfig, MyOutputs]):
            async def on_create(self):
                data = await wait_for_resource_state(
                    "other_provider/type/name",
                    LifecycleState.READY,
                    timeout=30.0,
                )
                connection_url = data["outputs"]["url"]
        ```
    """
    ctx = _runtime_context.get()
    if ctx is None:
        raise RuntimeError("wait_for_resource_state must be called from within a provider lifecycle handler")
    return await ctx.wait_for_state(resource_id, target_state, timeout)


async def apply_resource(resource_data: dict[str, Any]) -> None:
    """Apply a resource through the API.

    Call this from within provider lifecycle handlers to create or update
    subresources. The resource will be sent to the API, persisted, and
    processed through the normal lifecycle event flow.

    Args:
        resource_data: Serialized resource data including provider, resource,
            name, config, owner_references, etc.

    Raises:
        RuntimeError: If called outside a lifecycle handler context or if
            the resource could not be applied.

    Example:
        ```python
        class AppResource(Resource[AppConfig, AppOutputs]):
            async def on_create(self):
                db = DatabaseResource(
                    name=f"{self.name}-db",
                    config=DbConfig(port=5432),
                )
                await db.apply()  # Owner automatically set from context
                await db.wait_ready()
                return AppOutputs(db_url=db.outputs.connection_url)
        ```
    """
    ctx = _runtime_context.get()
    if ctx is None:
        raise RuntimeError("apply_resource must be called from within a provider lifecycle handler")
    await ctx.apply_resource(resource_data)


@contextmanager
def resource_owner_scope(owner: OwnerReference) -> Generator[None]:
    """Bind the executing resource as owner for child resources.

    Called by the runtime before invoking lifecycle methods. When a child
    resource calls apply() within the block, this owner is automatically
    added to its owner_references. The previous owner is restored on exit.

    Args:
        owner: OwnerReference for the currently executing resource.
    """
    token = _resource_owner.set(owner)
    try:
        yield
    finally:
        _resource_owner.reset(token)


def resource_owner() -> OwnerReference | None:
    """Get the current resource owner, if any.

    Returns:
        OwnerReference for the currently executing resource, or None if
        not in a lifecycle handler context.
    """
    return _resource_owner.get()


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
