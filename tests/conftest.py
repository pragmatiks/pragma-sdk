"""Shared test fixtures and stub models for pragma_sdk tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from pragma_sdk import Config, Field, Outputs, Resource
from pragma_sdk.context import reset_provider_name, set_provider_name
from pragma_sdk.provider import ProviderHarness


class StubConfig(Config):
    """Stub config for testing."""

    name: Field[str]
    size: Field[int] = 10


class StubOutputs(Outputs):
    """Stub outputs for testing."""

    url: str


class StubResource(Resource[StubConfig, StubOutputs]):
    """Stub resource with working lifecycle methods."""

    resource: ClassVar[str] = "stub"

    async def on_create(self) -> StubOutputs:
        """Create resource."""
        return StubOutputs(url=f"https://{self.config.name}.example.com")

    async def on_update(self, previous_config: StubConfig) -> StubOutputs:
        """Update resource."""
        return StubOutputs(url=f"https://{self.config.name}.example.com/updated")

    async def on_delete(self) -> None:
        """Delete resource."""


class FailingResource(Resource[StubConfig, StubOutputs]):
    """Stub resource that fails all lifecycle operations."""

    resource: ClassVar[str] = "failing"

    async def on_create(self) -> StubOutputs:
        """Fail on create."""
        raise ValueError("Creation failed")

    async def on_update(self, previous_config: StubConfig) -> StubOutputs:
        """Fail on update."""
        raise ValueError("Update failed")

    async def on_delete(self) -> None:
        """Fail on delete."""
        raise ValueError("Deletion failed")


@pytest.fixture(autouse=True)
def provider_context() -> Iterator[None]:
    """Set provider name context for all tests."""
    token = set_provider_name("test")
    yield
    reset_provider_name(token)


TEST_PROJECT_ID = "test-project"


@pytest.fixture
def stub_resource() -> StubResource:
    """StubResource instance for testing resource methods."""
    config = StubConfig(name="my-resource")
    return StubResource(project_id=TEST_PROJECT_ID, name="my-resource", config=config)


@pytest.fixture
def harness() -> ProviderHarness:
    """ProviderHarness instance for testing lifecycle methods."""
    return ProviderHarness(project_id=TEST_PROJECT_ID)


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove auth environment variables to ensure test isolation."""
    monkeypatch.delenv("PRAGMA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PRAGMA_AUTH_TOKEN_DEFAULT", raising=False)
    monkeypatch.delenv("PRAGMA_AUTH_TOKEN_PRODUCTION", raising=False)
    monkeypatch.delenv("PRAGMA_AUTH_TOKEN_STAGING", raising=False)
    monkeypatch.delenv("PRAGMA_CONTEXT", raising=False)
    monkeypatch.delenv("PRAGMA_API_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent")
