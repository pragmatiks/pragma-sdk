"""Test harness for local provider development without production infrastructure.

Provides a mock runtime environment for testing Resource lifecycle methods
(on_create, on_update, on_delete) without connecting to the platform.

The harness brokers child resources as the platform does: ``Resource.apply``
and ``Resource.read`` in ``on_create``, ``on_update`` and ``on_delete`` reach
an in-memory record of the harness project. A new or changed child starts
PENDING; the test moves it on with :meth:`ProviderHarness.set_child_state`
and runs the owner's next pass with :meth:`ProviderHarness.resume`. A FAILED
child is the test's own assertion: the harness does not fail its owner.

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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pragma_sdk.context import compute_defining_provider, package_catalog_names_scope, runtime_context_scope
from pragma_sdk.models import ChildrenPending, Config, Outputs, Resource, ResourceIdentity
from pragma_sdk.protocol.codec import build_json_payload
from pragma_sdk.protocol.frames import ChildResourceStatePayload
from pragma_sdk.provider.harness_children import BrokeredResource, HarnessChildBroker, build_nothing_pending_message
from pragma_sdk.provider.outputs import normalize_outputs, refuse_children_pending
from pragma_sdk.types import CopyContext, CopyResult, LifecycleState, PatchDefinition, PatchResult


class EventType(StrEnum):
    """Type of lifecycle operation being tested."""

    CREATE = "create"
    OBSERVE = "observe"
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
    and `.error` for any exception raised. `.normalized_outputs` holds the
    outputs as the platform compares a create's or an update's with an
    observe's: validated against the type's Outputs class and encoded as
    plain JSON data, secrets revealed. `.children_pending` is set when
    ``on_create`` or ``on_update`` returned ``ChildrenPending``; such a
    result carries no outputs. For copy and patch operations, use
    `.copy_result` and `.patch_result` respectively.
    """

    success: bool
    outputs: Outputs | None = None
    normalized_outputs: dict[str, Any] | None = None
    children_pending: bool = False
    copy_result: CopyResult | None = None
    patch_result: PatchResult | None = None
    error: Exception | None = None
    resource: Resource | None = None
    event: LifecycleEvent | None = None

    @property
    def failed(self) -> bool:
        """Whether the lifecycle method failed."""
        return not self.success


def build_package_catalog_names(
    resource_class: type[Resource], provider_name: str, package_catalog_names: dict[str, str]
) -> dict[str, str]:
    """Build the package catalog names bound while a harness runs a lifecycle method of a resource class.

    Args:
        resource_class: The Resource subclass under test.
        provider_name: Catalog name of the provider under test.
        package_catalog_names: Catalog name of each other provider package,
            keyed by package.

    Returns:
        ``package_catalog_names`` with the tested class's top-level package
        mapped to ``provider_name``.
    """
    return {**package_catalog_names, resource_class.__module__.partition(".")[0]: provider_name}


async def run_refusing_children_pending[ResultT](
    lifecycle_call: Awaitable[ResultT], resource_class: type[Resource], method_name: str
) -> ResultT:
    """Await a lifecycle method that may not return ``ChildrenPending``, refusing it when it does.

    Args:
        lifecycle_call: The started lifecycle method call, such as ``resource.on_delete()``.
        resource_class: The Resource subclass under test.
        method_name: The lifecycle method called, named in the refusal.

    Returns:
        What the method returned.

    Raises:
        ValueError: If the method returned ``ChildrenPending``.
    """  # noqa: DOC502
    result = await lifecycle_call
    refuse_children_pending(result, resource_class, method_name)

    return result


