"""Tests for SDK core models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from pragma_sdk import Config, Dependency, Field, FieldReference, LifecycleState
from pragma_sdk.models import (
    BuildInfo,
    BuildStatus,
    DeploymentResult,
    DeploymentStatus,
    OwnerReference,
    PushResult,
    ResourceReference,
    format_resource_id,
    is_dependency_marker,
)


if TYPE_CHECKING:
    from conftest import StubResource

from conftest import StubConfig


def test_lifecycle_state_values() -> None:
    """LifecycleState enum has all 5 states."""
    assert LifecycleState.DRAFT == "draft"
    assert LifecycleState.PENDING == "pending"
    assert LifecycleState.PROCESSING == "processing"
    assert LifecycleState.READY == "ready"
    assert LifecycleState.FAILED == "failed"


def test_format_resource_id() -> None:
    """format_resource_id creates format ID."""
    result = format_resource_id("postgres", "database", "my-db")
    assert result == "resource:postgres_database_my-db"


def test_resource_reference_id_property() -> None:
    """ResourceReference.id returns formatted resource ID."""
    ref = ResourceReference(provider="postgres", resource="database", name="my-db")
    assert ref.id == "resource:postgres_database_my-db"


def test_field_reference_extends_resource_reference() -> None:
    """FieldReference has field attribute on top of ResourceReference."""
    ref = FieldReference(
        provider="postgres",
        resource="database",
        name="my-db",
        field="outputs.connection_url",
    )
    assert ref.provider == "postgres"
    assert ref.resource == "database"
    assert ref.name == "my-db"
    assert ref.field == "outputs.connection_url"
    assert ref.id == "resource:postgres_database_my-db"


# --- OwnerReference tests ---


def test_owner_reference_initialization() -> None:
    """OwnerReference accepts provider, resource, name fields."""
    ref = OwnerReference(provider="app", resource="server", name="my-server")
    assert ref.provider == "app"
    assert ref.resource == "server"
    assert ref.name == "my-server"


def test_owner_reference_id_property() -> None:
    """OwnerReference.id returns formatted resource ID."""
    ref = OwnerReference(provider="app", resource="server", name="my-server")
    assert ref.id == "resource:app_server_my-server"


def test_owner_reference_validation_requires_all_fields() -> None:
    """OwnerReference requires provider, resource, and name."""
    with pytest.raises(ValidationError):
        OwnerReference(provider="app")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        OwnerReference(provider="app", resource="server")  # type: ignore[call-arg]


def test_owner_reference_equality() -> None:
    """OwnerReference instances with same fields are equal."""
    ref1 = OwnerReference(provider="app", resource="server", name="my-server")
    ref2 = OwnerReference(provider="app", resource="server", name="my-server")
    ref3 = OwnerReference(provider="app", resource="server", name="other-server")

    assert ref1 == ref2
    assert ref1 != ref3


def test_owner_reference_not_equal_to_resource_reference() -> None:
    """OwnerReference and ResourceReference are distinct types."""
    owner_ref = OwnerReference(provider="app", resource="server", name="my-server")
    resource_ref = ResourceReference(provider="app", resource="server", name="my-server")

    # Different types even with same data
    assert type(owner_ref) is not type(resource_ref)
    # But they have the same id since both use format_resource_id
    assert owner_ref.id == resource_ref.id


def test_config_forbids_extra_fields() -> None:
    """Config subclasses reject extra fields."""
    with pytest.raises(ValidationError):
        StubConfig(name="test", size=10, unknown_field="bad")


def test_resource_id_property(stub_resource: StubResource) -> None:
    """Resource.id returns formatted resource ID."""
    assert stub_resource.id == "resource:test_stub_my-resource"


def test_resource_default_lifecycle_state(stub_resource: StubResource) -> None:
    """Resource defaults to DRAFT lifecycle state."""
    assert stub_resource.lifecycle_state == LifecycleState.DRAFT


def test_resource_with_field_reference_in_config() -> None:
    """Config field can be a FieldReference instead of direct value."""
    ref = FieldReference(
        provider="postgres",
        resource="database",
        name="my-db",
        field="outputs.connection_url",
    )

    class AppConfig(Config):
        """Test config."""

        name: Field[str]
        database_url: Field[str]

    config1 = AppConfig(name="app", database_url="postgres://localhost")
    assert config1.database_url == "postgres://localhost"

    config2 = AppConfig(name="app", database_url=ref)
    assert isinstance(config2.database_url, FieldReference)
    assert config2.database_url.field == "outputs.connection_url"


def test_build_status_values() -> None:
    """BuildStatus enum has all 4 states."""
    assert BuildStatus.PENDING == "pending"
    assert BuildStatus.BUILDING == "building"
    assert BuildStatus.SUCCESS == "success"
    assert BuildStatus.FAILED == "failed"


def test_deployment_status_values() -> None:
    """DeploymentStatus enum has all 4 states."""
    assert DeploymentStatus.PENDING == "pending"
    assert DeploymentStatus.PROGRESSING == "progressing"
    assert DeploymentStatus.AVAILABLE == "available"
    assert DeploymentStatus.FAILED == "failed"


def test_push_result_model() -> None:
    """PushResult stores build initiation info."""
    result = PushResult(
        version="20250115.120000",
        status=BuildStatus.PENDING,
        message="Build started",
    )
    assert result.version == "20250115.120000"
    assert result.status == BuildStatus.PENDING
    assert result.message == "Build started"


def test_build_info_success() -> None:
    """BuildInfo stores successful build info."""
    from datetime import UTC, datetime

    result = BuildInfo(
        provider_id="test-provider",
        version="20250115.120000",
        status=BuildStatus.SUCCESS,
        created_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
    )
    assert result.provider_id == "test-provider"
    assert result.version == "20250115.120000"
    assert result.status == BuildStatus.SUCCESS
    assert result.error_message is None


def test_build_info_failure() -> None:
    """BuildInfo stores failed build info."""
    from datetime import UTC, datetime

    result = BuildInfo(
        provider_id="test-provider",
        version="20250115.120000",
        status=BuildStatus.FAILED,
        error_message="Dockerfile syntax error",
        created_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
    )
    assert result.provider_id == "test-provider"
    assert result.version == "20250115.120000"
    assert result.status == BuildStatus.FAILED
    assert result.error_message == "Dockerfile syntax error"


def test_deployment_result_available() -> None:
    """DeploymentResult stores available deployment info."""
    result = DeploymentResult(
        deployment_name="provider-my-provider",
        status=DeploymentStatus.AVAILABLE,
        available_replicas=1,
        ready_replicas=1,
    )
    assert result.status == DeploymentStatus.AVAILABLE
    assert result.available_replicas == 1
    assert result.ready_replicas == 1
    assert result.message is None


def test_deployment_result_progressing() -> None:
    """DeploymentResult stores progressing deployment info."""
    result = DeploymentResult(
        deployment_name="provider-my-provider",
        status=DeploymentStatus.PROGRESSING,
        available_replicas=0,
        ready_replicas=0,
        message="Deployment in progress",
    )
    assert result.status == DeploymentStatus.PROGRESSING
    assert result.message == "Deployment in progress"


# --- Dependency tests ---


def test_dependency_fields() -> None:
    """Dependency has provider, resource, name fields."""
    from conftest import StubResource

    dep = Dependency[StubResource](
        provider="postgres",
        resource="database",
        name="my-db",
    )
    assert dep.provider == "postgres"
    assert dep.resource == "database"
    assert dep.name == "my-db"


def test_dependency_id_property() -> None:
    """Dependency.id returns formatted resource ID."""
    from conftest import StubResource

    dep = Dependency[StubResource](
        provider="postgres",
        resource="database",
        name="my-db",
    )
    assert dep.id == "resource:postgres_database_my-db"


def test_dependency_serialization_includes_marker() -> None:
    """Dependency serialization includes __dependency__ marker."""
    from conftest import StubResource

    dep = Dependency[StubResource](
        provider="postgres",
        resource="database",
        name="my-db",
    )
    data = dep.model_dump(by_alias=True)
    assert data["__dependency__"] is True
    assert data["provider"] == "postgres"
    assert data["resource"] == "database"
    assert data["name"] == "my-db"


def test_dependency_type_extractable_at_runtime() -> None:
    """Type parameter T is extractable at runtime via __pydantic_generic_metadata__."""
    from conftest import StubResource

    # Create a parameterized type
    dep_type = Dependency[StubResource]

    # Extract the type argument via Pydantic's generic metadata
    # This is how the runtime will extract the type to instantiate the correct Resource subclass
    metadata = getattr(dep_type, "__pydantic_generic_metadata__", None)
    assert metadata is not None
    assert "args" in metadata
    assert len(metadata["args"]) == 1
    assert metadata["args"][0] is StubResource

    # Also verify it works on an instance's type
    dep = Dependency[StubResource](
        provider="test",
        resource="stub",
        name="my-db",
    )
    instance_metadata = getattr(type(dep), "__pydantic_generic_metadata__", None)
    assert instance_metadata is not None
    assert instance_metadata["args"][0] is StubResource


@pytest.mark.anyio
async def test_dependency_resolve_returns_cached_value() -> None:
    """resolve() returns cached value when _resolved is populated."""
    from conftest import StubConfig, StubOutputs, StubResource

    # Create a resolved resource
    config = StubConfig(name="my-db")
    resource = StubResource(
        name="my-db",
        config=config,
        outputs=StubOutputs(url="https://my-db.example.com"),
    )

    # Create dependency and populate _resolved
    dep = Dependency[StubResource](
        provider="test",
        resource="stub",
        name="my-db",
    )
    dep._resolved = resource

    # resolve() should return the cached value
    resolved = await dep.resolve()
    assert resolved is resource
    assert resolved.outputs is not None
    assert resolved.outputs.url == "https://my-db.example.com"


@pytest.mark.anyio
async def test_dependency_resolve_raises_when_not_resolved() -> None:
    """resolve() raises RuntimeError when _resolved is None."""
    from conftest import StubResource

    dep = Dependency[StubResource](
        provider="postgres",
        resource="database",
        name="my-db",
    )

    with pytest.raises(RuntimeError, match="Dependency.*not resolved"):
        await dep.resolve()


def test_dependency_in_config() -> None:
    """Dependency can be used as a field in Config."""
    from conftest import StubResource

    class AppConfig(Config):
        """Test config."""

        database: Dependency[StubResource]

    dep = Dependency[StubResource](
        provider="test",
        resource="stub",
        name="my-db",
    )
    config = AppConfig(database=dep)

    assert isinstance(config.database, Dependency)
    assert config.database.name == "my-db"
    assert config.database.id == "resource:test_stub_my-db"


# --- is_dependency_marker tests ---


def test_is_dependency_marker_valid() -> None:
    """is_dependency_marker returns True for valid dependency marker."""
    marker = {
        "__dependency__": True,
        "provider": "test",
        "resource": "database",
        "name": "my-db",
    }
    assert is_dependency_marker(marker) is True


def test_is_dependency_marker_with_extra_keys() -> None:
    """is_dependency_marker returns True even with extra keys like 'ref'."""
    marker = {
        "__dependency__": True,
        "provider": "test",
        "resource": "database",
        "name": "my-db",
        "ref": {"some": "data"},  # Added after resolution
    }
    assert is_dependency_marker(marker) is True


def test_is_dependency_marker_false_marker() -> None:
    """is_dependency_marker returns False when __dependency__ is False."""
    marker = {
        "__dependency__": False,
        "provider": "test",
        "resource": "database",
        "name": "my-db",
    }
    assert is_dependency_marker(marker) is False


def test_is_dependency_marker_missing_keys() -> None:
    """is_dependency_marker returns False when required keys are missing."""
    marker = {
        "__dependency__": True,
        "provider": "test",
    }
    assert is_dependency_marker(marker) is False


def test_is_dependency_marker_not_dict() -> None:
    """is_dependency_marker returns False for non-dict values."""
    assert is_dependency_marker("not a dict") is False
    assert is_dependency_marker(None) is False
    assert is_dependency_marker(123) is False
    assert is_dependency_marker([]) is False


def test_is_field_ref_marker_valid() -> None:
    """is_field_ref_marker returns True for valid __field_ref__ markers."""
    from pragma_sdk.models import is_field_ref_marker

    marker = {
        "__field_ref__": True,
        "ref": {
            "provider": "postgres",
            "resource": "database",
            "name": "prod-db",
            "field": "outputs.connection_url",
        },
        "resolved_value": "postgres://localhost/db",
    }
    assert is_field_ref_marker(marker) is True


def test_is_field_ref_marker_missing_keys() -> None:
    """is_field_ref_marker returns False when required keys are missing."""
    from pragma_sdk.models import is_field_ref_marker

    # Missing resolved_value
    assert is_field_ref_marker({"__field_ref__": True, "ref": {}}) is False
    # Missing ref
    assert is_field_ref_marker({"__field_ref__": True, "resolved_value": "x"}) is False
    # Missing __field_ref__
    assert is_field_ref_marker({"ref": {}, "resolved_value": "x"}) is False


def test_is_field_ref_marker_false_flag() -> None:
    """is_field_ref_marker returns False when __field_ref__ is not True."""
    from pragma_sdk.models import is_field_ref_marker

    marker = {"__field_ref__": False, "ref": {}, "resolved_value": "x"}
    assert is_field_ref_marker(marker) is False


def test_is_field_ref_marker_not_dict() -> None:
    """is_field_ref_marker returns False for non-dict values."""
    from pragma_sdk.models import is_field_ref_marker

    assert is_field_ref_marker("not a dict") is False
    assert is_field_ref_marker(None) is False
    assert is_field_ref_marker(123) is False
    assert is_field_ref_marker([]) is False


@pytest.mark.anyio
async def test_dependency_resolve_idempotent() -> None:
    """Multiple resolve() calls return same instance."""
    from conftest import StubConfig, StubOutputs, StubResource

    config = StubConfig(name="my-db")
    resource = StubResource(
        name="my-db",
        config=config,
        outputs=StubOutputs(url="https://my-db.example.com"),
    )

    dep = Dependency[StubResource](
        provider="test",
        resource="stub",
        name="my-db",
    )
    dep._resolved = resource

    first = await dep.resolve()
    second = await dep.resolve()
    third = await dep.resolve()

    assert first is second is third is resource


def test_dependency_serialization_excludes_resolved() -> None:
    """Serialization excludes _resolved private attribute."""
    from conftest import StubConfig, StubOutputs, StubResource

    config = StubConfig(name="my-db")
    resource = StubResource(
        name="my-db",
        config=config,
        outputs=StubOutputs(url="https://my-db.example.com"),
    )

    dep = Dependency[StubResource](
        provider="test",
        resource="stub",
        name="my-db",
    )
    dep._resolved = resource

    data = dep.model_dump(by_alias=True)
    assert "_resolved" not in data
    assert "resolved" not in data
    assert data == {
        "__dependency__": True,
        "provider": "test",
        "resource": "stub",
        "name": "my-db",
    }


# ==================== Resource.upgrade() / downgrade() Tests ====================


def test_resource_default_upgrade_returns_inputs_unchanged() -> None:
    """Default upgrade() returns config and outputs unchanged."""
    from conftest import StubResource

    config = {"name": "my-resource", "size": 10}
    outputs = {"url": "https://example.com"}

    result_config, result_outputs = StubResource.upgrade(config, outputs)

    assert result_config is config
    assert result_outputs is outputs


def test_resource_default_downgrade_returns_inputs_unchanged() -> None:
    """Default downgrade() returns config and outputs unchanged."""
    from conftest import StubResource

    config = {"name": "my-resource", "size": 10}
    outputs = {"url": "https://example.com"}

    result_config, result_outputs = StubResource.downgrade(config, outputs)

    assert result_config is config
    assert result_outputs is outputs


def test_resource_custom_upgrade_transforms_data() -> None:
    """Custom upgrade() override transforms config and outputs."""
    from typing import ClassVar

    from conftest import StubConfig, StubOutputs

    from pragma_sdk import Resource

    class V2Resource(Resource[StubConfig, StubOutputs]):
        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "v2"

        async def on_create(self) -> StubOutputs:
            return StubOutputs(url="")

        async def on_update(self, previous_config: StubConfig) -> StubOutputs:
            return StubOutputs(url="")

        async def on_delete(self) -> None:
            pass

        @classmethod
        def upgrade(cls, config: dict, outputs: dict) -> tuple[dict, dict]:
            config["new_field"] = "default_value"
            outputs["extra"] = True
            return config, outputs

        @classmethod
        def downgrade(cls, config: dict, outputs: dict) -> tuple[dict, dict]:
            config.pop("new_field", None)
            outputs.pop("extra", None)
            return config, outputs

    config = {"name": "my-resource"}
    outputs = {"url": "https://example.com"}

    upgraded_config, upgraded_outputs = V2Resource.upgrade(config, outputs)
    assert upgraded_config["new_field"] == "default_value"
    assert upgraded_outputs["extra"] is True

    downgraded_config, downgraded_outputs = V2Resource.downgrade(upgraded_config, upgraded_outputs)
    assert "new_field" not in downgraded_config
    assert "extra" not in downgraded_outputs


# ==================== Resource.set_owner() Tests ====================


def test_set_owner_adds_owner_reference(stub_resource: StubResource) -> None:
    """set_owner() adds owner reference to the resource."""
    from conftest import StubConfig, StubResource

    owner = StubResource(name="parent-resource", config=StubConfig(name="parent"))

    assert len(stub_resource.owner_references) == 0

    stub_resource.set_owner(owner)

    assert len(stub_resource.owner_references) == 1
    ref = stub_resource.owner_references[0]
    assert ref.provider == "test"
    assert ref.resource == "stub"
    assert ref.name == "parent-resource"


def test_set_owner_prevents_duplicates(stub_resource: StubResource) -> None:
    """set_owner() does not add duplicate owner references."""
    from conftest import StubConfig, StubResource

    owner = StubResource(name="parent-resource", config=StubConfig(name="parent"))

    stub_resource.set_owner(owner)
    stub_resource.set_owner(owner)
    stub_resource.set_owner(owner)

    assert len(stub_resource.owner_references) == 1


def test_set_owner_returns_self_for_chaining(stub_resource: StubResource) -> None:
    """set_owner() returns self for method chaining."""
    from conftest import StubConfig, StubResource

    owner = StubResource(name="parent-resource", config=StubConfig(name="parent"))

    result = stub_resource.set_owner(owner)

    assert result is stub_resource


def test_set_owner_allows_multiple_owners(stub_resource: StubResource) -> None:
    """set_owner() allows multiple distinct owners."""
    from conftest import StubConfig, StubResource

    owner1 = StubResource(name="parent-1", config=StubConfig(name="p1"))
    owner2 = StubResource(name="parent-2", config=StubConfig(name="p2"))

    stub_resource.set_owner(owner1).set_owner(owner2)

    assert len(stub_resource.owner_references) == 2
    assert stub_resource.owner_references[0].name == "parent-1"
    assert stub_resource.owner_references[1].name == "parent-2"


def test_set_owner_creates_correct_owner_reference_type(stub_resource: StubResource) -> None:
    """set_owner() creates OwnerReference, not ResourceReference."""
    from conftest import StubConfig, StubResource

    owner = StubResource(name="parent-resource", config=StubConfig(name="parent"))
    stub_resource.set_owner(owner)

    ref = stub_resource.owner_references[0]
    assert isinstance(ref, OwnerReference)


# ==================== Resource.apply() Tests ====================


class MockRuntimeContextForApply:
    """Mock runtime context for testing apply()."""

    def __init__(
        self,
        raise_exception: Exception | None = None,
    ):
        self.raise_exception = raise_exception
        self.apply_calls: list[dict[str, Any]] = []

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        self.apply_calls.append(resource_data)
        if self.raise_exception:
            raise self.raise_exception

    async def wait_for_state(
        self,
        resource_id: str,
        target_state: LifecycleState,
        timeout: float,
    ) -> dict[str, Any]:
        return {"lifecycle_state": "ready"}


@pytest.mark.asyncio
async def test_apply_raises_without_context(stub_resource: StubResource) -> None:
    """apply() raises RuntimeError when called without runtime context."""
    with pytest.raises(RuntimeError, match="must be called from within a provider lifecycle handler"):
        await stub_resource.apply()


@pytest.mark.asyncio
async def test_apply_delegates_to_context(stub_resource: StubResource) -> None:
    """apply() delegates to apply_resource with serialized resource data."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        await stub_resource.apply()

        assert len(ctx.apply_calls) == 1
        data = ctx.apply_calls[0]
        assert data["provider"] == "test"
        assert data["resource"] == "stub"
        assert data["name"] == "my-resource"
        assert "config" in data
        assert data["owner_references"] == []
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_returns_self_for_chaining(stub_resource: StubResource) -> None:
    """apply() returns self for method chaining."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        result = await stub_resource.apply()
        assert result is stub_resource
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_sets_lifecycle_state_to_pending(stub_resource: StubResource) -> None:
    """apply() sets lifecycle_state to PENDING."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        assert stub_resource.lifecycle_state == LifecycleState.DRAFT
        await stub_resource.apply()
        assert stub_resource.lifecycle_state == LifecycleState.PENDING
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_includes_owner_references(stub_resource: StubResource) -> None:
    """apply() includes owner_references in serialized data."""
    from conftest import StubConfig, StubResource

    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    owner = StubResource(name="parent-resource", config=StubConfig(name="parent"))
    stub_resource.set_owner(owner)

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        await stub_resource.apply()

        data = ctx.apply_calls[0]
        assert len(data["owner_references"]) == 1
        assert data["owner_references"][0]["provider"] == "test"
        assert data["owner_references"][0]["resource"] == "stub"
        assert data["owner_references"][0]["name"] == "parent-resource"
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_includes_tags_when_present(stub_resource: StubResource) -> None:
    """apply() includes tags in serialized data when present."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    stub_resource.tags = ["production", "critical"]

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        await stub_resource.apply()

        data = ctx.apply_calls[0]
        assert data["tags"] == ["production", "critical"]
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_propagates_runtime_error(stub_resource: StubResource) -> None:
    """apply() propagates RuntimeError from context."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForApply(raise_exception=RuntimeError("Failed to apply resource"))
    token = set_runtime_context(ctx)
    try:
        with pytest.raises(RuntimeError, match="Failed to apply resource"):
            await stub_resource.apply()
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_apply_auto_sets_owner_from_context(stub_resource: StubResource) -> None:
    """apply() automatically sets owner from current resource context."""
    from pragma_sdk.context import (
        reset_current_resource_owner,
        reset_runtime_context,
        set_current_resource_owner,
        set_runtime_context,
    )

    parent_owner = OwnerReference(provider="test", resource="parent", name="my-parent")

    ctx = MockRuntimeContextForApply()
    runtime_token = set_runtime_context(ctx)
    owner_token = set_current_resource_owner(parent_owner)
    try:
        assert len(stub_resource.owner_references) == 0

        await stub_resource.apply()

        assert len(stub_resource.owner_references) == 1
        assert stub_resource.owner_references[0] == parent_owner

        data = ctx.apply_calls[0]
        assert len(data["owner_references"]) == 1
        assert data["owner_references"][0]["provider"] == "test"
        assert data["owner_references"][0]["resource"] == "parent"
        assert data["owner_references"][0]["name"] == "my-parent"
    finally:
        reset_current_resource_owner(owner_token)
        reset_runtime_context(runtime_token)


