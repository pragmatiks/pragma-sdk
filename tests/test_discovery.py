"""Tests for resource discovery."""

from __future__ import annotations

from types import ModuleType
from typing import ClassVar

import pytest
from pytest_mock import MockerFixture

from pragma_sdk import Config, Field, Outputs, Provider, Resource
from pragma_sdk.provider.discovery import discover_resources, is_registered_resource
from pragma_sdk.provider.provider import RESOURCE_MARKER


class UnregisteredConfig(Config):
    """Config for unregistered resource."""

    value: Field[str]


class UnregisteredOutputs(Outputs):
    """Outputs for unregistered resource."""

    result: str


class UnregisteredResource(Resource[UnregisteredConfig, UnregisteredOutputs]):
    """Resource subclass without @provider.resource() decorator."""

    resource: ClassVar[str] = "unregistered"

    async def on_create(self) -> UnregisteredOutputs:
        return UnregisteredOutputs(result="created")

    async def on_update(self, previous_config: UnregisteredConfig) -> UnregisteredOutputs:
        return UnregisteredOutputs(result="updated")

    async def on_delete(self) -> None:
        pass


test_provider = Provider()


@test_provider.resource("registered")
class RegisteredResource(Resource[UnregisteredConfig, UnregisteredOutputs]):
    """Resource decorated with @provider.resource()."""

    async def on_create(self) -> UnregisteredOutputs:
        return UnregisteredOutputs(result="created")

    async def on_update(self, previous_config: UnregisteredConfig) -> UnregisteredOutputs:
        return UnregisteredOutputs(result="updated")

    async def on_delete(self) -> None:
        pass


def test_is_registered_resource_returns_true_for_decorated_resource() -> None:
    """Returns True for Resource subclass decorated with @provider.resource()."""
    assert is_registered_resource(RegisteredResource) is True


def test_is_registered_resource_returns_false_for_undecorated_resource() -> None:
    """Returns False for Resource subclass without @provider.resource() decorator."""
    assert is_registered_resource(UnregisteredResource) is False


def test_is_registered_resource_returns_false_for_base_resource() -> None:
    """Returns False for the base Resource class itself."""
    assert is_registered_resource(Resource) is False


def test_is_registered_resource_returns_false_for_non_resource_class() -> None:
    """Returns False for classes that don't inherit from Resource."""
    assert is_registered_resource(str) is False
    assert is_registered_resource(Config) is False
    assert is_registered_resource(Outputs) is False


def test_is_registered_resource_returns_false_for_non_class() -> None:
    """Returns False for non-class objects."""
    assert is_registered_resource("not a class") is False
    assert is_registered_resource(42) is False
    assert is_registered_resource(None) is False


def test_is_registered_resource_checks_marker_attribute() -> None:
    """Verifies the marker attribute is checked correctly."""
    assert getattr(RegisteredResource, RESOURCE_MARKER, False) is True
    assert getattr(UnregisteredResource, RESOURCE_MARKER, False) is False


def test_discover_resources_raises_import_error_for_nonexistent_package() -> None:
    """Raises ImportError when package doesn't exist."""
    with pytest.raises(ModuleNotFoundError):
        discover_resources("nonexistent_package_that_does_not_exist")


def test_discover_resources_returns_empty_dict_for_package_without_resources() -> None:
    """Returns empty dict for packages with no registered resources."""
    resources = discover_resources("json")
    assert resources == {}


def test_discover_resources_finds_registered_resources(mocker: MockerFixture) -> None:
    """Discovers registered resources in a mocked package."""
    mock_module = ModuleType("fake_provider")
    mock_module.RegisteredResource = RegisteredResource
    mock_module.UnregisteredResource = UnregisteredResource

    mocker.patch("pragma_sdk.provider.discovery.importlib.import_module", return_value=mock_module)

    resources = discover_resources("fake_provider")

    assert len(resources) == 1
    assert "registered" in resources
    assert resources["registered"] is RegisteredResource
