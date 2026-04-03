"""Tests for organization management client methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from pragma_sdk.client import AsyncPragmaClient, PragmaClient
from pragma_sdk.models import Organization, OrganizationStatus


ORGANIZATION_FIXTURE = {
    "organization_id": "org_123",
    "name": "Acme Corp",
    "slug": "acme-corp",
    "status": "active",
    "created_at": "2026-01-15T10:30:00Z",
    "updated_at": "2026-03-10T14:00:00Z",
}

ORGANIZATION_FIXTURE_2 = {
    "organization_id": "org_456",
    "name": "Widgets Inc",
    "slug": "widgets-inc",
    "status": "deactivating",
    "created_at": "2026-02-20T08:00:00Z",
    "updated_at": "2026-03-15T12:00:00Z",
}


@respx.mock
def test_list_organizations_returns_typed_list() -> None:
    """Returns list of Organization instances."""
    route = respx.get("https://api.pragmatiks.io/organizations").mock(
        return_value=httpx.Response(
            200,
            json=[ORGANIZATION_FIXTURE, ORGANIZATION_FIXTURE_2],
        )
    )

    with PragmaClient(auth_token=None) as client:
        organizations = client.list_organizations()

    assert route.called
    assert len(organizations) == 2
    assert isinstance(organizations[0], Organization)
    assert organizations[0].organization_id == "org_123"
    assert organizations[0].name == "Acme Corp"
    assert organizations[0].slug == "acme-corp"
    assert organizations[0].status == OrganizationStatus.ACTIVE
    assert isinstance(organizations[1], Organization)
    assert organizations[1].status == OrganizationStatus.DEACTIVATING


@respx.mock
def test_list_organizations_returns_empty_list() -> None:
    """Returns empty list when no organizations exist."""
    respx.get("https://api.pragmatiks.io/organizations").mock(return_value=httpx.Response(200, json=[]))

    with PragmaClient(auth_token=None) as client:
        organizations = client.list_organizations()

    assert organizations == []


@respx.mock
def test_get_organization_returns_typed_instance() -> None:
    """Returns Organization instance for valid ID."""
    route = respx.get("https://api.pragmatiks.io/organizations/org_123").mock(
        return_value=httpx.Response(200, json=ORGANIZATION_FIXTURE)
    )

    with PragmaClient(auth_token=None) as client:
        organization = client.get_organization("org_123")

    assert route.called
    assert isinstance(organization, Organization)
    assert organization.organization_id == "org_123"
    assert organization.name == "Acme Corp"
    assert organization.slug == "acme-corp"
    assert organization.status == OrganizationStatus.ACTIVE


@respx.mock
def test_get_organization_raises_on_not_found() -> None:
    """Raises HTTPStatusError when organization not found."""
    respx.get("https://api.pragmatiks.io/organizations/org_nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_organization("org_nonexistent")

    assert exc_info.value.response.status_code == 404


@respx.mock
def test_cleanup_organization_makes_post_returns_none() -> None:
    """Makes POST request and returns None on success."""
    route = respx.post("https://api.pragmatiks.io/organizations/org_123/cleanup").mock(
        return_value=httpx.Response(202, json={"status": "cleanup initiated"})
    )

    with PragmaClient(auth_token=None) as client:
        result = client.cleanup_organization("org_123")

    assert route.called
    assert result is None


@respx.mock
def test_cleanup_organization_raises_on_not_found() -> None:
    """Raises HTTPStatusError when organization not found."""
    respx.post("https://api.pragmatiks.io/organizations/org_nonexistent/cleanup").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.cleanup_organization("org_nonexistent")

    assert exc_info.value.response.status_code == 404


@respx.mock
async def test_async_list_organizations_returns_typed_list() -> None:
    """Returns list of Organization instances."""
    route = respx.get("https://api.pragmatiks.io/organizations").mock(
        return_value=httpx.Response(
            200,
            json=[ORGANIZATION_FIXTURE, ORGANIZATION_FIXTURE_2],
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        organizations = await client.list_organizations()

    assert route.called
    assert len(organizations) == 2
    assert isinstance(organizations[0], Organization)
    assert organizations[0].organization_id == "org_123"
    assert organizations[0].name == "Acme Corp"
    assert organizations[0].slug == "acme-corp"
    assert organizations[0].status == OrganizationStatus.ACTIVE
    assert isinstance(organizations[1], Organization)
    assert organizations[1].status == OrganizationStatus.DEACTIVATING


@respx.mock
async def test_async_list_organizations_returns_empty_list() -> None:
    """Returns empty list when no organizations exist."""
    respx.get("https://api.pragmatiks.io/organizations").mock(return_value=httpx.Response(200, json=[]))

    async with AsyncPragmaClient(auth_token=None) as client:
        organizations = await client.list_organizations()

    assert organizations == []


@respx.mock
async def test_async_get_organization_returns_typed_instance() -> None:
    """Returns Organization instance for valid ID."""
    route = respx.get("https://api.pragmatiks.io/organizations/org_123").mock(
        return_value=httpx.Response(200, json=ORGANIZATION_FIXTURE)
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        organization = await client.get_organization("org_123")

    assert route.called
    assert isinstance(organization, Organization)
    assert organization.organization_id == "org_123"
    assert organization.name == "Acme Corp"
    assert organization.slug == "acme-corp"
    assert organization.status == OrganizationStatus.ACTIVE


@respx.mock
async def test_async_get_organization_raises_on_not_found() -> None:
    """Raises HTTPStatusError when organization not found."""
    respx.get("https://api.pragmatiks.io/organizations/org_nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_organization("org_nonexistent")

    assert exc_info.value.response.status_code == 404


@respx.mock
async def test_async_cleanup_organization_makes_post_returns_none() -> None:
    """Makes POST request and returns None on success."""
    route = respx.post("https://api.pragmatiks.io/organizations/org_123/cleanup").mock(
        return_value=httpx.Response(202, json={"status": "cleanup initiated"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.cleanup_organization("org_123")

    assert route.called
    assert result is None


@respx.mock
async def test_async_cleanup_organization_raises_on_not_found() -> None:
    """Raises HTTPStatusError when organization not found."""
    respx.post("https://api.pragmatiks.io/organizations/org_nonexistent/cleanup").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.cleanup_organization("org_nonexistent")

    assert exc_info.value.response.status_code == 404
