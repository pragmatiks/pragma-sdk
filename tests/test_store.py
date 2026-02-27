"""Tests for unified provider store client methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from pragma_sdk.client import AsyncPragmaClient, PragmaClient
from pragma_sdk.models import (
    DeploymentResult,
    InstalledProvider,
    InstalledProviderSummary,
    PaginatedResponse,
    ResourceTier,
    StoreProviderDetail,
    StoreProviderSummary,
    StoreVersion,
    TrustTier,
    UpgradePolicy,
    VersionStatus,
)


STORE_PROVIDER_SUMMARY = {
    "name": "qdrant",
    "display_name": "Qdrant",
    "description": "Vector database provider",
    "author": {"tenant_id": "tenant_123", "org_name": "Pragmatiks"},
    "trust_tier": "official",
    "icon_url": "https://example.com/qdrant.png",
    "tags": ["vector", "database"],
    "latest_version": "1.2.0",
    "install_count": 42,
}

STORE_PROVIDER = {
    **STORE_PROVIDER_SUMMARY,
    "readme": "# Qdrant Provider\nVector database.",
    "created_at": "2026-01-15T12:00:00Z",
    "updated_at": "2026-02-20T10:30:00Z",
}

STORE_VERSION = {
    "provider_name": "qdrant",
    "version": "1.2.0",
    "runtime_version": "0.5.0",
    "image_url": "gcr.io/pragmatiks/qdrant:1.2.0",
    "source_hash": "abc123",
    "build_id": "build-456",
    "schemas": [{"type": "object"}],
    "changelog": "Added new features",
    "status": "published",
    "published_at": "2026-02-20T10:30:00Z",
    "error_message": None,
    "created_at": "2026-02-20T10:00:00Z",
    "updated_at": "2026-02-20T10:30:00Z",
}

INSTALLED_PROVIDER = {
    "store_provider_name": "qdrant",
    "installed_version": "1.2.0",
    "upgrade_policy": "manual",
    "resource_tier": "standard",
    "installed_at": "2026-02-21T08:00:00Z",
    "created_at": "2026-02-21T08:00:00Z",
    "updated_at": "2026-02-21T08:00:00Z",
}

INSTALLED_PROVIDER_SUMMARY = {
    "store_provider_name": "qdrant",
    "installed_version": "1.2.0",
    "upgrade_policy": "manual",
    "resource_tier": "standard",
    "installed_at": "2026-02-21T08:00:00Z",
    "latest_version": "1.3.0",
    "upgrade_available": True,
}


# --- Sync: list_providers ---


@respx.mock
def test_list_providers_returns_paginated_response() -> None:
    respx.get("http://localhost:8000/providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [STORE_PROVIDER_SUMMARY],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.list_providers()

    assert isinstance(result, PaginatedResponse)
    assert result.total == 1
    assert result.limit == 20
    assert result.offset == 0
    assert len(result.items) == 1
    assert isinstance(result.items[0], StoreProviderSummary)
    assert result.items[0].name == "qdrant"
    assert result.items[0].trust_tier == TrustTier.OFFICIAL
    assert result.items[0].install_count == 42


@respx.mock
def test_list_providers_passes_query_params() -> None:
    route = respx.get("http://localhost:8000/providers").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 10, "offset": 5},
        )
    )

    with PragmaClient(auth_token=None) as client:
        client.list_providers(query="qdrant", trust_tier="official", tags=["vector"], limit=10, offset=5)

    request = route.calls[0].request
    assert request.url.params["q"] == "qdrant"
    assert request.url.params["trust_tier"] == "official"
    assert request.url.params["limit"] == "10"
    assert request.url.params["offset"] == "5"
    assert "tags" in str(request.url)


# --- Sync: get_provider ---


@respx.mock
def test_get_provider_returns_detail() -> None:
    respx.get("http://localhost:8000/providers/pragma/qdrant").mock(
        return_value=httpx.Response(
            200,
            json={
                "provider": STORE_PROVIDER,
                "versions": [STORE_VERSION],
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_provider("pragma/qdrant")

    assert isinstance(result, StoreProviderDetail)
    assert result.provider.name == "qdrant"
    assert result.provider.readme == "# Qdrant Provider\nVector database."
    assert len(result.versions) == 1
    assert result.versions[0].version == "1.2.0"


@respx.mock
def test_get_provider_raises_on_not_found() -> None:
    respx.get("http://localhost:8000/providers/pragma/nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_provider("pragma/nonexistent")

    assert exc_info.value.response.status_code == 404


def test_get_provider_raises_on_unnamespaced_name() -> None:
    with PragmaClient(auth_token=None) as client:
        with pytest.raises(ValueError, match="org/name"):
            client.get_provider("qdrant")


# --- Sync: install_provider ---


@respx.mock
def test_install_provider_returns_installed() -> None:
    respx.post("http://localhost:8000/providers/install").mock(
        return_value=httpx.Response(201, json=INSTALLED_PROVIDER)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.install_provider("pragma/qdrant", version="1.2.0")

    assert isinstance(result, InstalledProvider)
    assert result.store_provider_name == "qdrant"
    assert result.installed_version == "1.2.0"
    assert result.upgrade_policy == UpgradePolicy.MANUAL
    assert result.resource_tier == ResourceTier.STANDARD


@respx.mock
def test_install_provider_raises_on_conflict() -> None:
    respx.post("http://localhost:8000/providers/install").mock(
        return_value=httpx.Response(409, json={"detail": "Already installed"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.install_provider("pragma/qdrant")

    assert exc_info.value.response.status_code == 409


# --- Sync: uninstall_provider ---


@respx.mock
def test_uninstall_provider_succeeds() -> None:
    respx.delete("http://localhost:8000/providers/installed/pragma/qdrant").mock(return_value=httpx.Response(204))

    with PragmaClient(auth_token=None) as client:
        client.uninstall_provider("pragma/qdrant")


@respx.mock
def test_uninstall_provider_with_cascade() -> None:
    route = respx.delete("http://localhost:8000/providers/installed/pragma/qdrant").mock(
        return_value=httpx.Response(204)
    )

    with PragmaClient(auth_token=None) as client:
        client.uninstall_provider("pragma/qdrant", cascade=True)

    assert route.calls[0].request.url.params["cascade"] == "true"


# --- Sync: upgrade_provider ---


@respx.mock
def test_upgrade_provider_returns_installed() -> None:
    upgraded = {**INSTALLED_PROVIDER, "installed_version": "1.3.0"}
    route = respx.post("http://localhost:8000/providers/installed/pragma/qdrant/upgrade").mock(
        return_value=httpx.Response(200, json=upgraded)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.upgrade_provider("pragma/qdrant", target_version="1.3.0")

    assert isinstance(result, InstalledProvider)
    assert result.installed_version == "1.3.0"

    import json

    body = json.loads(route.calls[0].request.content)
    assert "version" in body
    assert body["version"] == "1.3.0"
    assert "target_version" not in body


# --- Sync: list_installed_providers ---


@respx.mock
def test_list_installed_providers_returns_summaries() -> None:
    respx.get("http://localhost:8000/providers/installed").mock(
        return_value=httpx.Response(200, json=[INSTALLED_PROVIDER_SUMMARY])
    )

    with PragmaClient(auth_token=None) as client:
        result = client.list_installed_providers()

    assert len(result) == 1
    assert isinstance(result[0], InstalledProviderSummary)
    assert result[0].store_provider_name == "qdrant"
    assert result[0].upgrade_available is True
    assert result[0].latest_version == "1.3.0"


# --- Sync: publish_provider ---


@respx.mock
def test_publish_provider_returns_version() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.publish_provider("pragma/qdrant", b"tarball-content", "1.2.0", changelog="New stuff")

    assert route.called
    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.BUILDING
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers.get("content-type", "")


@respx.mock
def test_publish_provider_includes_metadata_fields() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    with PragmaClient(auth_token=None) as client:
        client.publish_provider(
            "pragma/qdrant",
            b"tarball-content",
            "1.2.0",
            display_name="Qdrant Vector DB",
            description="A vector database provider",
            tags=["database", "vector"],
        )

    assert route.called
    request = route.calls[0].request
    body = request.content.decode("utf-8", errors="replace")
    assert "display_name" in body
    assert "Qdrant Vector DB" in body
    assert "description" in body
    assert "A vector database provider" in body
    assert "tags" in body
    assert '["database", "vector"]' in body


@respx.mock
def test_publish_provider_omits_none_metadata_fields() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    with PragmaClient(auth_token=None) as client:
        client.publish_provider("pragma/qdrant", b"tarball-content", "1.2.0")

    assert route.called
    request = route.calls[0].request
    body = request.content.decode("utf-8", errors="replace")
    assert "display_name" not in body
    assert "description" not in body
    assert "tags" not in body


# --- Sync: get_publish_status ---


@respx.mock
def test_get_publish_status_returns_version() -> None:
    respx.get("http://localhost:8000/providers/pragma/qdrant/versions/1.2.0/status").mock(
        return_value=httpx.Response(200, json=STORE_VERSION)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_publish_status("pragma/qdrant", "1.2.0")

    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.PUBLISHED
    assert result.provider_name == "qdrant"


# --- Sync: deploy_provider ---


@respx.mock
def test_deploy_provider_returns_result() -> None:
    respx.post("http://localhost:8000/providers/installed/pragma/qdrant/deploy").mock(
        return_value=httpx.Response(
            200,
            json={
                "deployment_name": "pragma-qdrant",
                "status": "progressing",
                "available_replicas": 0,
                "ready_replicas": 0,
                "version": "1.2.0",
                "image": "gcr.io/pragmatiks/qdrant:1.2.0",
                "updated_at": None,
                "message": None,
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.deploy_provider("pragma/qdrant", version="1.2.0")

    assert isinstance(result, DeploymentResult)
    assert result.deployment_name == "pragma-qdrant"
    assert result.version == "1.2.0"
    assert result.available_replicas == 0


# --- Sync: get_deployment_status ---


@respx.mock
def test_get_deployment_status_returns_result() -> None:
    respx.get("http://localhost:8000/providers/installed/pragma/qdrant/deployment").mock(
        return_value=httpx.Response(
            200,
            json={
                "deployment_name": "pragma-qdrant",
                "status": "available",
                "available_replicas": 1,
                "ready_replicas": 1,
                "version": "1.2.0",
                "image": "gcr.io/pragmatiks/qdrant:1.2.0",
                "updated_at": "2026-02-21T09:00:00Z",
                "message": None,
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_deployment_status("pragma/qdrant")

    assert isinstance(result, DeploymentResult)
    assert result.deployment_name == "pragma-qdrant"
    assert result.ready_replicas == 1


# --- Async: list_providers ---


@respx.mock
async def test_async_list_providers_returns_paginated_response() -> None:
    respx.get("http://localhost:8000/providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [STORE_PROVIDER_SUMMARY],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.list_providers()

    assert isinstance(result, PaginatedResponse)
    assert result.total == 1
    assert len(result.items) == 1
    assert isinstance(result.items[0], StoreProviderSummary)
    assert result.items[0].name == "qdrant"
    assert result.items[0].trust_tier == TrustTier.OFFICIAL


@respx.mock
async def test_async_list_providers_passes_query_params() -> None:
    route = respx.get("http://localhost:8000/providers").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 10, "offset": 5},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.list_providers(query="qdrant", trust_tier="official", tags=["vector"], limit=10, offset=5)

    request = route.calls[0].request
    assert request.url.params["q"] == "qdrant"
    assert request.url.params["trust_tier"] == "official"
    assert request.url.params["limit"] == "10"
    assert request.url.params["offset"] == "5"
    assert "tags" in str(request.url)


# --- Async: get_provider ---


@respx.mock
async def test_async_get_provider_returns_detail() -> None:
    respx.get("http://localhost:8000/providers/pragma/qdrant").mock(
        return_value=httpx.Response(
            200,
            json={
                "provider": STORE_PROVIDER,
                "versions": [STORE_VERSION],
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_provider("pragma/qdrant")

    assert isinstance(result, StoreProviderDetail)
    assert result.provider.name == "qdrant"
    assert len(result.versions) == 1


@respx.mock
async def test_async_get_provider_raises_on_not_found() -> None:
    respx.get("http://localhost:8000/providers/pragma/nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_provider("pragma/nonexistent")

    assert exc_info.value.response.status_code == 404


# --- Async: install_provider ---


@respx.mock
async def test_async_install_provider_returns_installed() -> None:
    respx.post("http://localhost:8000/providers/install").mock(
        return_value=httpx.Response(201, json=INSTALLED_PROVIDER)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.install_provider("pragma/qdrant", version="1.2.0")

    assert isinstance(result, InstalledProvider)
    assert result.store_provider_name == "qdrant"
    assert result.installed_version == "1.2.0"


@respx.mock
async def test_async_install_provider_raises_on_conflict() -> None:
    respx.post("http://localhost:8000/providers/install").mock(
        return_value=httpx.Response(409, json={"detail": "Already installed"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.install_provider("pragma/qdrant")

    assert exc_info.value.response.status_code == 409


# --- Async: uninstall_provider ---


@respx.mock
async def test_async_uninstall_provider_succeeds() -> None:
    respx.delete("http://localhost:8000/providers/installed/pragma/qdrant").mock(return_value=httpx.Response(204))

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.uninstall_provider("pragma/qdrant")


@respx.mock
async def test_async_uninstall_provider_with_cascade() -> None:
    route = respx.delete("http://localhost:8000/providers/installed/pragma/qdrant").mock(
        return_value=httpx.Response(204)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.uninstall_provider("pragma/qdrant", cascade=True)

    assert route.calls[0].request.url.params["cascade"] == "true"


# --- Async: upgrade_provider ---


@respx.mock
async def test_async_upgrade_provider_returns_installed() -> None:
    upgraded = {**INSTALLED_PROVIDER, "installed_version": "1.3.0"}
    route = respx.post("http://localhost:8000/providers/installed/pragma/qdrant/upgrade").mock(
        return_value=httpx.Response(200, json=upgraded)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.upgrade_provider("pragma/qdrant", target_version="1.3.0")

    assert isinstance(result, InstalledProvider)
    assert result.installed_version == "1.3.0"

    import json

    body = json.loads(route.calls[0].request.content)
    assert "version" in body
    assert body["version"] == "1.3.0"
    assert "target_version" not in body


# --- Async: list_installed_providers ---


@respx.mock
async def test_async_list_installed_providers_returns_summaries() -> None:
    respx.get("http://localhost:8000/providers/installed").mock(
        return_value=httpx.Response(200, json=[INSTALLED_PROVIDER_SUMMARY])
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.list_installed_providers()

    assert len(result) == 1
    assert isinstance(result[0], InstalledProviderSummary)
    assert result[0].store_provider_name == "qdrant"
    assert result[0].upgrade_available is True


# --- Async: publish_provider ---


@respx.mock
async def test_async_publish_provider_returns_version() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.publish_provider("pragma/qdrant", b"tarball-content", "1.2.0", changelog="New stuff")

    assert route.called
    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.BUILDING
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers.get("content-type", "")


@respx.mock
async def test_async_publish_provider_includes_metadata_fields() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.publish_provider(
            "pragma/qdrant",
            b"tarball-content",
            "1.2.0",
            display_name="Qdrant Vector DB",
            description="A vector database provider",
            tags=["database", "vector"],
        )

    assert route.called
    request = route.calls[0].request
    body = request.content.decode("utf-8", errors="replace")
    assert "display_name" in body
    assert "Qdrant Vector DB" in body
    assert "description" in body
    assert "A vector database provider" in body
    assert "tags" in body
    assert '["database", "vector"]' in body


@respx.mock
async def test_async_publish_provider_omits_none_metadata_fields() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/providers/pragma/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.publish_provider("pragma/qdrant", b"tarball-content", "1.2.0")

    assert route.called
    request = route.calls[0].request
    body = request.content.decode("utf-8", errors="replace")
    assert "display_name" not in body
    assert "description" not in body
    assert "tags" not in body


# --- Async: get_publish_status ---


@respx.mock
async def test_async_get_publish_status_returns_version() -> None:
    respx.get("http://localhost:8000/providers/pragma/qdrant/versions/1.2.0/status").mock(
        return_value=httpx.Response(200, json=STORE_VERSION)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_publish_status("pragma/qdrant", "1.2.0")

    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.PUBLISHED
    assert result.provider_name == "qdrant"


# --- Async: deploy_provider ---


@respx.mock
async def test_async_deploy_provider_returns_result() -> None:
    respx.post("http://localhost:8000/providers/installed/pragma/qdrant/deploy").mock(
        return_value=httpx.Response(
            200,
            json={
                "deployment_name": "pragma-qdrant",
                "status": "progressing",
                "available_replicas": 0,
                "ready_replicas": 0,
                "version": "1.2.0",
                "image": "gcr.io/pragmatiks/qdrant:1.2.0",
                "updated_at": None,
                "message": None,
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.deploy_provider("pragma/qdrant", version="1.2.0")

    assert isinstance(result, DeploymentResult)
    assert result.deployment_name == "pragma-qdrant"
    assert result.version == "1.2.0"
    assert result.available_replicas == 0


# --- Async: get_deployment_status ---


@respx.mock
async def test_async_get_deployment_status_returns_result() -> None:
    respx.get("http://localhost:8000/providers/installed/pragma/qdrant/deployment").mock(
        return_value=httpx.Response(
            200,
            json={
                "deployment_name": "pragma-qdrant",
                "status": "available",
                "available_replicas": 1,
                "ready_replicas": 1,
                "version": "1.2.0",
                "image": "gcr.io/pragmatiks/qdrant:1.2.0",
                "updated_at": "2026-02-21T09:00:00Z",
                "message": None,
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_deployment_status("pragma/qdrant")

    assert isinstance(result, DeploymentResult)
    assert result.deployment_name == "pragma-qdrant"
    assert result.ready_replicas == 1