@pytest.mark.asyncio
async def test_apply_does_not_duplicate_owner_from_context(stub_resource: StubResource) -> None:
    """apply() does not add duplicate owner if already in owner_references."""
    from pragma_sdk.context import (
        reset_current_resource_owner,
        reset_runtime_context,
        set_current_resource_owner,
        set_runtime_context,
    )

    parent_owner = OwnerReference(provider="test", resource="parent", name="my-parent")

    stub_resource.owner_references.append(parent_owner)

    ctx = MockRuntimeContextForApply()
    runtime_token = set_runtime_context(ctx)
    owner_token = set_current_resource_owner(parent_owner)
    try:
        assert len(stub_resource.owner_references) == 1

        await stub_resource.apply()

        assert len(stub_resource.owner_references) == 1

        data = ctx.apply_calls[0]
        assert len(data["owner_references"]) == 1
    finally:
        reset_current_resource_owner(owner_token)
        reset_runtime_context(runtime_token)


@pytest.mark.asyncio
async def test_apply_without_owner_context_does_not_add_owner(stub_resource: StubResource) -> None:
    """apply() does not add owner when no current resource context is set."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForApply()
    token = set_runtime_context(ctx)
    try:
        assert len(stub_resource.owner_references) == 0

        await stub_resource.apply()

        assert len(stub_resource.owner_references) == 0

        data = ctx.apply_calls[0]
        assert data["owner_references"] == []
    finally:
        reset_runtime_context(token)


# ==================== Resource.wait_ready() Tests ====================


class MockRuntimeContextForWaitReady:
    """Mock runtime context for testing wait_ready()."""

    def __init__(
        self,
        return_value: dict[str, Any] | None = None,
        raise_exception: Exception | None = None,
    ):
        self.return_value = return_value or {"lifecycle_state": "ready"}
        self.raise_exception = raise_exception
        self.wait_calls: list[tuple[str, LifecycleState, float]] = []

    async def wait_for_state(
        self,
        resource_id: str,
        target_state: LifecycleState,
        timeout: float,
    ) -> dict[str, Any]:
        self.wait_calls.append((resource_id, target_state, timeout))
        if self.raise_exception:
            raise self.raise_exception
        return self.return_value

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        pass  # Not used in wait_ready tests


@pytest.mark.asyncio
async def test_wait_ready_raises_without_context(stub_resource: StubResource) -> None:
    """wait_ready() raises RuntimeError when called without runtime context."""
    with pytest.raises(RuntimeError, match="must be called from within a provider lifecycle handler"):
        await stub_resource.wait_ready()


@pytest.mark.asyncio
async def test_wait_ready_delegates_to_context(stub_resource: StubResource) -> None:
    """wait_ready() delegates to wait_for_resource_state with correct arguments."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForWaitReady({"lifecycle_state": "ready", "outputs": {"url": "http://test"}})
    token = set_runtime_context(ctx)
    try:
        await stub_resource.wait_ready(timeout=30.0)

        assert len(ctx.wait_calls) == 1
        assert ctx.wait_calls[0] == (stub_resource.id, LifecycleState.READY, 30.0)
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_ready_uses_default_timeout(stub_resource: StubResource) -> None:
    """wait_ready() uses default timeout of 60.0."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForWaitReady()
    token = set_runtime_context(ctx)
    try:
        await stub_resource.wait_ready()
        assert ctx.wait_calls[0][2] == 60.0
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_ready_updates_lifecycle_state(stub_resource: StubResource) -> None:
    """wait_ready() updates resource lifecycle_state from response."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForWaitReady({"lifecycle_state": "ready"})
    token = set_runtime_context(ctx)
    try:
        assert stub_resource.lifecycle_state == LifecycleState.DRAFT

        result = await stub_resource.wait_ready()

        assert stub_resource.lifecycle_state == LifecycleState.READY
        assert result is stub_resource
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_ready_updates_outputs(stub_resource: StubResource) -> None:
    """wait_ready() updates resource outputs from response."""
    from pragma_sdk import Outputs
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForWaitReady({
        "lifecycle_state": "ready",
        "outputs": {"url": "http://updated-url.com"},
    })
    token = set_runtime_context(ctx)
    try:
        assert stub_resource.outputs is None

        await stub_resource.wait_ready()

        assert stub_resource.outputs is not None
        # Check it's an Outputs subclass with correct data (avoid import path issues)
        assert isinstance(stub_resource.outputs, Outputs)
        assert stub_resource.outputs.__class__.__name__ == "StubOutputs"
        assert stub_resource.outputs.url == "http://updated-url.com"
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_ready_propagates_timeout_error(stub_resource: StubResource) -> None:
    """wait_ready() propagates TimeoutError from context."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context

    ctx = MockRuntimeContextForWaitReady(raise_exception=TimeoutError("Resource not ready within timeout"))
    token = set_runtime_context(ctx)
    try:
        with pytest.raises(TimeoutError, match="Resource not ready within timeout"):
            await stub_resource.wait_ready()
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_ready_propagates_resource_failed_error(stub_resource: StubResource) -> None:
    """wait_ready() propagates ResourceFailedError from context."""
    from pragma_sdk.context import reset_runtime_context, set_runtime_context
    from pragma_sdk.exceptions import ResourceFailedError

    ctx = MockRuntimeContextForWaitReady(
        raise_exception=ResourceFailedError("resource:test_stub_test", "Database connection failed")
    )
    token = set_runtime_context(ctx)
    try:
        with pytest.raises(ResourceFailedError, match="Database connection failed"):
            await stub_resource.wait_ready()
    finally:
        reset_runtime_context(token)
