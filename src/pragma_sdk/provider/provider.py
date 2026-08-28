"""Provider class for grouping Resource classes (like FastAPI's APIRouter)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pragma_sdk.models import Resource


ResourceT = TypeVar("ResourceT", bound=Resource)

RESOURCE_MARKER = "__pragma_resource__"


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

            async def on_update(self, previous_config: DatabaseConfig) -> DatabaseOutputs:
                return self.outputs

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

        Example:
            @postgres.resource("database")
            class Database(Resource[DatabaseConfig, DatabaseOutputs]):
                ...
        """

        def decorator(cls: type[ResourceT]) -> type[ResourceT]:
            if not isinstance(cls, type) or not issubclass(cls, Resource):
                raise TypeError(f"@resource() can only decorate Resource subclasses, got {cls!r}")

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
