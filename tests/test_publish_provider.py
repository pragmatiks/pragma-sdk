"""Unit tests for the wheel-based publish flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pytest_mock import MockerFixture

from pragma_sdk import AsyncPragmaClient, PragmaClient
from pragma_sdk.provider.publish import (
    DEFAULT_ARTIFACT_REPO,
    WheelPublishPayload,
    _build_wheel,
    _detect_provider_package,
    _detect_provider_short_name,
    _detect_provider_version,
    _detect_runtime_overrides,
    _normalize_artifact_repo,
    _to_resource_schemas_dict,
    _upload_wheel_to_artifact_registry,
)


def _write_pyproject(path: Path, body: str) -> None:
    (path / "pyproject.toml").write_text(body)


def test_detect_provider_package_explicit():
    pyproject = {"tool": {"pragma": {"package": "my_provider"}}, "project": {"name": "ignored"}}
    assert _detect_provider_package(pyproject) == "my_provider"


def test_detect_provider_package_from_distribution_name():
    pyproject = {"project": {"name": "postgres-provider"}}
    assert _detect_provider_package(pyproject) == "postgres_provider"


def test_detect_provider_package_missing_raises():
    with pytest.raises(ValueError, match="Could not determine provider package name"):
        _detect_provider_package({"project": {"name": "no-suffix"}})


def test_detect_provider_short_name_from_pragma_section():
    pyproject = {"tool": {"pragma": {"name": "gcp"}}}
    assert _detect_provider_short_name(pyproject) == "gcp"


def test_detect_provider_short_name_from_distribution():
    pyproject = {"project": {"name": "postgres-provider"}}
    assert _detect_provider_short_name(pyproject) == "postgres"


def test_detect_provider_short_name_missing_raises():
    with pytest.raises(ValueError, match="Could not determine provider short name"):
        _detect_provider_short_name({"project": {"name": "untagged"}})


def test_detect_provider_version_present():
    assert _detect_provider_version({"project": {"version": "1.2.3"}}) == "1.2.3"


def test_detect_provider_version_missing():
    assert _detect_provider_version({"project": {}}) is None


def test_detect_runtime_overrides_present():
    pyproject = {"tool": {"pragma": {"image": "ghcr.io/me/runtime:1", "entrypoint": ["python", "-m", "x"]}}}
    image, entrypoint = _detect_runtime_overrides(pyproject)
    assert image == "ghcr.io/me/runtime:1"
    assert entrypoint == ["python", "-m", "x"]


def test_detect_runtime_overrides_absent():
    image, entrypoint = _detect_runtime_overrides({})
    assert image is None
    assert entrypoint is None


def test_detect_runtime_overrides_invalid_image():
    with pytest.raises(TypeError, match="image must be a string"):
        _detect_runtime_overrides({"tool": {"pragma": {"image": 42}}})


def test_detect_runtime_overrides_invalid_entrypoint():
    with pytest.raises(TypeError, match="entrypoint must be a list of strings"):
        _detect_runtime_overrides({"tool": {"pragma": {"entrypoint": "python"}}})


def test_to_resource_schemas_dict_keyed_by_resource():
    schemas = [
        {
            "provider": "acme/storage",
            "resource": "bucket",
            "config_schema": {"type": "object"},
            "outputs_schema": {"type": "object", "properties": {"url": {"type": "string"}}},
            "description": "An object bucket",
            "field_descriptions": {"name": "Bucket name"},
        },
        {
            "provider": "acme/storage",
            "resource": "object",
            "config_schema": {"type": "object"},
        },
    ]

    result = _to_resource_schemas_dict(schemas)

    assert set(result.keys()) == {"bucket", "object"}
    assert result["bucket"]["description"] == "An object bucket"
    assert result["bucket"]["outputs_schema"] == {"type": "object", "properties": {"url": {"type": "string"}}}
    assert "field_descriptions" not in result["bucket"]
    assert result["object"]["outputs_schema"] is None


def test_normalize_artifact_repo_strips_scheme():
    assert _normalize_artifact_repo("https://europe-west4-python.pkg.dev/proj/repo") == (
        "europe-west4-python.pkg.dev/proj/repo"
    )
    assert _normalize_artifact_repo("europe-west4-python.pkg.dev/proj/repo/") == (
        "europe-west4-python.pkg.dev/proj/repo"
    )
    assert _normalize_artifact_repo("europe-west4-python.pkg.dev/proj/repo") == (
        "europe-west4-python.pkg.dev/proj/repo"
    )


def test_build_wheel_uv_missing_raises(tmp_path: Path, mocker: MockerFixture):
    mocker.patch("pragma_sdk.provider.publish.shutil.which", return_value=None)

    with pytest.raises(FileNotFoundError, match="'uv' binary not found"):
        _build_wheel(tmp_path, tmp_path)


def test_upload_wheel_twine_missing_raises(tmp_path: Path, mocker: MockerFixture):
    wheel = tmp_path / "x-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"\x00")

    mocker.patch("pragma_sdk.provider.publish.shutil.which", return_value=None)

    with pytest.raises(FileNotFoundError, match="'twine' binary not found"):
        _upload_wheel_to_artifact_registry(wheel, "europe-west4-python.pkg.dev/p/r")


_ME_PAYLOAD = {
    "user_id": "user-1",
    "email": "alice@example.com",
    "organization_id": "org-1",
    "organization_name": "Acme",
}

_ORG_PAYLOAD = {
    "organization_id": "org-1",
    "name": "Acme",
    "slug": "acme",
    "status": "active",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_VERSION_PAYLOAD = {
    "prefix": "acme",
    "name": "storage",
    "version": "1.2.3",
    "runtime_version": "1.0.0",
    "wheel_url": (
        "https://europe-west4-python.pkg.dev/pragmatiks-prod/pragma-providers/storage_provider-1.2.3-py3-none-any.whl"
    ),
    "schemas": [],
    "status": "published",
    "published_at": "2026-05-01T12:00:00Z",
    "created_at": "2026-05-01T12:00:00Z",
    "updated_at": "2026-05-01T12:00:00Z",
}


def _publish_payload(**overrides: Any) -> WheelPublishPayload:
    base = WheelPublishPayload(
        name="storage",
        version="1.2.3",
        wheel_url=(
            "https://europe-west4-python.pkg.dev/pragmatiks-prod/pragma-providers/"
            "storage_provider-1.2.3-py3-none-any.whl"
        ),
        config_schema=None,
        outputs_schema=None,
        resource_schemas={
            "bucket": {
                "provider": "acme/storage",
                "resource": "bucket",
                "description": None,
                "config_schema": {"type": "object"},
                "outputs_schema": None,
            }
        },
        runtime_image=None,
        entrypoint=None,
    )
    return WheelPublishPayload(**{**base.__dict__, **overrides})


@respx.mock
def test_publish_provider_sync_posts_wheel_payload(tmp_path: Path, mocker: MockerFixture):
    captured: dict[str, Any] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json=_VERSION_PAYLOAD)

    respx.get("http://api.test/auth/me").mock(return_value=httpx.Response(200, json=_ME_PAYLOAD))
    respx.get("http://api.test/organizations/org-1").mock(return_value=httpx.Response(200, json=_ORG_PAYLOAD))
    respx.post("http://api.test/provider-versions").mock(side_effect=_record)

    prep = mocker.patch("pragma_sdk.client.prepare_wheel_publish", return_value=_publish_payload())

    with PragmaClient(base_url="http://api.test", auth_token=None) as client:
        result = client.publish_provider(tmp_path, version="1.2.3", changelog="initial release")

    prep.assert_called_once()
    kwargs = prep.call_args.kwargs
    assert kwargs["catalog_prefix"] == "acme"
    assert kwargs["version"] == "1.2.3"
    assert kwargs["artifact_repo"] == DEFAULT_ARTIFACT_REPO

    assert result.canonical == "acme/storage"
    assert result.wheel_url is not None
    assert result.wheel_url.endswith(".whl")

    import json as _json

    body = _json.loads(captured["body"])
    assert body["organization_id"] == "org-1"
    assert body["name"] == "storage"
    assert body["version"] == "1.2.3"
    assert body["wheel_url"].endswith("storage_provider-1.2.3-py3-none-any.whl")
    assert body["resource_schemas"]["bucket"]["resource"] == "bucket"
    assert body["changelog"] == "initial release"
    assert "config_schema" not in body
    assert "outputs_schema" not in body
    assert "runtime_image" not in body
    assert "entrypoint" not in body


@respx.mock
def test_publish_provider_sync_uses_explicit_organization(tmp_path: Path, mocker: MockerFixture):
    org_payload = {**_ORG_PAYLOAD, "organization_id": "org-2", "slug": "other"}
    respx.get("http://api.test/organizations/org-2").mock(
        return_value=httpx.Response(200, json=org_payload),
    )
    respx.post("http://api.test/provider-versions").mock(
        return_value=httpx.Response(201, json=_VERSION_PAYLOAD),
    )

    me_route = respx.get("http://api.test/auth/me")

    prep = mocker.patch("pragma_sdk.client.prepare_wheel_publish", return_value=_publish_payload())

    with PragmaClient(base_url="http://api.test", auth_token=None) as client:
        client.publish_provider(tmp_path, version="1.2.3", organization_id="org-2")

    assert me_route.call_count == 0
    assert prep.call_args.kwargs["catalog_prefix"] == "other"


@respx.mock
def test_publish_provider_sync_includes_runtime_overrides(tmp_path: Path, mocker: MockerFixture):
    captured: dict[str, Any] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json=_VERSION_PAYLOAD)

    respx.get("http://api.test/auth/me").mock(return_value=httpx.Response(200, json=_ME_PAYLOAD))
    respx.get("http://api.test/organizations/org-1").mock(return_value=httpx.Response(200, json=_ORG_PAYLOAD))
    respx.post("http://api.test/provider-versions").mock(side_effect=_record)

    payload = _publish_payload(runtime_image="ghcr.io/me/runtime:1", entrypoint=["python", "-m", "custom"])
    mocker.patch("pragma_sdk.client.prepare_wheel_publish", return_value=payload)

    with PragmaClient(base_url="http://api.test", auth_token=None) as client:
        client.publish_provider(tmp_path, version="1.2.3")

    import json as _json

    body = _json.loads(captured["body"])
    assert body["runtime_image"] == "ghcr.io/me/runtime:1"
    assert body["entrypoint"] == ["python", "-m", "custom"]


@respx.mock
async def test_publish_provider_async_posts_wheel_payload(tmp_path: Path, mocker: MockerFixture):
    captured: dict[str, Any] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json=_VERSION_PAYLOAD)

    respx.get("http://api.test/auth/me").mock(return_value=httpx.Response(200, json=_ME_PAYLOAD))
    respx.get("http://api.test/organizations/org-1").mock(return_value=httpx.Response(200, json=_ORG_PAYLOAD))
    respx.post("http://api.test/provider-versions").mock(side_effect=_record)

    mocker.patch("pragma_sdk.client.prepare_wheel_publish", return_value=_publish_payload())

    async with AsyncPragmaClient(base_url="http://api.test", auth_token=None) as client:
        result = await client.publish_provider(tmp_path, version="1.2.3")

    assert result.canonical == "acme/storage"

    import json as _json

    body = _json.loads(captured["body"])
    assert body["organization_id"] == "org-1"
    assert body["wheel_url"].endswith(".whl")


def test_prepare_wheel_publish_detects_metadata(tmp_path: Path, mocker: MockerFixture):
    """End-to-end metadata detection with build/extract/upload stubbed out."""
    src_dir = tmp_path / "src" / "storage_provider"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("")

    _write_pyproject(
        tmp_path,
        """
