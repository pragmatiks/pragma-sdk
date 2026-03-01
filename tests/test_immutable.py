"""Tests for immutable field types and validation."""

from __future__ import annotations

from typing import ClassVar

import pytest

from pragma_sdk import (
    Config,
    Dependency,
    Field,
    ImmutableDependency,
    ImmutableField,
    Outputs,
    Resource,
)


class StubOutputs(Outputs):
    """Stub outputs for immutable tests."""

    url: str


class StubResource(Resource[Config, StubOutputs]):
    """Stub resource for dependency type tests."""

    provider: ClassVar[str] = "test"
    resource: ClassVar[str] = "stub"

    async def on_create(self) -> StubOutputs:
        """Handle creation."""
        return StubOutputs(url="https://example.com")

    async def on_update(self, previous_config: Config) -> StubOutputs:
        """Handle update."""
        return StubOutputs(url="https://example.com/updated")

    async def on_delete(self) -> None:
        """Handle deletion."""


# --- Schema generation tests ---


def test_immutable_field_adds_immutable_to_schema() -> None:
    """ImmutableField[str] generates JSON schema with immutable=true on the property."""

    class MyConfig(Config):
        region: ImmutableField[str]

    schema = MyConfig.model_json_schema()
    region_prop = schema["properties"]["region"]

    assert region_prop.get("immutable") is True


def test_immutable_dependency_adds_immutable_to_schema() -> None:
    """ImmutableDependency[T] generates schema with __dependency__ marker and immutable=true."""

    class MyConfig(Config):
        db: ImmutableDependency[StubResource]

    schema = MyConfig.model_json_schema()
    db_prop = schema["properties"]["db"]

    assert db_prop.get("immutable") is True

    ref = db_prop.get("$ref", "")
    assert ref.startswith("#/$defs/")

    def_name = ref[len("#/$defs/") :]
    def_schema = schema["$defs"][def_name]
    assert def_schema.get("immutable") is True
    assert "provider" in def_schema["properties"]
    assert "__dependency__" in def_schema["properties"]


def test_field_does_not_have_immutable_in_schema() -> None:
    """Field[str] does NOT have immutable in schema."""

    class MyConfig(Config):
        name: Field[str]

    schema = MyConfig.model_json_schema()
    name_prop = schema["properties"]["name"]

    assert "immutable" not in name_prop

    ref = name_prop.get("$ref", "")

    if ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        assert "immutable" not in schema["$defs"][def_name]


def test_dependency_does_not_have_immutable_in_schema() -> None:
    """Dependency[T] does NOT have immutable in schema."""

    class MyConfig(Config):
        db: Dependency[StubResource]

    schema = MyConfig.model_json_schema()
    db_prop = schema["properties"]["db"]

    assert "immutable" not in db_prop

    ref = db_prop.get("$ref", "")

    if ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        assert "immutable" not in schema["$defs"][def_name]


def test_mixed_field_and_immutable_field_schema() -> None:
    """Mix of Field and ImmutableField generates correct schema markers."""

    class MyConfig(Config):
        name: Field[str]
        region: ImmutableField[str]
        size: Field[int]

    schema = MyConfig.model_json_schema()

    name_prop = schema["properties"]["name"]
    region_prop = schema["properties"]["region"]
    size_prop = schema["properties"]["size"]

    assert "immutable" not in name_prop
    assert region_prop.get("immutable") is True
    assert "immutable" not in size_prop

    name_ref = name_prop.get("$ref", "")

    if name_ref.startswith("#/$defs/"):
        assert "immutable" not in schema["$defs"][name_ref[len("#/$defs/") :]]

    region_ref = region_prop.get("$ref", "")

    if region_ref.startswith("#/$defs/"):
        assert schema["$defs"][region_ref[len("#/$defs/") :]].get("immutable") is True


def test_mixed_dependency_and_immutable_dependency_schema() -> None:
    """Mix of Dependency and ImmutableDependency generates correct schema markers."""

    class MyConfig(Config):
        mutable_db: Dependency[StubResource]
        immutable_db: ImmutableDependency[StubResource]

    schema = MyConfig.model_json_schema()

    mutable_prop = schema["properties"]["mutable_db"]
    immutable_prop = schema["properties"]["immutable_db"]

    assert "immutable" not in mutable_prop
    assert immutable_prop.get("immutable") is True


# --- Validation tests: bare types raise TypeError ---


def test_bare_str_raises_type_error() -> None:
    """Config with bare str type raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            name: str


def test_bare_int_raises_type_error() -> None:
    """Config with bare int type raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            count: int


