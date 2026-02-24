"""Tests for store SDK models and client methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from pragma_sdk.client import AsyncPragmaClient, PragmaClient
from pragma_sdk.models import (
    InstalledProvider,
    InstalledProviderSummary,
    PaginatedResponse,
    ResourceTier,
    StoreProviderDetail,
    StoreProviderSummary,
    StoreVersion,
    StoreVersionDetail,
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
    "cloud_build_id": "build-456",
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


# --- Sync: list_store_providers ---


@respx.mock
def test_list_store_providers_returns_paginated_response() -> None:
    respx.get("http://localhost:8000/store/providers").mock(
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
        result = client.list_store_providers()

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
def test_list_store_providers_passes_query_params() -> None:
    route = respx.get("http://localhost:8000/store/providers").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 10, "offset": 5},
        )
    )

    with PragmaClient(auth_token=None) as client:
        client.list_store_providers(q="qdrant", trust_tier="official", tags=["vector"], limit=10, offset=5)

    request = route.calls[0].request
    assert request.url.params["q"] == "qdrant"
    assert request.url.params["trust_tier"] == "official"
    assert request.url.params["limit"] == "10"
    assert request.url.params["offset"] == "5"
    assert "tags" in str(request.url)


# --- Sync: get_store_provider ---


@respx.mock
def test_get_store_provider_returns_detail() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant").mock(
        return_value=httpx.Response(
            200,
            json={
                "provider": STORE_PROVIDER,
                "versions": [STORE_VERSION],
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_store_provider("qdrant")

    assert isinstance(result, StoreProviderDetail)
    assert result.provider.name == "qdrant"
    assert result.provider.readme == "# Qdrant Provider\nVector database."
    assert len(result.versions) == 1
    assert result.versions[0].version == "1.2.0"


@respx.mock
def test_get_store_provider_raises_on_not_found() -> None:
    respx.get("http://localhost:8000/store/providers/nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_store_provider("nonexistent")

    assert exc_info.value.response.status_code == 404


# --- Sync: get_store_version ---


@respx.mock
def test_get_store_version_returns_detail() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant/versions/1.2.0").mock(
        return_value=httpx.Response(
            200,
            json={"version": STORE_VERSION},
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_store_version("qdrant", "1.2.0")

    assert isinstance(result, StoreVersionDetail)
    assert result.version.version == "1.2.0"
    assert result.version.status == VersionStatus.PUBLISHED


# --- Sync: install_store_provider ---


@respx.mock
def test_install_store_provider_returns_installed() -> None:
    respx.post("http://localhost:8000/store/install").mock(return_value=httpx.Response(200, json=INSTALLED_PROVIDER))

    with PragmaClient(auth_token=None) as client:
        result = client.install_store_provider("qdrant", version="1.2.0")

    assert isinstance(result, InstalledProvider)
    assert result.store_provider_name == "qdrant"
    assert result.installed_version == "1.2.0"
    assert result.upgrade_policy == UpgradePolicy.MANUAL
    assert result.resource_tier == ResourceTier.STANDARD


@respx.mock
def test_install_store_provider_raises_on_conflict() -> None:
    respx.post("http://localhost:8000/store/install").mock(
        return_value=httpx.Response(409, json={"detail": "Already installed"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.install_store_provider("qdrant")

    assert exc_info.value.response.status_code == 409


# --- Sync: uninstall_store_provider ---


@respx.mock
def test_uninstall_store_provider_succeeds() -> None:
    respx.delete("http://localhost:8000/store/installed/qdrant").mock(return_value=httpx.Response(204))

    with PragmaClient(auth_token=None) as client:
        client.uninstall_store_provider("qdrant")


@respx.mock
def test_uninstall_store_provider_with_cascade() -> None:
    route = respx.delete("http://localhost:8000/store/installed/qdrant").mock(return_value=httpx.Response(204))

    with PragmaClient(auth_token=None) as client:
        client.uninstall_store_provider("qdrant", cascade=True)

    assert route.calls[0].request.url.params["cascade"] == "true"


# --- Sync: upgrade_store_provider ---


@respx.mock
def test_upgrade_store_provider_returns_installed() -> None:
    upgraded = {**INSTALLED_PROVIDER, "installed_version": "1.3.0"}
    respx.post("http://localhost:8000/store/installed/qdrant/upgrade").mock(
        return_value=httpx.Response(200, json=upgraded)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.upgrade_store_provider("qdrant", version="1.3.0")

    assert isinstance(result, InstalledProvider)
    assert result.installed_version == "1.3.0"


# --- Sync: list_installed_providers ---


@respx.mock
def test_list_installed_providers_returns_summaries() -> None:
    respx.get("http://localhost:8000/store/installed").mock(
        return_value=httpx.Response(200, json=[INSTALLED_PROVIDER_SUMMARY])
    )

    with PragmaClient(auth_token=None) as client:
        result = client.list_installed_providers()

    assert len(result) == 1
    assert isinstance(result[0], InstalledProviderSummary)
    assert result[0].store_provider_name == "qdrant"
    assert result[0].upgrade_available is True
    assert result[0].latest_version == "1.3.0"


# --- Sync: publish_store_provider ---


@respx.mock
def test_publish_store_provider_returns_version() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/store/providers/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.publish_store_provider("qdrant", b"tarball-content", "1.2.0", changelog="New stuff")

    assert route.called
    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.BUILDING
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers.get("content-type", "")


# --- Sync: get_store_build_status ---


@respx.mock
def test_get_store_build_status_returns_version() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant/versions/1.2.0/status").mock(
        return_value=httpx.Response(200, json=STORE_VERSION)
    )

    with PragmaClient(auth_token=None) as client:
        result = client.get_store_build_status("qdrant", "1.2.0")

    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.PUBLISHED
    assert result.provider_name == "qdrant"


# --- Async: list_store_providers ---


@respx.mock
async def test_async_list_store_providers_returns_paginated_response() -> None:
    respx.get("http://localhost:8000/store/providers").mock(
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
        result = await client.list_store_providers()

    assert isinstance(result, PaginatedResponse)
    assert result.total == 1
    assert len(result.items) == 1
    assert isinstance(result.items[0], StoreProviderSummary)
    assert result.items[0].name == "qdrant"
    assert result.items[0].trust_tier == TrustTier.OFFICIAL


@respx.mock
async def test_async_list_store_providers_passes_query_params() -> None:
    route = respx.get("http://localhost:8000/store/providers").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 10, "offset": 5},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.list_store_providers(q="qdrant", trust_tier="official", tags=["vector"], limit=10, offset=5)

    request = route.calls[0].request
    assert request.url.params["q"] == "qdrant"
    assert request.url.params["trust_tier"] == "official"
    assert request.url.params["limit"] == "10"
    assert request.url.params["offset"] == "5"
    assert "tags" in str(request.url)


# --- Async: get_store_provider ---


@respx.mock
async def test_async_get_store_provider_returns_detail() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant").mock(
        return_value=httpx.Response(
            200,
            json={
                "provider": STORE_PROVIDER,
                "versions": [STORE_VERSION],
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_store_provider("qdrant")

    assert isinstance(result, StoreProviderDetail)
    assert result.provider.name == "qdrant"
    assert len(result.versions) == 1


@respx.mock
async def test_async_get_store_provider_raises_on_not_found() -> None:
    respx.get("http://localhost:8000/store/providers/nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_store_provider("nonexistent")

    assert exc_info.value.response.status_code == 404


# --- Async: get_store_version ---


@respx.mock
async def test_async_get_store_version_returns_detail() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant/versions/1.2.0").mock(
        return_value=httpx.Response(
            200,
            json={"version": STORE_VERSION},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_store_version("qdrant", "1.2.0")

    assert isinstance(result, StoreVersionDetail)
    assert result.version.version == "1.2.0"


# --- Async: install_store_provider ---


@respx.mock
async def test_async_install_store_provider_returns_installed() -> None:
    respx.post("http://localhost:8000/store/install").mock(return_value=httpx.Response(200, json=INSTALLED_PROVIDER))

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.install_store_provider("qdrant", version="1.2.0")

    assert isinstance(result, InstalledProvider)
    assert result.store_provider_name == "qdrant"
    assert result.installed_version == "1.2.0"


@respx.mock
async def test_async_install_store_provider_raises_on_conflict() -> None:
    respx.post("http://localhost:8000/store/install").mock(
        return_value=httpx.Response(409, json={"detail": "Already installed"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.install_store_provider("qdrant")

    assert exc_info.value.response.status_code == 409


# --- Async: uninstall_store_provider ---


@respx.mock
async def test_async_uninstall_store_provider_succeeds() -> None:
    respx.delete("http://localhost:8000/store/installed/qdrant").mock(return_value=httpx.Response(204))

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.uninstall_store_provider("qdrant")


@respx.mock
async def test_async_uninstall_store_provider_with_cascade() -> None:
    route = respx.delete("http://localhost:8000/store/installed/qdrant").mock(return_value=httpx.Response(204))

    async with AsyncPragmaClient(auth_token=None) as client:
        await client.uninstall_store_provider("qdrant", cascade=True)

    assert route.calls[0].request.url.params["cascade"] == "true"


# --- Async: upgrade_store_provider ---


@respx.mock
async def test_async_upgrade_store_provider_returns_installed() -> None:
    upgraded = {**INSTALLED_PROVIDER, "installed_version": "1.3.0"}
    respx.post("http://localhost:8000/store/installed/qdrant/upgrade").mock(
        return_value=httpx.Response(200, json=upgraded)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.upgrade_store_provider("qdrant", version="1.3.0")

    assert isinstance(result, InstalledProvider)
    assert result.installed_version == "1.3.0"


# --- Async: list_installed_providers ---


@respx.mock
async def test_async_list_installed_providers_returns_summaries() -> None:
    respx.get("http://localhost:8000/store/installed").mock(
        return_value=httpx.Response(200, json=[INSTALLED_PROVIDER_SUMMARY])
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.list_installed_providers()

    assert len(result) == 1
    assert isinstance(result[0], InstalledProviderSummary)
    assert result[0].store_provider_name == "qdrant"
    assert result[0].upgrade_available is True


# --- Async: publish_store_provider ---


@respx.mock
async def test_async_publish_store_provider_returns_version() -> None:
    building_version = {**STORE_VERSION, "status": "building", "published_at": None}
    route = respx.post("http://localhost:8000/store/providers/qdrant/publish").mock(
        return_value=httpx.Response(202, json=building_version)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.publish_store_provider("qdrant", b"tarball-content", "1.2.0", changelog="New stuff")

    assert route.called
    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.BUILDING
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers.get("content-type", "")


# --- Async: get_store_build_status ---


@respx.mock
async def test_async_get_store_build_status_returns_version() -> None:
    respx.get("http://localhost:8000/store/providers/qdrant/versions/1.2.0/status").mock(
        return_value=httpx.Response(200, json=STORE_VERSION)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.get_store_build_status("qdrant", "1.2.0")

    assert isinstance(result, StoreVersion)
    assert result.status == VersionStatus.PUBLISHED
    assert result.provider_name == "qdrant"
