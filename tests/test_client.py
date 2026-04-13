"""Tests for PragmaClient and AsyncPragmaClient HTTP clients."""

from __future__ import annotations

import httpx
import pytest
import respx
from conftest import StubConfig, StubResource

from pragma_sdk import LifecycleState
from pragma_sdk.client import AsyncPragmaClient, PragmaClient


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


@respx.mock
def test_pragma_client_project_list_resources_returns_dicts_without_model() -> None:
    """Returns list of dicts when no model parameter provided."""
    respx.get("https://api.pragmatiks.io/projects/proj-test/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "db1", "config": {}, "lifecycle_state": "ready"},
                {"name": "db2", "config": {}, "lifecycle_state": "pending"},
            ],
        )
    )

    with PragmaClient(auth_token=None) as client:
        resources = client.project("proj-test").list_resources()

    assert len(resources) == 2
    assert resources[0]["name"] == "db1"
    assert resources[0]["lifecycle_state"] == "ready"
    assert resources[1]["lifecycle_state"] == "pending"


@respx.mock
def test_pragma_client_project_list_resources_returns_typed_resources_with_model() -> None:
    """Returns list of typed Resource instances when model parameter provided."""
    respx.get("https://api.pragmatiks.io/projects/proj-test/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"project_id": "proj-test", "name": "db1", "config": {"name": "db1"}, "lifecycle_state": "ready"},
                {"project_id": "proj-test", "name": "db2", "config": {"name": "db2"}, "lifecycle_state": "pending"},
            ],
        )
    )

    with PragmaClient(auth_token=None) as client:
        resources = client.project("proj-test").list_resources(model=StubResource)

    assert len(resources) == 2
    assert isinstance(resources[0], StubResource)
    assert resources[0].name == "db1"
    assert resources[0].lifecycle_state == LifecycleState.READY
    assert resources[1].lifecycle_state == LifecycleState.PENDING


@respx.mock
def test_pragma_client_project_get_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "ready"},
        )
    )

    with PragmaClient(auth_token=None) as client:
        resource = client.project("proj-test").get_resource("postgres", "database", "mydb")

    assert resource["name"] == "mydb"
    assert resource["lifecycle_state"] == "ready"


@respx.mock
def test_pragma_client_project_get_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "test", "resource": "stub", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "ready",
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        resource = client.project("proj-test").get_resource("test", "stub", "mydb", model=StubResource)

    assert isinstance(resource, StubResource)
    assert resource.name == "mydb"
    assert resource.lifecycle_state == LifecycleState.READY


@respx.mock
def test_pragma_client_project_apply_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.post("https://api.pragmatiks.io/projects/proj-test/resources/apply").mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "pending"},
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.project("proj-test").apply_resource({"project_id": "proj-test", "name": "mydb", "config": {}})

    assert result["name"] == "mydb"
    assert result["lifecycle_state"] == "pending"


@respx.mock
def test_pragma_client_project_apply_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.post("https://api.pragmatiks.io/projects/proj-test/resources/apply").mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "pending",
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        stub = StubResource(project_id="proj-test", name="mydb", config=StubConfig(name="mydb"))
        result = client.project("proj-test").apply_resource(stub, model=StubResource)

    assert isinstance(result, StubResource)
    assert result.name == "mydb"
    assert result.lifecycle_state == LifecycleState.PENDING


def test_pragma_client_project_apply_resource_rejects_mismatched_project() -> None:
    """Rejects resources whose project_id does not match the scoped project."""
    with PragmaClient(auth_token=None) as client:
        stub = StubResource(project_id="other-proj", name="mydb", config=StubConfig(name="mydb"))

        with pytest.raises(ValueError, match="does not match scoped project"):
            client.project("proj-test").apply_resource(stub)


@respx.mock
def test_pragma_client_project_deactivate_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.post(
        "https://api.pragmatiks.io/projects/proj-test/resources/deactivate",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "deleting"},
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.project("proj-test").deactivate_resource("postgres", "database", "mydb")

    assert result["name"] == "mydb"
    assert result["lifecycle_state"] == "deleting"


