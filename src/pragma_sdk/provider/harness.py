"""Test harness for local provider development without production infrastructure.

Provides a mock runtime environment for testing Resource lifecycle methods
(on_create, on_update, on_delete) without connecting to NATS or the platform.

Example:
    from typing import ClassVar
    from pragma_sdk import Resource, Config, Outputs
    from pragma_sdk.provider import ProviderHarness

    class MyConfig(Config):
        setting: str

    class MyOutputs(Outputs):
        some_field: str

    class MyResource(Resource[MyConfig, MyOutputs]):
        resource: ClassVar[str] = "myresource"

        async def on_create(self) -> MyOutputs:
            return MyOutputs(some_field="created")

    async def test_my_resource():
        harness = ProviderHarness()

        result = await harness.invoke_create(
            MyResource,
            name="test-resource",
            config=MyConfig(setting="value")
        )

        assert result.success
        assert result.outputs.some_field == "created"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pragma_sdk.context import reset_provider_name, set_provider_name
from pragma_sdk.models import Config, Outputs, Resource
from pragma_sdk.types import CopyContext, CopyResult, LifecycleState, PatchDefinition, PatchResult


class EventType(StrEnum):
    """Type of lifecycle operation being tested."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COPY = "copy"
    PATCH = "patch"


@dataclass
class LifecycleEvent:
    """Simulated lifecycle event for testing."""

    event_id: str
    event_type: EventType
    resource_class: type[Resource]
    name: str
    config: Config
    previous_config: Config | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LifecycleResult:
    """Result of executing a lifecycle method in tests.

    Use `.success` or `.failed` to check status, `.outputs` for returned values,
    and `.error` for any exception raised. For copy and patch operations, use
    `.copy_result` and `.patch_result` respectively.
    """

    success: bool
    outputs: Outputs | None = None
    copy_result: CopyResult | None = None
    patch_result: PatchResult | None = None
    error: Exception | None = None
    resource: Resource | None = None
    event: LifecycleEvent | None = None

    @property
    def failed(self) -> bool:
        """Whether the lifecycle method failed."""
        return not self.success