class ProviderHarness:
    """Mock runtime for local provider development and testing.

    Test Resource lifecycle methods (on_create, on_update, on_delete) without
    connecting to the platform. Child resources a method applies are brokered
    in memory: inspect them with :meth:`children`, settle them with
    :meth:`set_child_state` and run the owner's next pass with :meth:`resume`.

    Example:
        harness = ProviderHarness()

        create_result = await harness.invoke_create(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db")
        )
        assert create_result.success

        observe_result = await harness.invoke_observe(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db")
        )
        assert observe_result.success
        assert observe_result.normalized_outputs == create_result.normalized_outputs

        update_result = await harness.invoke_update(
            Database,
            name="test-db",
            config=DatabaseConfig(name="test-db", size_gb=20),
            previous_config=DatabaseConfig(name="test-db", size_gb=10),
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
        package_catalog_names: dict[str, str] | None = None,
    ) -> None:
        """Initialize harness for testing.

        Args:
            provider_name: Provider catalog name a resource of a class from
                the tested class's top-level package carries during
                lifecycle calls. Defaults to "test/local".
            project_id: Project identifier stamped on all resources
                constructed inside the harness. Defaults to ``"harness-test"``.
            package_catalog_names: Provider catalog name of each other
                provider package whose resource classes the tested class
                builds, keyed by package (e.g., ``{"gcp_provider":
                "pragmatiks/gcp"}``), as the runtime names the providers a
                provider embeds. A resource of a class from a package neither
                here nor the tested class's raises ``RuntimeError`` on
                ``provider``. Defaults to none.
        """
        self._provider_name = provider_name
        self._project_id = project_id
        self.package_catalog_names = dict(package_catalog_names or {})
        self._events: list[LifecycleEvent] = []
        self._results: list[LifecycleResult] = []

        self.child_broker = HarnessChildBroker(project_id)
        self._completed_configs: dict[tuple[type[Resource], str], Config] = {}

    @property
    def project_id(self) -> str:
        """Project identifier stamped on resources built by this harness."""
        return self._project_id

    @property
    def events(self) -> list[LifecycleEvent]:
        """All events that have been processed."""
        return self._events.copy()

    @property
    def results(self) -> list[LifecycleResult]:
        """All results from processed events."""
        return self._results.copy()

    @property
    def completed_configs(self) -> dict[tuple[type[Resource], str], Config]:
        """Config of the last create or update that returned outputs, keyed by resource class and name.

        A successful delete removes the entry. ``resume`` passes it to
        ``on_update`` as ``previous_config``.
        """
        return self._completed_configs.copy()

    def build_identity(self, resource_class: type[Resource], name: str) -> ResourceIdentity:
        """Build the identity a resource of a class carries in this harness.

        Args:
            resource_class: The Resource subclass.
            name: Resource instance name.

        Returns:
            Its identity in the harness project, naming the provider that
            defines the class, as :meth:`children` takes it.
        """
        with package_catalog_names_scope(
            build_package_catalog_names(resource_class, self._provider_name, self.package_catalog_names)
        ):
            provider = compute_defining_provider(resource_class)

        return self.child_broker.build_identity(provider, resource_class.resource, name)

    def clear(self) -> None:
        """Clear event and result history, brokered resources and completed configs."""
        self._events.clear()
        self._results.clear()
        self.child_broker.clear()
        self._completed_configs.clear()

    def children(self, owner: ResourceIdentity) -> dict[ResourceIdentity, ChildResourceStatePayload]:
        """List the children a resource owns.

        Args:
            owner: The owner's identity, such as from :meth:`build_identity`.

        Returns:
            Each child's recorded state, config as applied, keyed by identity.
        """
        return {
            identity: record.state for identity, record in self.child_broker.resources.items() if record.owner == owner
        }

    def set_child_state(
        self,
        identity: ResourceIdentity,
        lifecycle_state: LifecycleState,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Set a brokered resource's lifecycle state and outputs, as its own lifecycle would.

        Args:
            identity: The resource identity, as :meth:`children` keys it.
            lifecycle_state: The state to record, such as ``LifecycleState.READY``.
            outputs: The outputs to record, or ``None``.

        Raises:
            KeyError: If no resource with that identity was applied or seeded.
        """  # noqa: DOC502
        record = self.child_broker.resources[identity]
        self.child_broker.resources[identity] = BrokeredResource(
            record.owner,
            ChildResourceStatePayload(lifecycle_state=lifecycle_state, config=record.state.config, outputs=outputs),
        )

    def seed_resource(
        self,
        identity: ResourceIdentity,
        config: dict[str, Any],
        owner: ResourceIdentity | None,
        lifecycle_state: LifecycleState,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Record a resource that exists before the tested pass, such as one another resource owns.

        Args:
            identity: The resource identity, in the harness project.
            config: Its config, as applied.
            owner: Its owner, or ``None`` for a resource no resource owns.
            lifecycle_state: Its lifecycle state.
            outputs: Its outputs, or ``None``.

        Raises:
            ValueError: If the identity names another project than the harness's.
        """
        if identity.project_id != self._project_id:
            raise ValueError(
                f"Seeded resource {identity} is outside the harness project {self._project_id!r}; "
                "build its identity with build_identity."
            )

        self.child_broker.resources[identity] = BrokeredResource(
            owner,
            ChildResourceStatePayload(lifecycle_state=lifecycle_state, config=config, outputs=outputs),
        )

    async def resume(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Run the pass the platform runs once a child of a resource that returned ``ChildrenPending`` settles.

        As the platform does, it runs ``on_update`` with the config of the
        last create or update that returned outputs (``None`` when none
        did). For a type that is not computed it runs ``on_observe`` first,
        and runs ``on_create`` instead when that reports no object.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration for the resource.
            tags: Tags to attach to the resource.

        Returns:
            The failed observe's result, or the create's or update's result.
        """
        previous_config = self._completed_configs.get((resource_class, name))

        if resource_class.computed:
            return await self.invoke_update(resource_class, name, config, previous_config, tags)

        observed = await self.invoke_observe(resource_class, name, config, tags)

        if observed.failed:
            return observed

        if observed.outputs is None:
            return await self.invoke_create(resource_class, name, config, tags)

        return await self.invoke_update(resource_class, name, config, previous_config, tags)

    async def invoke(
        self,
        event_type: EventType,
        resource_class: type[Resource],
        name: str,
        config: Config,
        previous_config: Config | None,
        tags: list[str] | None,
        handler: Callable[[Resource], Awaitable[Outputs | ChildrenPending | None]],
    ) -> LifecycleResult:
        """Run one lifecycle handler on a freshly built resource, recording the event and result.

        Args:
            event_type: Lifecycle operation being recorded.
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration for the resource.
            previous_config: Configuration recorded on the event as last applied.
            tags: Tags to attach to the resource.
            handler: Lifecycle method to await on the built resource.

        Returns:
            Result carrying the handler's outputs and their normalized form,
            whether it returned ``ChildrenPending``, or the exception it
            raised, an outputs validation failure included.
        """
        event = LifecycleEvent(
            event_id=str(uuid4()),
            event_type=event_type,
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
            outputs=None,
            lifecycle_state=LifecycleState.PROCESSING,
            tags=tags,
        )

        try:
            with package_catalog_names_scope(
                build_package_catalog_names(resource_class, self._provider_name, self.package_catalog_names)
            ):
                handler_result = await handler(resource)

            outputs = None if isinstance(handler_result, ChildrenPending) else handler_result
            result = LifecycleResult(
                success=True,
                outputs=outputs,
                normalized_outputs=build_json_payload(normalize_outputs(resource_class, outputs)),
                children_pending=isinstance(handler_result, ChildrenPending),
                resource=resource,
                event=event,
            )

            if outputs is not None and event_type in {EventType.CREATE, EventType.UPDATE}:
                self._completed_configs[resource_class, name] = config

            if event_type == EventType.DELETE:
                self._completed_configs.pop((resource_class, name), None)
        except Exception as e:
            result = LifecycleResult(
                success=False,
                error=e,
                resource=resource,
                event=event,
            )

        self._results.append(result)
        return result

    async def run_dispatch(
        self, resource: Resource, lifecycle_call: Awaitable[Outputs | ChildrenPending | None]
    ) -> Outputs | ChildrenPending | None:
        """Await a create, update or delete with its child applies and reads brokered.

        Args:
            resource: The dispatched resource, owner of what the method applies.
            lifecycle_call: The started lifecycle method call, such as ``resource.on_create()``.

        Returns:
            What the method returned.

        Raises:
            RuntimeError: If the method returned ``ChildrenPending`` after it
                applied no child, or only children READY and unchanged, with
                the platform's reason.
        """
        context = self.child_broker.bind(resource.identity)

        with runtime_context_scope(context):
            result = await lifecycle_call

        if isinstance(result, ChildrenPending) and context.has_nothing_pending():
            raise RuntimeError(build_nothing_pending_message(resource.identity))

        return result

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
        return await self.invoke(
            EventType.CREATE,
            resource_class,
            name,
            config,
            None,
            tags,
            lambda resource: self.run_dispatch(resource, resource.on_create()),
        )

    async def invoke_observe(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_observe lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration the resource locates its object with.
            tags: Tags to attach to the resource.

        Returns:
            Result whose ``outputs`` is ``None`` when the object is absent.
        """
        return await self.invoke(
            EventType.OBSERVE,
            resource_class,
            name,
            config,
            None,
            tags,
            lambda resource: run_refusing_children_pending(resource.on_observe(), resource_class, "on_observe"),
        )

    async def invoke_update(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        previous_config: Config | None,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_update lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: New configuration for the resource.
            previous_config: Configuration passed to on_update, or ``None`` when unknown.
            tags: Tags to attach to the resource.

        Returns:
            Result containing success status, outputs, and any error.
        """
        return await self.invoke(
            EventType.UPDATE,
            resource_class,
            name,
            config,
            previous_config,
            tags,
            lambda resource: self.run_dispatch(resource, resource.on_update(previous_config)),
        )

    async def invoke_delete(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_delete lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration for the resource.
            tags: Tags to attach to the resource.

        Returns:
            Result containing success status and any error.
        """
        return await self.invoke(
            EventType.DELETE,
            resource_class,
            name,
            config,
            None,
            tags,
            lambda resource: self.run_dispatch(
                resource,
                run_refusing_children_pending(resource.on_delete(), resource_class, "on_delete"),
            ),
        )

    async def invoke_copy(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        context: CopyContext,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_copy lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Source resource instance name.
            config: Configuration of the source resource.
            context: Copy context with target name, tags, strategy, and metadata.
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
            outputs=None,
            lifecycle_state=LifecycleState.PROCESSING,
            tags=tags,
        )

        try:
            with package_catalog_names_scope(
                build_package_catalog_names(resource_class, self._provider_name, self.package_catalog_names)
            ):
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

        self._results.append(result)
        return result

    async def invoke_patch(
        self,
        resource_class: type[Resource],
        name: str,
        config: Config,
        patch: PatchDefinition,
        tags: list[str] | None = None,
    ) -> LifecycleResult:
        """Invoke the on_patch lifecycle method.

        Args:
            resource_class: Resource subclass to test.
            name: Resource instance name.
            config: Configuration of the resource.
            patch: Patch definition to apply.
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
            outputs=None,
            lifecycle_state=LifecycleState.PROCESSING,
            tags=tags,
        )

        try:
            with package_catalog_names_scope(
                build_package_catalog_names(resource_class, self._provider_name, self.package_catalog_names)
            ):
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

        self._results.append(result)
        return result
