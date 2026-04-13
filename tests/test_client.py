"""Tests for PragmaClient and AsyncPragmaClient HTTP clients."""

from __future__ import annotations

import httpx
import pytest
import respx
from conftest import TEST_PROJECT_ID, StubConfig, StubResource

from pragma_sdk import (
    AsyncPragmaClient,
    InvalidResourceIdentityError,
    PragmaClient,
    ProjectMismatchError,
)


def test_pragma_client_raises_when_auth_required_but_no_token() -> None:
    """Raises ValueError when require_auth=True and no token available."""
    with pytest.raises(ValueError, match="Authentication required"):
        PragmaClient(require_auth=True)


@respx.mock
def test_pragma_client_is_healthy_returns_true_when_api_ok() -> None:
    """Returns True when API health check succeeds."""
    respx.get("https://api.pragmatiks.io/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))

    with PragmaClient(auth_token=None) as client:
        assert client.is_healthy() is True


@respx.mock
def test_pragma_client_is_healthy_returns_false_on_error() -> None:
    """Returns False when API health check fails."""
    respx.get("https://api.pragmatiks.io/health").mock(return_value=httpx.Response(500, json={"status": "error"}))

    with PragmaClient(auth_token=None) as client:
        assert client.is_healthy() is False


def test_pragma_client_context_manager_closes_client(mocker) -> None:
    """Context manager exit closes the underlying httpx client."""
    mock_close = mocker.patch.object(httpx.Client, "close")

    with PragmaClient(auth_token=None):
        pass

    mock_close.assert_called_once()


def test_pragma_client_close_closes_httpx_client(mocker) -> None:
    """Explicit close() closes the underlying httpx client."""
    mock_close = mocker.patch.object(httpx.Client, "close")

    client = PragmaClient(auth_token=None)
    client.close()

    mock_close.assert_called_once()


def test_project_handle_rejects_empty_project_id() -> None:
    """client.project('') raises InvalidResourceIdentityError."""
    with PragmaClient(auth_token=None) as client:
        with pytest.raises(InvalidResourceIdentityError):
            client.project("")


def test_project_handle_rejects_reserved_separator() -> None:
    """client.project('a::b') raises InvalidResourceIdentityError."""
    with PragmaClient(auth_token=None) as client:
        with pytest.raises(InvalidResourceIdentityError):
            client.project("a::b")


def test_project_handle_exposes_project_id() -> None:
    """ProjectResources.project_id returns the scoped identifier."""
    with PragmaClient(auth_token=None) as client:
        handle = client.project(TEST_PROJECT_ID)
        assert handle.project_id == TEST_PROJECT_ID


@respx.mock
def test_project_list_resources_uses_project_scoped_path() -> None:
    """list_resources routes through /projects/{id}/resources."""
    respx.get(f"https://api.pragmatiks.io/projects/{TEST_PROJECT_ID}/resources").mock(
        return_value=httpx.Response(200, json=[])
    )

    with PragmaClient(auth_token=None) as client:
        resources = client.project(TEST_PROJECT_ID).list_resources()

    assert resources == []


@respx.mock
def test_project_apply_resource_rejects_mismatched_project() -> None:
    """apply_resource raises ProjectMismatchError without calling the API."""
    resource = StubResource(
        project_id="other-project",
        name="my-res",
        config=StubConfig(name="my-res"),
    )

    with PragmaClient(auth_token=None) as client:
        handle = client.project(TEST_PROJECT_ID)
        with pytest.raises(ProjectMismatchError) as exc_info:
            handle.apply_resource(resource)

    assert exc_info.value.expected_project_id == TEST_PROJECT_ID
    assert exc_info.value.actual_project_id == "other-project"


@respx.mock
def test_project_apply_resource_posts_to_scoped_path() -> None:
    """apply_resource posts to /projects/{id}/resources/apply when project matches."""
    route = respx.post(f"https://api.pragmatiks.io/projects/{TEST_PROJECT_ID}/resources/apply").mock(
        return_value=httpx.Response(200, json={"project_id": TEST_PROJECT_ID, "name": "my-res"})
    )

    resource = StubResource(
        project_id=TEST_PROJECT_ID,
        name="my-res",
        config=StubConfig(name="my-res"),
    )

    with PragmaClient(auth_token=None) as client:
        client.project(TEST_PROJECT_ID).apply_resource(resource)

    assert route.called


def test_async_pragma_client_raises_when_auth_required_but_no_token() -> None:
    """Raises ValueError when require_auth=True and no token available."""
    with pytest.raises(ValueError, match="Authentication required"):
        AsyncPragmaClient(require_auth=True)


@respx.mock
async def test_async_pragma_client_is_healthy_returns_true_when_api_ok() -> None:
    """Returns True when API health check succeeds."""
    respx.get("https://api.pragmatiks.io/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))

    async with AsyncPragmaClient(auth_token=None) as client:
        assert await client.is_healthy() is True


@respx.mock
async def test_async_pragma_client_is_healthy_returns_false_on_error() -> None:
    """Returns False when API health check fails."""
    respx.get("https://api.pragmatiks.io/health").mock(return_value=httpx.Response(500, json={"status": "error"}))

    async with AsyncPragmaClient(auth_token=None) as client:
        assert await client.is_healthy() is False


async def test_async_pragma_client_context_manager_closes_client(mocker) -> None:
    """Async context manager exit closes the underlying httpx client."""
    mock_aexit = mocker.patch.object(httpx.AsyncClient, "__aexit__", return_value=None)
    mocker.patch.object(httpx.AsyncClient, "__aenter__", return_value=mocker.MagicMock())

    async with AsyncPragmaClient(auth_token=None):
        pass

    mock_aexit.assert_called_once()


async def test_async_pragma_client_close_calls_aclose(mocker) -> None:
    """Explicit close() calls aclose on the underlying httpx client."""
    mock_aclose = mocker.patch.object(httpx.AsyncClient, "aclose", return_value=None)

    client = AsyncPragmaClient(auth_token=None)
    await client.close()

    mock_aclose.assert_called_once()


@respx.mock
async def test_async_project_apply_resource_rejects_mismatched_project() -> None:
    """Async apply_resource raises ProjectMismatchError before network."""
    resource = StubResource(
        project_id="other-project",
        name="my-res",
        config=StubConfig(name="my-res"),
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        handle = client.project(TEST_PROJECT_ID)
        with pytest.raises(ProjectMismatchError):
            await handle.apply_resource(resource)
