"""Tests for Resource description class attribute with docstring fallback."""

from __future__ import annotations

from types import ModuleType
from typing import ClassVar

from pytest_mock import MockerFixture

from pragma_sdk import Config, Field, Outputs, Provider, Resource
from pragma_sdk.provider.extract_schemas import extract_schemas


class DescConfig(Config):
    name: Field[str]


class DescOutputs(Outputs):
    url: str


def test_explicit_description_used() -> None:
    """Resource with explicit description class attribute uses that value."""

    class MyResource(Resource[DescConfig, DescOutputs]):
        """This docstring should be ignored."""

        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "mine"
        description: ClassVar[str | None] = "Manages GKE clusters"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    assert MyResource.description == "Manages GKE clusters"


def test_docstring_fallback_uses_first_line() -> None:
    """Resource without explicit description falls back to first docstring line."""

    class DocResource(Resource[DescConfig, DescOutputs]):
        """Manages PostgreSQL databases.

        This is a longer description that should not be included.
        Only the first line matters.
        """

        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "doc"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    assert DocResource.description == "Manages PostgreSQL databases."


def test_no_description_no_docstring() -> None:
    """Resource with neither description nor docstring has None."""

    class BareResource(Resource[DescConfig, DescOutputs]):
        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "bare"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    assert BareResource.description is None


def test_single_line_docstring_fallback() -> None:
    """Resource with single-line docstring uses it as description."""

    class SingleDoc(Resource[DescConfig, DescOutputs]):
        """Manages Redis caches."""

        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "singledoc"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    assert SingleDoc.description == "Manages Redis caches."


def test_explicit_description_overrides_docstring() -> None:
    """Explicit description takes precedence over docstring."""

    class OverrideResource(Resource[DescConfig, DescOutputs]):
        """This is the docstring."""

        provider: ClassVar[str] = "test"
        resource: ClassVar[str] = "override"
        description: ClassVar[str | None] = "Explicit wins"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    assert OverrideResource.description == "Explicit wins"


def test_extract_schemas_includes_description(mocker: MockerFixture) -> None:
    """extract_schemas includes resource description in output."""
    provider = Provider(name="desc_test")

    @provider.resource("with_desc")
    class WithDesc(Resource[DescConfig, DescOutputs]):
        description: ClassVar[str | None] = "A described resource"

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    mock_module = ModuleType("desc_provider")
    mock_module.WithDesc = WithDesc

    mocker.patch("pragma_sdk.provider.discovery.importlib.import_module", return_value=mock_module)

    schemas = extract_schemas("desc_provider")

    assert len(schemas) == 1
    assert schemas[0]["description"] == "A described resource"


def test_extract_schemas_omits_description_when_none(mocker: MockerFixture) -> None:
    """extract_schemas omits description key when resource has no description."""
    provider = Provider(name="no_desc_test")

    @provider.resource("no_desc")
    class NoDesc(Resource[DescConfig, DescOutputs]):
        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    mock_module = ModuleType("no_desc_provider")
    mock_module.NoDesc = NoDesc

    mocker.patch("pragma_sdk.provider.discovery.importlib.import_module", return_value=mock_module)

    schemas = extract_schemas("no_desc_provider")

    assert len(schemas) == 1
    assert "description" not in schemas[0]


def test_extract_schemas_includes_docstring_fallback_description(mocker: MockerFixture) -> None:
    """extract_schemas includes docstring-derived description."""
    provider = Provider(name="docstr_test")

    @provider.resource("docstr")
    class DocstrResource(Resource[DescConfig, DescOutputs]):
        """Manages cloud storage buckets."""

        async def on_create(self) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_update(self, previous_config: DescConfig) -> DescOutputs:
            return DescOutputs(url="http://example.com")

        async def on_delete(self) -> None:
            pass

    mock_module = ModuleType("docstr_provider")
    mock_module.DocstrResource = DocstrResource

    mocker.patch("pragma_sdk.provider.discovery.importlib.import_module", return_value=mock_module)

    schemas = extract_schemas("docstr_provider")

    assert len(schemas) == 1
    assert schemas[0]["description"] == "Manages cloud storage buckets."