@respx.mock
def test_pragma_client_project_deactivate_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.post(
        "https://api.pragmatiks.io/projects/proj-test/resources/deactivate",
        params={"provider": "test", "resource": "stub", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "deleting",
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        result = client.project("proj-test").deactivate_resource("test", "stub", "mydb", model=StubResource)

    assert isinstance(result, StubResource)
    assert result.name == "mydb"
    assert result.lifecycle_state == LifecycleState.DELETING


@respx.mock
def test_pragma_client_project_delete_resource_sends_delete_request() -> None:
    """Sends DELETE to the scoped by-name path and returns None."""
    route = respx.delete(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(return_value=httpx.Response(200, json=None))

    with PragmaClient(auth_token=None) as client:
        result = client.project("proj-test").delete_resource("postgres", "database", "mydb")

    assert result is None
    assert route.called


@respx.mock
def test_pragma_client_project_raises_on_not_found() -> None:
    """Raises HTTPStatusError when a scoped resource is not found."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "test", "resource": "db", "name": "notfound"},
    ).mock(return_value=httpx.Response(404, json={"detail": "Not found"}))

    with PragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.project("proj-test").get_resource("test", "db", "notfound")

    assert exc_info.value.response.status_code == 404


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


@respx.mock
async def test_async_pragma_client_project_list_resources_returns_dicts_without_model() -> None:
    """Returns list of dicts when no model parameter provided."""
    respx.get("https://api.pragmatiks.io/projects/proj-test/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "db1", "config": {}, "lifecycle_state": "ready"},
                {"name": "db2", "config": {}, "lifecycle_state": "pending"},
            ],
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        resources = await client.project("proj-test").list_resources()

    assert len(resources) == 2
    assert resources[0]["name"] == "db1"
    assert resources[0]["lifecycle_state"] == "ready"
    assert resources[1]["lifecycle_state"] == "pending"


@respx.mock
async def test_async_pragma_client_project_list_resources_returns_typed_resources_with_model() -> None:
    """Returns list of typed Resource instances when model parameter provided."""
    respx.get("https://api.pragmatiks.io/projects/proj-test/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"project_id": "proj-test", "name": "db1", "config": {"name": "db1"}, "lifecycle_state": "ready"},
                {"project_id": "proj-test", "name": "db2", "config": {"name": "db2"}, "lifecycle_state": "pending"},
            ],
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        resources = await client.project("proj-test").list_resources(model=StubResource)

    assert len(resources) == 2
    assert isinstance(resources[0], StubResource)
    assert resources[0].name == "db1"
    assert resources[0].lifecycle_state == LifecycleState.READY
    assert resources[1].lifecycle_state == LifecycleState.PENDING


@respx.mock
async def test_async_pragma_client_project_get_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "ready"},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        resource = await client.project("proj-test").get_resource("postgres", "database", "mydb")

    assert resource["name"] == "mydb"
    assert resource["lifecycle_state"] == "ready"


@respx.mock
async def test_async_pragma_client_project_get_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "test", "resource": "stub", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "ready",
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        resource = await client.project("proj-test").get_resource("test", "stub", "mydb", model=StubResource)

    assert isinstance(resource, StubResource)
    assert resource.name == "mydb"
    assert resource.lifecycle_state == LifecycleState.READY


@respx.mock
async def test_async_pragma_client_project_apply_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.post("https://api.pragmatiks.io/projects/proj-test/resources/apply").mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "pending"},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.project("proj-test").apply_resource({
            "project_id": "proj-test",
            "name": "mydb",
            "config": {},
        })

    assert result["name"] == "mydb"
    assert result["lifecycle_state"] == "pending"


@respx.mock
async def test_async_pragma_client_project_apply_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.post("https://api.pragmatiks.io/projects/proj-test/resources/apply").mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "pending",
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        stub = StubResource(project_id="proj-test", name="mydb", config=StubConfig(name="mydb"))
        result = await client.project("proj-test").apply_resource(stub, model=StubResource)

    assert isinstance(result, StubResource)
    assert result.name == "mydb"
    assert result.lifecycle_state == LifecycleState.PENDING


async def test_async_pragma_client_project_apply_resource_rejects_mismatched_project() -> None:
    """Rejects resources whose project_id does not match the scoped project."""
    async with AsyncPragmaClient(auth_token=None) as client:
        stub = StubResource(project_id="other-proj", name="mydb", config=StubConfig(name="mydb"))

        with pytest.raises(ValueError, match="does not match scoped project"):
            await client.project("proj-test").apply_resource(stub)


@respx.mock
async def test_async_pragma_client_project_deactivate_resource_returns_dict_without_model() -> None:
    """Returns dict when no model parameter provided."""
    respx.post(
        "https://api.pragmatiks.io/projects/proj-test/resources/deactivate",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"name": "mydb", "config": {}, "lifecycle_state": "deleting"},
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.project("proj-test").deactivate_resource("postgres", "database", "mydb")

    assert result["name"] == "mydb"
    assert result["lifecycle_state"] == "deleting"


@respx.mock
async def test_async_pragma_client_project_deactivate_resource_returns_typed_resource_with_model() -> None:
    """Returns typed Resource instance when model parameter provided."""
    respx.post(
        "https://api.pragmatiks.io/projects/proj-test/resources/deactivate",
        params={"provider": "test", "resource": "stub", "name": "mydb"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "project_id": "proj-test",
                "name": "mydb",
                "config": {"name": "mydb"},
                "lifecycle_state": "deleting",
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.project("proj-test").deactivate_resource("test", "stub", "mydb", model=StubResource)

    assert isinstance(result, StubResource)
    assert result.name == "mydb"
    assert result.lifecycle_state == LifecycleState.DELETING


@respx.mock
async def test_async_pragma_client_project_delete_resource_sends_delete_request() -> None:
    """Sends DELETE to the scoped by-name path and returns None."""
    route = respx.delete(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "postgres", "resource": "database", "name": "mydb"},
    ).mock(return_value=httpx.Response(200, json=None))

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.project("proj-test").delete_resource("postgres", "database", "mydb")

    assert result is None
    assert route.called


@respx.mock
async def test_async_pragma_client_project_raises_on_not_found() -> None:
    """Raises HTTPStatusError when a scoped resource is not found."""
    respx.get(
        "https://api.pragmatiks.io/projects/proj-test/resources/by-name",
        params={"provider": "test", "resource": "db", "name": "notfound"},
    ).mock(return_value=httpx.Response(404, json={"detail": "Not found"}))

    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.project("proj-test").get_resource("test", "db", "notfound")

    assert exc_info.value.response.status_code == 404


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