class ProviderHarness:
    """Mock runtime for local provider development and testing.

    Test Resource lifecycle methods (on_create, on_update, on_delete) without
    connecting to NATS or other production infrastructure.

    Example:
        harness = ProviderHarness()

        create_result = await harness.invoke_create(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db")
        )
        assert create_result.success

        update_result = await harness.invoke_update(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db", size_gb=20),
            previous_config=DatabaseConfig(name="test-db", size_gb=10),
            current_outputs=create_result.outputs
        )
        assert update_result.success

        delete_result = await harness.invoke_delete(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db")
        )
        assert delete_result.success
    """

    def __init__(
        self,
        provider_name: str = "test/local",
        project_id: str = "harness-test",
    ) -> None:
        """Initialize harness for testing.

        Args:
            provider_name: Provider name to set in context during lifecycle
                calls. Defaults to "test/local".
            project_id: Project identifier assigned to every resource the
                harness constructs. Override when exercising scenarios that
                depend on multiple projects (for example, same-name-across-
                projects cases) by instantiating one harness per project.
        """
        self._provider_name = provider_name
        self._project_id = project_id
        self._events: list[LifecycleEvent] = []
        self._results: list[LifecycleResult] = []

    @property
    def project_id(self) -> str:
        """Project identifier used when constructing test resources."""
        return self._project_id

    @property
    def events(self) -> list[LifecycleEvent]:
        """All events that have been processed."""
        return self._events.copy()

    @property
    def results(self) -> list[LifecycleResult]:
        """All results from processed events."""
        return self._results.copy()

    def clear(self) -> None:
        """Clear event and result history."""
        self._events.clear()
        self._results.clear()

    async def invoke_create(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_create lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration to pass to the resource.
            tags: Tags to attach to the resource.

        Returns:
            Result containing success status, outputs, and any error.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=EventType.CREATE,
            resource_class=resource_class,
            name=name,
            config=config,
        )
        self._events.append(event)

        resource = resource_class(
            project_id=self._project_id,
            name=name,
            config=config,
            outputs=None,
            lifecycle_state=LifecycleState.PROCESSING,
            tags=tags,
        )

        provider_token = set_provider_name(self._provider_name)
        try:
            outputs = await resource.on_create()
            result = LifecycleResult(
                success=True,
                outputs=outputs,
                resource=resource,
                event=event,
            )
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )
        finally:
            reset_provider_name(provider_token)

        self._results.append(result)
        return result

    async def invoke_update(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        previous_config: Config,
        current_outputs: Outputs | None = None,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_update lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: New configuration for the resource.
            previous_config: Configuration passed to on_update for comparison.
            current_outputs: Outputs to attach to the resource instance.
            tags: Tags to attach to the resource.

        Returns:
            Result containing success status, outputs, and any error.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=EventType.UPDATE,
            resource_class=resource_class,
            name=name,
            config=config,
            previous_config=previous_config,
        )
        self._events.append(event)

        resource = resource_class(
            project_id=self._project_id,
            name=name,
            config=config,
            lifecycle_state=LifecycleState.PROCESSING,
            outputs=current_outputs,
            tags=tags,
        )

        provider_token = set_provider_name(self._provider_name)
        try:
            outputs = await resource.on_update(previous_config)
            result = LifecycleResult(
                success=True,
                outputs=outputs,
                resource=resource,
                event=event,
            )
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )
        finally:
            reset_provider_name(provider_token)

        self._results.append(result)
        return result

    async def invoke_delete(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        current_outputs: Outputs | None = None,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_delete lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration for the resource.
            current_outputs: Outputs to attach to the resource instance.
            tags: Tags to attach to the resource.

        Returns:
            Result containing success status and any error.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=EventType.DELETE,
            resource_class=resource_class,
            name=name,
            config=config,
        )
        self._events.append(event)

        resource = resource_class(
            project_id=self._project_id,
            name=name,
            config=config,
            lifecycle_state=LifecycleState.PROCESSING,
            outputs=current_outputs,
            tags=tags,
        )

        provider_token = set_provider_name(self._provider_name)
        try:
            await resource.on_delete()
            result = LifecycleResult(
                success=True,
                resource=resource,
                event=event,
            )
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )
        finally:
            reset_provider_name(provider_token)

        self._results.append(result)
        return result

    async def invoke_copy(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        context: CopyContext,
        current_outputs: Outputs | None = None,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_copy lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Source resource instance name.
            config: Configuration of the source resource.
            context: Copy context with target name, tags, strategy, and metadata.
            current_outputs: Outputs of the source resource.
            tags: Tags on the source resource.

        Returns:
            Result containing success status, copy result as outputs, and any error.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=EventType.COPY,
            resource_class=resource_class,
            name=name,
            config=config,
        )
        self._events.append(event)

        resource = resource_class(
            project_id=self._project_id,
            name=name,
            config=config,
            lifecycle_state=LifecycleState.PROCESSING,
            outputs=current_outputs,
            tags=tags,
        )

        provider_token = set_provider_name(self._provider_name)
        try:
            copy_result = await resource.on_copy(context)
            result = LifecycleResult(
                success=True,
                copy_result=copy_result,
                resource=resource,
                event=event,
            )
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )
        finally:
            reset_provider_name(provider_token)

        self._results.append(result)
        return result

    async def invoke_patch(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        patch: PatchDefinition,
        current_outputs: Outputs | None = None,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_patch lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration of the resource.
            patch: Patch definition to apply.
            current_outputs: Current outputs of the resource.
            tags: Tags on the resource.

        Returns:
            Result containing success status, patch result, and any error.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=EventType.PATCH,
            resource_class=resource_class,
            name=name,
            config=config,
        )
        self._events.append(event)

        resource = resource_class(
            project_id=self._project_id,
            name=name,
            config=config,
            lifecycle_state=LifecycleState.PROCESSING,
            outputs=current_outputs,
            tags=tags,
        )

        provider_token = set_provider_name(self._provider_name)
        try:
            patch_result = await resource.on_patch(patch)
            result = LifecycleResult(
                success=True,
                patch_result=patch_result,
                resource=resource,
                event=event,
            )
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )
        finally:
            reset_provider_name(provider_token)

        self._results.append(result)
        return result
