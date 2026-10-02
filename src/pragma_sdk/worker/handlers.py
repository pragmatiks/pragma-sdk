"""The provider-side work behind each request the host sends the worker."""

from __future__ import annotations

from typing import assert_never

from pydantic import BaseModel

from pragma_sdk.context import RuntimeContext, provider_name_scope, resource_owner_scope, runtime_context_scope
from pragma_sdk.models import Config, Resource
from pragma_sdk.models.references import OwnerReference
from pragma_sdk.protocol.frames import DispatchParams, MigrateParams, MigrateResult, ResourceQueryParams
from pragma_sdk.types import EventType, HealthStatus, LogEntry
from pragma_sdk.worker.resource_builder import ResourceBuilder


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

    async def run_dispatch(self, params: DispatchParams, context: RuntimeContext) -> BaseModel | None:
        """Build the resource and run the lifecycle method the event names.

        The runtime context, the owner of applied resources and the provider
        name are bound for the duration of the method.

        Args:
            params: The dispatch parameters.
            context: Runtime context serving the method's platform callbacks.

        Returns:
            The outputs the method returned, or ``None``.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference with no signed URL. Any exception the lifecycle
                method raises propagates unchanged.
        """  # noqa: DOC502
        resource_class = self.find_resource_class(params.target.resource)
        resource = self.resource_builder.build_resource(resource_class, params.target, params.resolved_files)
        owner_reference = OwnerReference.model_validate(params.owner_reference.model_dump())

        with (
            runtime_context_scope(context),
            resource_owner_scope(owner_reference),
            provider_name_scope(params.provider),
        ):
            return await self.invoke_handler(params, resource, resource_class)

    async def invoke_handler(
        self, params: DispatchParams, resource: Resource, resource_class: type[Resource]
    ) -> BaseModel | None:
        """Call the lifecycle method matching the event type.

        Args:
            params: The dispatch parameters.
            resource: The built resource.
            resource_class: The resource class, used to rebuild the previous config.

        Returns:
            Outputs of ``on_create`` or ``on_update``, or ``None`` for ``on_delete``.

        Raises:
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
                return await resource.on_delete()

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

        The provider name is bound while the resource's ``logs`` runs.

        Args:
            params: The query parameters.

        Returns:
            The resource's log entries.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference. Any exception the resource's ``logs`` raises
                propagates unchanged.
        """  # noqa: DOC502
        resource = self.build_queried_resource(params)

        with provider_name_scope(params.provider):
            return [entry async for entry in resource.logs(since=params.since, tail=params.tail)]

    async def run_resource_health(self, params: ResourceQueryParams) -> HealthStatus:
        """Read a resource's health.

        The provider name is bound while the resource's ``health`` runs.

        Args:
            params: The query parameters.

        Returns:
            The resource's health status.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file
                reference. Any exception the resource's ``health`` raises
                propagates unchanged.
        """  # noqa: DOC502
        resource = self.build_queried_resource(params)

        with provider_name_scope(params.provider):
            return await resource.health()

    def build_queried_resource(self, params: ResourceQueryParams) -> Resource:
        """Build the resource a logs or health query targets.

        A query carries no signed file URLs, so a resource whose config holds
        a Pragmatiks file reference cannot be built for one.

        Args:
            params: The query parameters.

        Returns:
            The built resource.

        Raises:
            ValueError: If the provider defines no such resource type, or its
                class has no Config-typed ``config`` field.
            ValidationError: If the config does not match the resource's Config class.
            MissingResolvedFileError: If the config holds a Pragmatiks file reference.
        """  # noqa: DOC502
        resource_class = self.find_resource_class(params.target.resource)

        return self.resource_builder.build_resource(resource_class, params.target, {})

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
