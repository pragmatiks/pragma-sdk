"""Provider class for grouping Resource classes (like FastAPI's APIRouter)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pragma_sdk.models import Resource


ResourceT = TypeVar("ResourceT", bound=Resource)

RESOURCE_MARKER = "__pragma_resource__"


class MissingObserveError(TypeError):
    """Raised when a non-computed resource type does not define ``on_observe``."""

    def __init__(self, resource_name: str) -> None:
        """Build the error for a resource type.

        Args:
            resource_name: Registered name of the offending resource type.
        """
        super().__init__(
            f'resource "{resource_name}" does not define on_observe. '
            "Add it, or declare the resource computed, and publish a new version."
        )


class Provider:
    """Group Resource classes under a provider.

    Provider identity (the catalog name like ``pragmatiks/gcp``) is an
    external concern injected at build time and runtime. The Provider
    class only groups Resource classes and sets their ``resource`` class
    variable.

    Example:
        from pragma_sdk import Provider, Resource, Config, Outputs

        postgres = Provider()

        @postgres.resource("database")
        class Database(Resource[DatabaseConfig, DatabaseOutputs]):
            async def on_create(self) -> DatabaseOutputs:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_observe(self) -> DatabaseOutputs | None:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_update(self, previous_config: DatabaseConfig | None) -> DatabaseOutputs:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_delete(self) -> None:
                pass
    """

    def __init__(self) -> None:
        """Initialize a Provider."""
        self._resources: dict[str, type[Resource]] = {}

    def resource(self, name: str) -> Callable[[type[ResourceT]], type[ResourceT]]:
        """Register a Resource class under this provider.

        Args:
            name: Resource type name (e.g., "database", "warehouse").

        Returns:
            Decorator function that registers the Resource class.

        Raises:
            TypeError: If the decorated object is not a Resource subclass or declares ``computed`` as a field.
            MissingObserveError: If a non-computed class does not override ``on_observe``.
            ValueError: If ``name`` is already registered on this provider.

        Example:
            @postgres.resource("database")
            class Database(Resource[DatabaseConfig, DatabaseOutputs]):
                ...
        """  # noqa: DOC502

        def decorator(cls: type[ResourceT]) -> type[ResourceT]:
            if not isinstance(cls, type) or not issubclass(cls, Resource):
                raise TypeError(f"@resource() can only decorate Resource subclasses, got {cls!r}")

            if "computed" in cls.model_fields:
                raise TypeError(
                    f'resource "{name}" declares computed as a field; '
                    "write a bare `computed = True` without a type annotation."
                )

            if not cls.computed and cls.on_observe is Resource.on_observe:
                raise MissingObserveError(name)

            cls.resource = name

            setattr(cls, RESOURCE_MARKER, True)

            if name in self._resources:
                existing = self._resources[name]
                raise ValueError(
                    f"Resource '{name}' already registered (existing: {existing.__name__}, new: {cls.__name__})"
                )

            self._resources[name] = cls
            return cls

        return decorator

    @property
    def resources(self) -> dict[str, type[Resource]]:
        """All resources registered with this provider.

        Returns:
            Dictionary mapping resource names to Resource classes.
        """
        return self._resources.copy()

    def __repr__(self) -> str:
        """Return string representation of this provider.

        Returns:
            String showing registered resources.
        """
        return f"Provider(resources={list(self._resources.keys())})"
