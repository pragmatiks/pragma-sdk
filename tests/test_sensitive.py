"""Tests for sensitive field types, schema generation, and validation."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field as PydanticField

from pragma_sdk import (
    Config,
    Dependency,
    Field,
    FieldReference,
    ImmutableField,
    ImmutableSensitiveField,
    Outputs,
    Resource,
    Sensitive,
    SensitiveDependency,
    SensitiveField,
    SensitiveOutput,
)


class StubOutputs(Outputs):
    """Stub outputs for sensitive tests."""

    url: str


class StubResource(Resource[Config, StubOutputs]):
    """Stub resource for dependency type tests."""

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


def test_sensitive_field_adds_sensitive_to_schema() -> None:
    """SensitiveField[str] generates JSON schema with sensitive=true on the property."""

    class MyConfig(Config):
        """Test config."""

        api_key: SensitiveField[str]

    schema = MyConfig.model_json_schema()
    api_key_prop = schema["properties"]["api_key"]

    assert api_key_prop.get("sensitive") is True


def test_sensitive_dependency_adds_sensitive_to_schema() -> None:
    """SensitiveDependency[T] generates schema with __dependency__ marker and sensitive=true."""

    class MyConfig(Config):
        """Test config."""

        secret_db: SensitiveDependency[StubResource]

    schema = MyConfig.model_json_schema()
    db_prop = schema["properties"]["secret_db"]

    assert db_prop.get("sensitive") is True

    ref = db_prop.get("$ref", "")
    assert ref.startswith("#/$defs/")

    def_name = ref[len("#/$defs/") :]
    def_schema = schema["$defs"][def_name]
    assert def_schema.get("sensitive") is True
    assert "provider" in def_schema["properties"]
    assert "__dependency__" in def_schema["properties"]


def test_immutable_sensitive_field_has_both_markers() -> None:
    """ImmutableSensitiveField[str] generates schema with both immutable=true and sensitive=true."""

    class MyConfig(Config):
        """Test config."""

        master_key: ImmutableSensitiveField[str]

    schema = MyConfig.model_json_schema()
    prop = schema["properties"]["master_key"]

    assert prop.get("immutable") is True
    assert prop.get("sensitive") is True


def test_field_does_not_have_sensitive_in_schema() -> None:
    """Field[str] does NOT have sensitive in schema."""

    class MyConfig(Config):
        """Test config."""

        name: Field[str]

    schema = MyConfig.model_json_schema()
    name_prop = schema["properties"]["name"]

    assert "sensitive" not in name_prop

    ref = name_prop.get("$ref", "")

    if ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        assert "sensitive" not in schema["$defs"][def_name]


def test_dependency_does_not_have_sensitive_in_schema() -> None:
    """Dependency[T] does NOT have sensitive in schema."""

    class MyConfig(Config):
        """Test config."""

        db: Dependency[StubResource]

    schema = MyConfig.model_json_schema()
    db_prop = schema["properties"]["db"]

    assert "sensitive" not in db_prop

    ref = db_prop.get("$ref", "")

    if ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        assert "sensitive" not in schema["$defs"][def_name]


def test_mixed_field_and_sensitive_field_schema() -> None:
    """Mix of Field and SensitiveField generates correct schema markers."""

    class MyConfig(Config):
        """Test config."""

        name: Field[str]
        api_key: SensitiveField[str]
        size: Field[int]

    schema = MyConfig.model_json_schema()

    name_prop = schema["properties"]["name"]
    api_key_prop = schema["properties"]["api_key"]
    size_prop = schema["properties"]["size"]

    assert "sensitive" not in name_prop
    assert api_key_prop.get("sensitive") is True
    assert "sensitive" not in size_prop


def test_mixed_dependency_and_sensitive_dependency_schema() -> None:
    """Mix of Dependency and SensitiveDependency generates correct schema markers."""

    class MyConfig(Config):
        """Test config."""

        public_db: Dependency[StubResource]
        secret_db: SensitiveDependency[StubResource]

    schema = MyConfig.model_json_schema()

    public_prop = schema["properties"]["public_db"]
    secret_prop = schema["properties"]["secret_db"]

    assert "sensitive" not in public_prop
    assert secret_prop.get("sensitive") is True


def test_mixed_immutable_and_sensitive_fields() -> None:
    """Mix of ImmutableField, SensitiveField, and ImmutableSensitiveField in one Config."""

    class MyConfig(Config):
        """Test config."""

        region: ImmutableField[str]
        api_key: SensitiveField[str]
        master_key: ImmutableSensitiveField[str]
        name: Field[str]

    schema = MyConfig.model_json_schema()

    region_prop = schema["properties"]["region"]
    api_key_prop = schema["properties"]["api_key"]
    master_key_prop = schema["properties"]["master_key"]
    name_prop = schema["properties"]["name"]

    assert region_prop.get("immutable") is True
    assert "sensitive" not in region_prop

    assert api_key_prop.get("sensitive") is True
    assert "immutable" not in api_key_prop

    assert master_key_prop.get("immutable") is True
    assert master_key_prop.get("sensitive") is True

    assert "immutable" not in name_prop
    assert "sensitive" not in name_prop


# --- Optional sensitive field tests ---


def test_optional_sensitive_field_has_sensitive_in_schema() -> None:
    """SensitiveField[str] | None generates schema with sensitive=true."""

    class MyConfig(Config):
        """Test config."""

        token: SensitiveField[str] | None = None

    schema = MyConfig.model_json_schema()

    token_prop = schema.get("properties", {}).get("token", {})
    assert token_prop.get("sensitive") is True


def test_optional_sensitive_dependency_has_sensitive_in_schema() -> None:
    """SensitiveDependency[T] | None generates schema with sensitive=true."""

    class MyConfig(Config):
        """Test config."""

        secret_db: SensitiveDependency[StubResource] | None = None

    schema = MyConfig.model_json_schema()
    db_prop = schema["properties"]["secret_db"]

    assert db_prop.get("sensitive") is True


# --- Outputs schema tests ---


def test_sensitive_output_adds_sensitive_to_schema() -> None:
    """SensitiveOutput[str] generates JSON schema with sensitive=true on the output property."""

    class MyOutputs(Outputs):
        """Test outputs."""

        url: str
        connection_string: SensitiveOutput[str]

    schema = MyOutputs.model_json_schema()

    url_prop = schema["properties"]["url"]
    conn_prop = schema["properties"]["connection_string"]

    assert "sensitive" not in url_prop
    assert conn_prop.get("sensitive") is True


def test_outputs_without_sensitive_has_no_markers() -> None:
    """Outputs with no sensitive fields returns clean schema."""

    class MyOutputs(Outputs):
        """Test outputs."""

        url: str
        port: int

    schema = MyOutputs.model_json_schema()

    assert "sensitive" not in schema["properties"]["url"]
    assert "sensitive" not in schema["properties"]["port"]


def test_all_sensitive_outputs() -> None:
    """Outputs where all fields are sensitive."""

    class MyOutputs(Outputs):
        """Test outputs."""

        token: SensitiveOutput[str]
        secret: SensitiveOutput[str]

    schema = MyOutputs.model_json_schema()

    assert schema["properties"]["token"].get("sensitive") is True
    assert schema["properties"]["secret"].get("sensitive") is True


# --- Validation tests: sensitive types pass _is_valid_config_field ---


def test_sensitive_field_str_passes_validation() -> None:
    """Config with SensitiveField[str] passes validation."""

    class GoodConfig(Config):
        """Test config."""

        api_key: SensitiveField[str]

    config = GoodConfig(api_key="sk-1234")
    assert config.api_key == "sk-1234"


def test_sensitive_dependency_passes_validation() -> None:
    """Config with SensitiveDependency[T] passes validation."""

    class GoodConfig(Config):
        """Test config."""

        secret_db: SensitiveDependency[StubResource]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="my-db")
    config = GoodConfig(secret_db=dep)
    assert config.secret_db.name == "my-db"


def test_immutable_sensitive_field_passes_validation() -> None:
    """Config with ImmutableSensitiveField[str] passes validation."""

    class GoodConfig(Config):
        """Test config."""

        master_key: ImmutableSensitiveField[str]

    config = GoodConfig(master_key="mk-secret")
    assert config.master_key == "mk-secret"


def test_optional_sensitive_field_passes_validation() -> None:
    """Config with SensitiveField[str] | None passes validation."""

    class GoodConfig(Config):
        """Test config."""

        token: SensitiveField[str] | None = None

    config = GoodConfig()
    assert config.token is None


def test_sensitive_field_accepts_field_reference() -> None:
    """SensitiveField[str] accepts a FieldReference value, same as Field[str]."""

    class MyConfig(Config):
        """Test config."""

        api_key: SensitiveField[str]

    ref = FieldReference(
        project_id="proj-test",
        provider="vault",
        resource="secret",
        name="my-key",
        field="outputs.value",
    )
    config = MyConfig(api_key=ref)
    assert isinstance(config.api_key, FieldReference)
    assert config.api_key.field == "outputs.value"


def test_sensitive_field_accepts_direct_value() -> None:
    """SensitiveField[str] accepts a direct string value."""

    class MyConfig(Config):
        """Test config."""

        api_key: SensitiveField[str]

    config = MyConfig(api_key="sk-1234")
    assert config.api_key == "sk-1234"


def test_immutable_sensitive_field_accepts_field_reference() -> None:
    """ImmutableSensitiveField[str] accepts a FieldReference value."""

    class MyConfig(Config):
        """Test config."""

        master_key: ImmutableSensitiveField[str]

    ref = FieldReference(
        project_id="proj-test",
        provider="vault",
        resource="secret",
        name="master",
        field="outputs.key",
    )
    config = MyConfig(master_key=ref)
    assert isinstance(config.master_key, FieldReference)


# --- Aliased field regression tests ---


def test_sensitive_field_with_alias_marks_aliased_property() -> None:
    """SensitiveField with alias generates sensitive=true on the aliased property name."""

    class MyConfig(Config):
        """Test config."""

        api_key: SensitiveField[str] = PydanticField(alias="api_key_alias")

    schema = MyConfig.model_json_schema()

    assert "api_key_alias" in schema["properties"]
    assert schema["properties"]["api_key_alias"].get("sensitive") is True
    assert "api_key" not in schema["properties"]


def test_sensitive_output_with_alias_marks_aliased_property() -> None:
    """SensitiveOutput with alias generates sensitive=true on the aliased property name."""

    class MyOutputs(Outputs):
        """Test outputs."""

        secret: SensitiveOutput[str] = PydanticField(alias="secret_alias")

    schema = MyOutputs.model_json_schema()

    assert "secret_alias" in schema["properties"]
    assert schema["properties"]["secret_alias"].get("sensitive") is True
    assert "secret" not in schema["properties"]


# --- Sensitive marker class tests ---


def test_sensitive_marker_is_distinct_from_immutable() -> None:
    """Sensitive and Immutable are distinct marker classes."""
    from pragma_sdk import Immutable

    s = Sensitive()
    i = Immutable()

    assert not isinstance(s, Immutable)
    assert not isinstance(i, Sensitive)