def test_bare_bool_raises_type_error() -> None:
    """Config with bare bool type raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            enabled: bool


def test_bare_float_raises_type_error() -> None:
    """Config with bare float type raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            rate: float


def test_bare_optional_str_raises_type_error() -> None:
    """Config with bare Optional[str] raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            name: str | None = None


def test_bare_list_str_raises_type_error() -> None:
    """Config with bare list[str] raises TypeError."""
    with pytest.raises(TypeError, match="bare types are not allowed"):

        class BadConfig(Config):
            tags: list[str]


# --- Validation tests: valid types pass ---


def test_field_str_passes_validation() -> None:
    """Config with Field[str] passes validation."""

    class GoodConfig(Config):
        name: Field[str]

    config = GoodConfig(name="test")
    assert config.name == "test"


def test_immutable_field_str_passes_validation() -> None:
    """Config with ImmutableField[str] passes validation."""

    class GoodConfig(Config):
        region: ImmutableField[str]

    config = GoodConfig(region="us-east-1")
    assert config.region == "us-east-1"


def test_dependency_passes_validation() -> None:
    """Config with Dependency[T] passes validation."""

    class GoodConfig(Config):
        db: Dependency[StubResource]

    dep = Dependency[StubResource](provider="test", resource="stub", name="my-db")
    config = GoodConfig(db=dep)
    assert config.db.name == "my-db"


def test_immutable_dependency_passes_validation() -> None:
    """Config with ImmutableDependency[T] passes validation."""

    class GoodConfig(Config):
        db: ImmutableDependency[StubResource]

    dep = Dependency[StubResource](provider="test", resource="stub", name="my-db")
    config = GoodConfig(db=dep)
    assert config.db.name == "my-db"


def test_optional_field_passes_validation() -> None:
    """Config with Field[str] | None passes validation."""

    class GoodConfig(Config):
        label: Field[str] | None = None

    config = GoodConfig()
    assert config.label is None


def test_optional_dependency_passes_validation() -> None:
    """Config with Dependency[T] | None passes validation."""

    class GoodConfig(Config):
        db: Dependency[StubResource] | None = None

    config = GoodConfig()
    assert config.db is None


def test_list_field_passes_validation() -> None:
    """Config with list[Field[str]] passes validation."""

    class GoodConfig(Config):
        tags: list[Field[str]]

    config = GoodConfig(tags=["a", "b"])
    assert config.tags == ["a", "b"]


def test_list_dependency_passes_validation() -> None:
    """Config with list[Dependency[T]] passes validation."""

    class GoodConfig(Config):
        dbs: list[Dependency[StubResource]]

    dep = Dependency[StubResource](provider="test", resource="stub", name="db1")
    config = GoodConfig(dbs=[dep])
    assert len(config.dbs) == 1


def test_field_with_default_passes_validation() -> None:
    """Config with Field[int] and default value passes validation."""

    class GoodConfig(Config):
        name: Field[str]
        size: Field[int] = 10

    config = GoodConfig(name="test")
    assert config.size == 10


def test_error_message_includes_field_name_and_class() -> None:
    """TypeError message includes both field name and class name."""
    with pytest.raises(TypeError, match="'count'") as exc_info:

        class BadConfig(Config):
            count: int

    assert "BadConfig" in str(exc_info.value)


# --- extract_schemas includes immutable metadata ---


def test_extract_schemas_includes_immutable_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """extract_schemas propagates immutable metadata from model_json_schema."""

    class RegionConfig(Config):
        name: Field[str]
        region: ImmutableField[str]

    schema = RegionConfig.model_json_schema()

    region_prop = schema["properties"]["region"]
    name_prop = schema["properties"]["name"]

    assert region_prop.get("immutable") is True
    assert "immutable" not in name_prop


def test_immutable_field_accepts_field_reference() -> None:
    """ImmutableField[str] accepts a FieldReference value, same as Field[str]."""
    from pragma_sdk import FieldReference

    class MyConfig(Config):
        region: ImmutableField[str]

    ref = FieldReference(provider="gcp", resource="project", name="my-proj", field="outputs.region")
    config = MyConfig(region=ref)
    assert isinstance(config.region, FieldReference)
    assert config.region.field == "outputs.region"


def test_immutable_field_accepts_direct_value() -> None:
    """ImmutableField[str] accepts a direct string value."""

    class MyConfig(Config):
        region: ImmutableField[str]

    config = MyConfig(region="us-central1")
    assert config.region == "us-central1"