[project]
name = "storage-provider"
version = "1.2.3"

[tool.pragma]
name = "storage"
image = "ghcr.io/me/runtime:1"
entrypoint = ["python", "-m", "pragma_runtime.entrypoint"]
""",
    )

    wheel_path = tmp_path / "out" / "storage_provider-1.2.3-py3-none-any.whl"

    def _fake_build(provider_dir: Path, out_dir: Path) -> Path:  # noqa: ARG001
        wheel_path.parent.mkdir(parents=True, exist_ok=True)
        wheel_path.write_bytes(b"\x00")
        return wheel_path

    def _fake_extract(provider_dir: Path, package_name: str, catalog_name: str) -> list[dict[str, Any]]:  # noqa: ARG001
        return [
            {
                "provider": catalog_name,
                "resource": "bucket",
                "config_schema": {"type": "object"},
            }
        ]

    def _fake_upload(wheel: Path, repo: str) -> str:
        return f"https://{repo}/{wheel.name}"

    from pragma_sdk.provider import publish as publish_mod

    mocker.patch.object(publish_mod, "_build_wheel", side_effect=_fake_build)
    mocker.patch.object(publish_mod, "_extract_schemas_from_dir", side_effect=_fake_extract)
    mocker.patch.object(publish_mod, "_upload_wheel_to_artifact_registry", side_effect=_fake_upload)

    payload = publish_mod.prepare_wheel_publish(tmp_path, catalog_prefix="acme")

    assert payload.name == "storage"
    assert payload.version == "1.2.3"
    assert payload.wheel_url.endswith("/storage_provider-1.2.3-py3-none-any.whl")
    assert payload.wheel_url.startswith("https://europe-west4-python.pkg.dev/")
    assert payload.runtime_image == "ghcr.io/me/runtime:1"
    assert payload.entrypoint == ["python", "-m", "pragma_runtime.entrypoint"]
    assert "bucket" in payload.resource_schemas


def test_prepare_wheel_publish_explicit_overrides_pyproject(tmp_path: Path, mocker: MockerFixture):
    _write_pyproject(
        tmp_path,
        """
