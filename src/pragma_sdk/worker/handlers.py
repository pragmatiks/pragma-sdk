"""The provider-side work behind each request the host sends the worker."""

from __future__ import annotations

from typing import assert_never

import structlog
from pydantic import BaseModel

from pragma_sdk.context import RuntimeContext, runtime_context_scope
from pragma_sdk.models import ChildrenPending, Config, Resource
from pragma_sdk.protocol.frames import (
    DispatchParams,
    DispatchResult,
    MigrateParams,
    MigrateResult,
    ObserveParams,
    ObserveResult,
    ResourcePayload,
    ResourceQueryParams,
)
from pragma_sdk.provider.outputs import normalize_outputs, refuse_children_pending
from pragma_sdk.types import EventType, HealthStatus, LogEntry
from pragma_sdk.worker.resource_builder import ResourceBuilder


logger = structlog.get_logger()


class RequestHandlers:
    """Runs lifecycle methods, migrations and queries on a provider's resource types."""

    def __init__(self, resource_types: dict[str, type[Resource]], resource_builder: ResourceBuilder) -> None:
        """Initialize the handlers for one provider's resource types.

        Args:
            resource_types: Resource classes the provider defines, by resource name.
            resource_builder: Builder turning resource payloads into resources.
        """
        self.resource_types = resource_types
        self.resource_builder = resource_builder

    async def run_dispatch(self, params: DispatchParams, context: RuntimeContext) -> DispatchResult:
        """Build the resource, run the lifecycle method the event names and build the result answering the dispatch.

        The runtime context is bound for the duration of the method. The
        outputs of a create or an update are normalized as
        :func:`~pragma_sdk.provider.outputs.normalize_outputs` describes, so
        the host can compare them with what observing the object returns.
        ``ChildrenPending`` from ``on_create`` or ``on_update`` is answered
        with ``children_pending`` and no outputs, logged
        ``dispatch_children_pending``.

        Args:
            params: The dispatch parameters.
            context: Runtime context serving the method's platform callbacks.

        Returns:
            The dispatch result: the normalized outputs, or
            ``children_pending`` with no outputs.

        Raises:
            ValueError: If the provider defines no such resource type, its
                class has no Config-typed ``config`` field, it returns
                outputs without declaring an Outputs class, or ``on_delete``
                returns ``ChildrenPending``.
            ValidationError: If the config does not match the resource's
                Config class, or the outputs do not match its Outputs class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL. Any exception the lifecycle
                method raises propagates unchanged.
        """  # noqa: DOC502
        resource_class, resource = self.build_target_resource(params.target, params.resolved_files)

        with runtime_context_scope(context):
            result = await self.invoke_handler(params, resource, resource_class)

        if isinstance(result, ChildrenPending):
            logger.info("dispatch_children_pending")

            return DispatchResult(outputs=None, children_pending=True)

        return DispatchResult(outputs=normalize_outputs(resource_class, result))

    async def run_observe(self, params: ObserveParams) -> ObserveResult:
        """Build the resource and report the external object it owns through ``on_observe``.

        No runtime context is bound, so a platform callback such as
        ``apply_resource`` or ``read_resource`` raises. The outputs are
        normalized as :func:`~pragma_sdk.provider.outputs.normalize_outputs`
        describes, the same way a create's or an update's are. Logs
        ``observe_completed`` with whether the object is present.

        Args:
            params: The observe parameters.

        Returns:
            The normalized outputs of the observed object, or ``None`` when
            the object does not exist.

        Raises:
            ValueError: If the provider defines no such resource type, its
                class has no Config-typed ``config`` field, it returns
                outputs without declaring an Outputs class, or it returns
                ``ChildrenPending``.
            ValidationError: If the config does not match the resource's
                Config class, or the outputs do not match its Outputs class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL. Any exception ``on_observe``
                raises propagates unchanged.
        """  # noqa: DOC502
        resource_class, resource = self.build_target_resource(params.target, params.resolved_files)
        observed = await resource.on_observe()
        refuse_children_pending(observed, resource_class, "on_observe")
        outputs = normalize_outputs(resource_class, observed)
        logger.info("observe_completed", observed_present=outputs is not None)

        return ObserveResult(outputs=outputs)

    async def invoke_handler(
        self, params: DispatchParams, resource: Resource, resource_class: type[Resource]
    ) -> BaseModel | None:
        """Call the lifecycle method matching the event type.

        Args:
            params: The dispatch parameters.
            resource: The built resource.
            resource_class: The resource class, used to rebuild the previous config and named in a refusal.

        Returns:
            Outputs or ``ChildrenPending`` from ``on_create`` or
            ``on_update``, ``None`` from ``on_delete``.

        Raises:
            ValueError: If ``on_delete`` returns ``ChildrenPending``.
            ValidationError: If an update's previous config does not match the
                resource's Config class.
            MissingResolvedFileError: If an update's previous config holds a
                Pragmatiks file reference with no signed URL. Any exception
                the lifecycle method raises propagates unchanged.
        """  # noqa: DOC502
        match params.event_type:
            case EventType.CREATE:
                return await resource.on_create()

            case EventType.UPDATE:
                previous_config = self.build_previous_config(params, resource_class)

                return await resource.on_update(previous_config)

            case EventType.DELETE:
                deleted = await resource.on_delete()
                refuse_children_pending(deleted, resource_class, "on_delete")

                return deleted

            case _:
                assert_never(params.event_type)

    def build_previous_config(self, params: DispatchParams, resource_class: type[Resource]) -> Config | None:
        """Build the configuration an update replaces.

        Args:
            params: The dispatch parameters of an update.
            resource_class: The resource class whose Config class to build.

        Returns:
            The previous config, or ``None`` when the dispatch carries none.

        Raises:
            ValidationError: If the previous config does not match the
                resource's Config class.
            MissingResolvedFileError: If the previous config holds a
                Pragmatiks file reference with no signed URL.
        """  # noqa: DOC502
        if params.previous_config is None:
            return None

        return self.resource_builder.build_config(resource_class, params.previous_config, params.resolved_files)

    async def run_migration(self, params: MigrateParams) -> MigrateResult:
        """Run a resource class's ``upgrade`` or ``downgrade`` on one resource's state.

        Args:
            params: The migration parameters.

        Returns:
            The transformed config and outputs.

        Raises:
            TypeError: If ``upgrade`` or ``downgrade`` returns something
                that is not an iterable.
            ValueError: If the provider defines no such resource type, or
                ``upgrade`` or ``downgrade`` returns an iterable of other
                than two items.
            ValidationError: If ``upgrade`` or ``downgrade`` returns a pair
                whose config is not a dict or whose outputs is neither a
                dict nor ``None``. Any exception ``upgrade`` or
                ``downgrade`` raises propagates unchanged.
        """  # noqa: DOC502
        resource_class = self.find_resource_class(params.resource)

        if params.direction == "up":
            config, outputs = resource_class.upgrade(params.config, params.outputs)
        else:
            config, outputs = resource_class.downgrade(params.config, params.outputs)

        return MigrateResult(config=config, outputs=outputs)

    async def run_resource_logs(self, params: ResourceQueryParams) -> list[LogEntry]:
        """Collect a resource's log entries.

        Args:
            params: The query parameters.

        Returns:
            The resource's log entries.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference the query carries no signed URL for.
                Any exception the resource's ``logs`` raises propagates unchanged.
        """  # noqa: DOC502
        resource = self.build_queried_resource(params)

        return [entry async for entry in resource.logs(since=params.since, tail=params.tail)]

    async def run_resource_health(self, params: ResourceQueryParams) -> HealthStatus:
        """Read a resource's health.

        Args:
            params: The query parameters.

        Returns:
            The resource's health status.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference the query carries no signed URL for.
                Any exception the resource's ``health`` raises propagates unchanged.
        """  # noqa: DOC502
        resource = self.build_queried_resource(params)

        return await resource.health()

    def build_queried_resource(self, params: ResourceQueryParams) -> Resource:
        """Build the resource a logs or health query targets.

        Args:
            params: The query parameters.

        Returns:
            The built resource.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference the query carries no signed URL for.
        """  # noqa: DOC502
        _, resource = self.build_target_resource(params.target, params.resolved_files)

        return resource

    def build_target_resource(
        self, target: ResourcePayload, resolved_files: dict[str, str]
    ) -> tuple[type[Resource], Resource]:
        """Build the resource a request targets, together with the class it is built as.

        Args:
            target: The resource a request acts on.
            resolved_files: Signed download URLs keyed by Pragmatiks file reference.

        Returns:
            The resource class and the built resource.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL.
        """  # noqa: DOC502
        resource_class = self.find_resource_class(target.resource)

        return resource_class, self.resource_builder.build_resource(resource_class, target, resolved_files)

    def find_resource_class(self, resource: str) -> type[Resource]:
        """Look up a resource class the provider defines.

        Args:
            resource: Registered resource type name.

        Returns:
            The resource class.

        Raises:
            ValueError: If the provider defines no such resource type.
        """
        resource_class = self.resource_types.get(resource)

        if resource_class is None:
            raise ValueError(f"Unknown resource type: {resource}")

        return resource_class
