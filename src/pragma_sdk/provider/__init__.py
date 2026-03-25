"""Provider authoring utilities for Pragmatiks.

Develop and test providers locally without connecting to production infrastructure.

Example:
    from pragma_sdk import Resource, Config, Outputs
    from pragma_sdk.provider import Provider, ProviderHarness

    postgres = Provider()

    class DatabaseConfig(Config):
        name: str
        size_gb: int = 10

    class DatabaseOutputs(Outputs):
        connection_url: str

    @postgres.resource("database")
    class Database(Resource[DatabaseConfig, DatabaseOutputs]):
        async def on_create(self) -> DatabaseOutputs:
            return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

        async def on_update(self, previous_config: DatabaseConfig) -> DatabaseOutputs:
            return self.outputs

        async def on_delete(self) -> None:
            pass

    async def test_database_creation():
        harness = ProviderHarness()
        result = await harness.invoke_create(
            Database,
            name="my-db",
            config=DatabaseConfig(name="my-db", size_gb=20)
        )
        assert result.success
"""

from pragma_sdk.provider.discovery import discover_resources, is_registered_resource
from pragma_sdk.provider.harness import (
    EventType,
    LifecycleEvent,
    LifecycleResult,
    ProviderHarness,
)
from pragma_sdk.provider.provider import RESOURCE_MARKER, Provider


__all__ = [
    "EventType",
    "LifecycleEvent",
    "LifecycleResult",
    "Provider",
    "ProviderHarness",
    "RESOURCE_MARKER",
    "discover_resources",
    "is_registered_resource",
]