[project]
name = "storage-provider"
version = "1.0.0"

[tool.pragma]
name = "storage"
image = "default-image"
entrypoint = ["default", "entry"]
""",
    )

    from pragma_sdk.provider import publish as publish_mod

    mocker.patch.object(publish_mod, "_build_wheel", return_value=tmp_path / "out.whl")
    mocker.patch.object(publish_mod, "_extract_schemas_from_dir", return_value=[])
    mocker.patch.object(publish_mod, "_upload_wheel_to_artifact_registry", return_value="https://repo/out.whl")

    payload = publish_mod.prepare_wheel_publish(
        tmp_path,
        name="custom",
        version="2.0.0",
        catalog_prefix="acme",
        runtime_image="override-image",
        entrypoint=["override", "entry"],
    )

    assert payload.name == "custom"
    assert payload.version == "2.0.0"
    assert payload.runtime_image == "override-image"
    assert payload.entrypoint == ["override", "entry"]


def test_prepare_wheel_publish_missing_dir_raises(tmp_path: Path):
    from pragma_sdk.provider import publish as publish_mod

    with pytest.raises(FileNotFoundError, match="Provider directory does not exist"):
        publish_mod.prepare_wheel_publish(tmp_path / "missing", catalog_prefix="acme")


def test_prepare_wheel_publish_requires_version(tmp_path: Path):
    _write_pyproject(
        tmp_path,
        """
[project]
name = "storage-provider"
""",
    )

    from pragma_sdk.provider import publish as publish_mod

    with pytest.raises(ValueError, match="version not specified"):
        publish_mod.prepare_wheel_publish(tmp_path, catalog_prefix="acme")


def test_legacy_polling_helpers_removed():
    """The old polling helpers must be gone — publish is synchronous."""
    assert not hasattr(PragmaClient, "get_publish_status")
    assert not hasattr(PragmaClient, "stream_publish_logs")
    assert not hasattr(AsyncPragmaClient, "get_publish_status")
    assert not hasattr(AsyncPragmaClient, "stream_publish_logs")
