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

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

from pragma_sdk.types import LifecycleState


if TYPE_CHECKING:
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
            Resource data dict from the state notification.

        Raises:
            TimeoutError: If target state not reached within timeout.
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
_current_resource_owner: ContextVar[OwnerReference | None] = ContextVar("_current_resource_owner", default=None)


def set_runtime_context(ctx: RuntimeContext) -> Any:
    """Set the runtime context for the current async context.

    Called by the runtime before invoking lifecycle methods. Returns a
    token that must be passed to reset_runtime_context() when done.

    Args:
        ctx: Runtime context implementing RuntimeContext protocol.

    Returns:
        Token for resetting the context.
    """
    return _runtime_context.set(ctx)


def reset_runtime_context(token: Any) -> None:
    """Reset the runtime context using the token from set_runtime_context().

    Args:
        token: Token returned by set_runtime_context().
    """
    _runtime_context.reset(token)


def get_runtime_context() -> RuntimeContext | None:
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
        timeout: Maximum seconds to wait before raising TimeoutError.

    Returns:
        Resource data dict from the state notification.

    Raises:
        RuntimeError: If called outside a lifecycle handler context.

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


def set_current_resource_owner(owner: OwnerReference) -> Any:
    """Set the current executing resource as owner for child resources.

    Called by the runtime before invoking lifecycle methods. When a child
    resource calls apply(), it will automatically have this owner added
    to its owner_references.

    Args:
        owner: OwnerReference for the currently executing resource.

    Returns:
        Token for resetting the owner context.
    """
    return _current_resource_owner.set(owner)


def reset_current_resource_owner(token: Any) -> None:
    """Reset the current resource owner using the token from set_current_resource_owner().

    Args:
        token: Token returned by set_current_resource_owner().
    """
    _current_resource_owner.reset(token)


def get_current_resource_owner() -> OwnerReference | None:
    """Get the current resource owner, if any.

    Returns:
        OwnerReference for the currently executing resource, or None if
        not in a lifecycle handler context.
    """
    return _current_resource_owner.get()
