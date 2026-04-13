"""Tests for forward reference detection in Dependency declarations."""

from typing import ClassVar, ForwardRef

import pytest

from pragma_sdk import (
    Config,
    Dependency,
    Field,
    ImmutableDependency,
    Outputs,
    Resource,
    SensitiveDependency,
)
from pragma_sdk.models.base import _find_dependency_forward_ref


class StubOutputs(Outputs):
    """Stub outputs for forward reference tests."""

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


def test_dependency_forward_ref_raises_type_error() -> None:
    """Dependency['ClassName'] raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            dep: Dependency["NonExistent"]


def test_immutable_dependency_forward_ref_raises_type_error() -> None:
    """ImmutableDependency['ClassName'] raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            dep: ImmutableDependency["NonExistent"]


def test_sensitive_dependency_forward_ref_raises_type_error() -> None:
    """SensitiveDependency['ClassName'] raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            dep: SensitiveDependency["NonExistent"]


def test_list_dependency_forward_ref_raises_type_error() -> None:
    """list[Dependency['ClassName']] raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            deps: list[Dependency["NonExistent"]]


def test_optional_dependency_forward_ref_raises_type_error() -> None:
    """Dependency['ClassName'] | None raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            dep: Dependency["NonExistent"] | None = None


def test_optional_immutable_dependency_forward_ref_raises_type_error() -> None:
    """ImmutableDependency['ClassName'] | None raises TypeError at class definition time."""
    with pytest.raises(TypeError, match='Forward reference "NonExistent"'):

        class BadConfig(Config):
            dep: ImmutableDependency["NonExistent"] | None = None


def test_forward_ref_error_includes_class_and_field_name() -> None:
    """Error message includes both the Config class name and the field name."""
    with pytest.raises(TypeError, match=r"on BadConfig\.dep") as exc_info:

        class BadConfig(Config):
            dep: Dependency["MissingResource"]

    assert "MissingResource" in str(exc_info.value)
    assert "Move MissingResource above BadConfig" in str(exc_info.value)


def test_forward_ref_error_suggests_fix() -> None:
    """Error message tells the user to move the class or import it directly."""
    with pytest.raises(TypeError, match="Move .+ above .+ or import it directly"):

        class BadConfig(Config):
            dep: Dependency["MissingResource"]


def test_dependency_direct_reference_passes() -> None:
    """Dependency[ActualClass] (direct reference) works without error."""

    class GoodConfig(Config):
        """Test config."""

        dep: Dependency[StubResource]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="my-db")
    config = GoodConfig(dep=dep)
    assert config.dep.name == "my-db"


def test_immutable_dependency_direct_reference_passes() -> None:
    """ImmutableDependency[ActualClass] works without error."""

    class GoodConfig(Config):
        """Test config."""

        dep: ImmutableDependency[StubResource]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="my-db")
    config = GoodConfig(dep=dep)
    assert config.dep.name == "my-db"


def test_sensitive_dependency_direct_reference_passes() -> None:
    """SensitiveDependency[ActualClass] works without error."""

    class GoodConfig(Config):
        """Test config."""

        dep: SensitiveDependency[StubResource]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="my-db")
    config = GoodConfig(dep=dep)
    assert config.dep.name == "my-db"


def test_list_dependency_direct_reference_passes() -> None:
    """list[Dependency[ActualClass]] works without error."""

    class GoodConfig(Config):
        """Test config."""

        deps: list[Dependency[StubResource]]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="db1")
    config = GoodConfig(deps=[dep])
    assert len(config.deps) == 1


def test_optional_dependency_direct_reference_passes() -> None:
    """Dependency[ActualClass] | None works without error."""

    class GoodConfig(Config):
        """Test config."""

        dep: Dependency[StubResource] | None = None

    config = GoodConfig()
    assert config.dep is None


def test_non_dependency_fields_unaffected() -> None:
    """Non-dependency fields like Field[str] are not affected by the check."""

    class GoodConfig(Config):
        """Test config."""

        name: Field[str]
        dep: Dependency[StubResource]

    dep = Dependency[StubResource](project_id="proj-test", provider="test", resource="stub", name="my-db")
    config = GoodConfig(name="test", dep=dep)
    assert config.name == "test"


@pytest.mark.parametrize(
    ("forward_arg", "expected"),
    [
        ("Dependency[NonExistent]", "NonExistent"),
        ("ImmutableDependency[NonExistent]", "NonExistent"),
        ("SensitiveDependency[NonExistent]", "NonExistent"),
        ("Dependency[module.Resource]", "module.Resource"),
        ("ImmutableDependency[pkg.mod.Cls]", "pkg.mod.Cls"),
    ],
)
def test_find_dependency_forward_ref_unquoted(forward_arg: str, expected: str) -> None:
    """Unquoted forward refs from postponed annotations are detected."""
    ref = ForwardRef(forward_arg)
    assert _find_dependency_forward_ref(ref) == expected


@pytest.mark.parametrize(
    ("forward_arg", "expected"),
    [
        ("Dependency['NonExistent']", "NonExistent"),
        ('Dependency["NonExistent"]', "NonExistent"),
        ('ImmutableDependency["NonExistent"]', "NonExistent"),
        ('SensitiveDependency["NonExistent"]', "NonExistent"),
        ('Dependency["module.Resource"]', "module.Resource"),
    ],
)
def test_find_dependency_forward_ref_quoted(forward_arg: str, expected: str) -> None:
    """Quoted forward refs (explicit string annotations) are detected."""
    ref = ForwardRef(forward_arg)
    assert _find_dependency_forward_ref(ref) == expected


@pytest.mark.parametrize(
    "forward_arg",
    [
        "str",
        "list[str]",
        "SomethingElse[Foo]",
        "NotDependency[Bar]",
    ],
)
def test_find_dependency_forward_ref_ignores_non_dependency(forward_arg: str) -> None:
    """Non-Dependency forward refs return None."""
    ref = ForwardRef(forward_arg)
    assert _find_dependency_forward_ref(ref) is None
