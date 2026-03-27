"""Tests for on_copy and on_patch resource lifecycle methods."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from pragma_sdk import Config, Field, Outputs, Resource
from pragma_sdk.provider import ProviderHarness
from pragma_sdk.types import (
    CompatibilityConstraint,
    CopyContext,
    CopyResult,
    CopyStrategy,
    PatchDefinition,
    PatchResult,
)


class DatabaseConfig(Config):
    """Database resource configuration.

    Attributes:
        name: Database name.
        size_gb: Storage size in gigabytes.
        version: Database engine version.
    """

    name: Field[str]
    size_gb: Field[int] = 10
    version: Field[str] = "16"


class DatabaseOutputs(Outputs):
    """Database resource outputs.

    Attributes:
        connection_url: Connection URL for the database.
    """

    connection_url: str


class CopyableDatabase(Resource[DatabaseConfig, DatabaseOutputs]):
    """Database resource that supports copy and patch."""

    resource: ClassVar[str] = "database"

    async def on_create(self) -> DatabaseOutputs:
        return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

    async def on_update(self, previous_config: DatabaseConfig) -> DatabaseOutputs:
        return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

    async def on_delete(self) -> None:
        pass

    async def on_copy(self, context: CopyContext) -> CopyResult:
        new_config = self.config.model_dump()
        new_config["name"] = context.target_name

        outputs: dict[str, Any] | None = None

        if context.strategy == CopyStrategy.STATELESS:
            outputs = {"connection_url": f"postgres://localhost/{context.target_name}"}

        return CopyResult(
            config=new_config,
            outputs=outputs,
            tags=context.tags or None,
        )

    async def on_patch(self, patch: PatchDefinition) -> PatchResult:
        if patch.payload.get("action") == "upgrade_version":
            new_version = patch.payload["target_version"]
            return PatchResult(
                success=True,
                message=f"Upgraded to version {new_version}",
                modified_config={"version": new_version},
            )

        return PatchResult(
            success=False,
            message=f"Unknown patch action: {patch.payload.get('action')}",
        )


class BasicResource(Resource[DatabaseConfig, DatabaseOutputs]):
    """Resource that does NOT support copy or patch."""

    resource: ClassVar[str] = "basic"

    async def on_create(self) -> DatabaseOutputs:
        return DatabaseOutputs(connection_url="postgres://localhost/basic")

    async def on_update(self, previous_config: DatabaseConfig) -> DatabaseOutputs:
        return self.outputs  # type: ignore[return-value]

    async def on_delete(self) -> None:
        pass


# --- supports_copy / supports_patch detection ---


def test_supports_copy_returns_true_when_overridden():
    assert CopyableDatabase.supports_copy() is True


def test_supports_copy_returns_false_when_not_overridden():
    assert BasicResource.supports_copy() is False


def test_supports_patch_returns_true_when_overridden():
    assert CopyableDatabase.supports_patch() is True


def test_supports_patch_returns_false_when_not_overridden():
    assert BasicResource.supports_patch() is False


def test_base_resource_does_not_support_copy():
    assert Resource.supports_copy() is False


def test_base_resource_does_not_support_patch():
    assert Resource.supports_patch() is False


# --- on_copy default raises NotImplementedError ---


async def test_on_copy_raises_not_implemented_on_base_resource():
    resource = BasicResource(
        name="test",
        config=DatabaseConfig(name="test"),
    )
    context = CopyContext(target_name="test-copy", tags=["env:test"])

    with pytest.raises(NotImplementedError, match="does not support on_copy"):
        await resource.on_copy(context)


async def test_on_patch_raises_not_implemented_on_base_resource():
    resource = BasicResource(
        name="test",
        config=DatabaseConfig(name="test"),
    )
    patch = PatchDefinition(patch_id="p-1", payload={"action": "noop"})

    with pytest.raises(NotImplementedError, match="does not support on_patch"):
        await resource.on_patch(patch)


# --- on_copy lifecycle ---


async def test_on_copy_returns_copy_result_with_stateless_strategy():
    resource = CopyableDatabase(
        name="source-db",
        config=DatabaseConfig(name="source-db", size_gb=20),
        outputs=DatabaseOutputs(connection_url="postgres://localhost/source-db"),
    )
    context = CopyContext(
        target_name="copy-db",
        tags=["env:feature-348"],
        strategy=CopyStrategy.STATELESS,
    )

    result = await resource.on_copy(context)

    assert isinstance(result, CopyResult)
    assert result.config["name"] == "copy-db"
    assert result.config["size_gb"] == 20
    assert result.outputs is not None
    assert result.outputs["connection_url"] == "postgres://localhost/copy-db"
    assert result.tags == ["env:feature-348"]


async def test_on_copy_returns_copy_result_with_stateful_strategy():
    resource = CopyableDatabase(
        name="source-db",
        config=DatabaseConfig(name="source-db"),
        outputs=DatabaseOutputs(connection_url="postgres://localhost/source-db"),
    )
    context = CopyContext(
        target_name="copy-db",
        strategy=CopyStrategy.STATEFUL,
    )

    result = await resource.on_copy(context)

    assert isinstance(result, CopyResult)
    assert result.config["name"] == "copy-db"
    assert result.outputs is None


# --- on_patch lifecycle ---


async def test_on_patch_succeeds_with_known_action():
    resource = CopyableDatabase(
        name="my-db",
        config=DatabaseConfig(name="my-db", version="15"),
        outputs=DatabaseOutputs(connection_url="postgres://localhost/my-db"),
    )
    patch = PatchDefinition(
        patch_id="upgrade-pg-16",
        description="Upgrade PostgreSQL to version 16",
        constraints=[
            CompatibilityConstraint(field="version", operator=">=", value="14"),
        ],
        payload={"action": "upgrade_version", "target_version": "16"},
    )

    result = await resource.on_patch(patch)

    assert isinstance(result, PatchResult)
    assert result.success is True
    assert result.message == "Upgraded to version 16"
    assert result.modified_config == {"version": "16"}


async def test_on_patch_fails_with_unknown_action():
    resource = CopyableDatabase(
        name="my-db",
        config=DatabaseConfig(name="my-db"),
    )
    patch = PatchDefinition(
        patch_id="unknown-patch",
        payload={"action": "unknown"},
    )

    result = await resource.on_patch(patch)

    assert result.success is False
    assert "Unknown patch action" in result.message


# --- Harness invoke_copy ---


async def test_harness_invoke_copy_succeeds(harness: ProviderHarness):
    context = CopyContext(
        target_name="copy-db",
        tags=["env:feature-123"],
        strategy=CopyStrategy.STATELESS,
    )

    result = await harness.invoke_copy(
        CopyableDatabase,
        name="source-db",
        config=DatabaseConfig(name="source-db", size_gb=50),
        context=context,
        current_outputs=DatabaseOutputs(connection_url="postgres://localhost/source-db"),
    )

    assert result.success
    assert result.copy_result is not None
    assert result.copy_result.config["name"] == "copy-db"
    assert result.copy_result.config["size_gb"] == 50
    assert result.error is None


async def test_harness_invoke_copy_fails_on_unsupported_resource(harness: ProviderHarness):
    context = CopyContext(target_name="copy-basic")

    result = await harness.invoke_copy(
        BasicResource,
        name="source",
        config=DatabaseConfig(name="source"),
        context=context,
    )

    assert result.failed
    assert isinstance(result.error, NotImplementedError)


async def test_harness_invoke_copy_records_event(harness: ProviderHarness):
    context = CopyContext(target_name="copy-db")

    await harness.invoke_copy(
        CopyableDatabase,
        name="source-db",
        config=DatabaseConfig(name="source-db"),
        context=context,
    )

    assert len(harness.events) == 1
    assert harness.events[0].event_type == "copy"


# --- Harness invoke_patch ---


async def test_harness_invoke_patch_succeeds(harness: ProviderHarness):
    patch = PatchDefinition(
        patch_id="upgrade-pg-16",
        payload={"action": "upgrade_version", "target_version": "16"},
    )

    result = await harness.invoke_patch(
        CopyableDatabase,
        name="my-db",
        config=DatabaseConfig(name="my-db", version="15"),
        patch=patch,
        current_outputs=DatabaseOutputs(connection_url="postgres://localhost/my-db"),
    )

    assert result.success
    assert result.patch_result is not None
    assert result.patch_result.success is True
    assert result.patch_result.modified_config == {"version": "16"}
    assert result.error is None


async def test_harness_invoke_patch_fails_on_unsupported_resource(harness: ProviderHarness):
    patch = PatchDefinition(patch_id="some-patch", payload={})

    result = await harness.invoke_patch(
        BasicResource,
        name="basic",
        config=DatabaseConfig(name="basic"),
        patch=patch,
    )

    assert result.failed
    assert isinstance(result.error, NotImplementedError)


async def test_harness_invoke_patch_records_event(harness: ProviderHarness):
    patch = PatchDefinition(
        patch_id="test-patch",
        payload={"action": "upgrade_version", "target_version": "17"},
    )

    await harness.invoke_patch(
        CopyableDatabase,
        name="my-db",
        config=DatabaseConfig(name="my-db"),
        patch=patch,
    )

    assert len(harness.events) == 1
    assert harness.events[0].event_type == "patch"


# --- CopyContext model validation ---


def test_copy_context_defaults():
    ctx = CopyContext(target_name="new-resource")

    assert ctx.tags == []
    assert ctx.strategy == CopyStrategy.STATELESS
    assert ctx.metadata == {}


def test_copy_context_rejects_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        CopyContext(target_name="x", unknown_field="y")


# --- PatchDefinition model validation ---


def test_patch_definition_defaults():
    patch = PatchDefinition(patch_id="p-1")

    assert patch.description == ""
    assert patch.constraints == []
    assert patch.payload == {}
    assert patch.metadata == {}


def test_patch_definition_rejects_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        PatchDefinition(patch_id="p-1", unknown="value")


# --- CopyResult model validation ---


def test_copy_result_minimal():
    result = CopyResult(config={"name": "test"})

    assert result.config == {"name": "test"}
    assert result.outputs is None
    assert result.tags is None


# --- PatchResult model validation ---


def test_patch_result_minimal():
    result = PatchResult(success=True)

    assert result.success is True
    assert result.message == ""
    assert result.modified_config is None
    assert result.modified_outputs is None


# --- CompatibilityConstraint model validation ---


def test_compatibility_constraint_creation():
    constraint = CompatibilityConstraint(
        field="postgresql_version",
        operator=">=",
        value="14",
    )

    assert constraint.field == "postgresql_version"
    assert constraint.operator == ">="
    assert constraint.value == "14"


@pytest.mark.parametrize("operator", ["==", "!=", ">", ">=", "<", "<=", "in"])
def test_compatibility_constraint_valid_operators(operator: str):
    constraint = CompatibilityConstraint(
        field="version",
        operator=operator,
        value="1",
    )

    assert constraint.operator == operator
