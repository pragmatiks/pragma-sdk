"""Tests for dead letter SDK client methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from pragma_sdk.client import AsyncPragmaClient, PragmaClient


@respx.mock
def test_list_dead_letter_events_without_filter() -> None:
    """Returns list of dead letter events when no filter provided."""
    route = respx.get("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "evt_1", "provider": "postgres", "error": "Connection failed"},
                {"id": "evt_2", "provider": "redis", "error": "Timeout"},
            ],
        )
    )

    with PragmaClient(auth_token=None) as client:
        events = client.list_dead_letter_events()

    assert route.called
    assert len(events) == 2
    assert events[0]["id"] == "evt_1"
    assert events[1]["provider"] == "redis"


@respx.mock
def test_list_dead_letter_events_with_provider_filter() -> None:
    """Passes provider filter as query parameter."""
    route = respx.get("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "evt_1", "provider": "postgres", "error": "Connection failed"}],
        )
    )

    with PragmaClient(auth_token=None) as client:
        events = client.list_dead_letter_events(provider="postgres")

    assert route.called
    assert route.calls[0].request.url.params["provider"] == "postgres"
    assert len(events) == 1
    assert events[0]["provider"] == "postgres"


@respx.mock
def test_get_dead_letter_event_returns_event_dict() -> None:
    """Returns dead letter event as dict."""
    respx.get("https://api.pragmatiks.io/ops/dead-letter/evt_123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_123",
                "provider": "postgres",
                "error": "Connection failed",
                "payload": {"action": "create"},
            },
        )
    )

    with PragmaClient(auth_token=None) as client:
        event = client.get_dead_letter_event("evt_123")

    assert event["id"] == "evt_123"
    assert event["provider"] == "postgres"
    assert event["payload"]["action"] == "create"


@respx.mock
def test_retry_dead_letter_event_makes_post_returns_none() -> None:
    """Makes POST request and returns None on success."""
    route = respx.post("https://api.pragmatiks.io/ops/dead-letter/evt_123/retry").mock(return_value=httpx.Response(204))

    with PragmaClient(auth_token=None) as client:
        result = client.retry_dead_letter_event("evt_123")

    assert route.called
    assert result is None


@respx.mock
def test_retry_all_dead_letter_events_returns_count() -> None:
    """Returns retried count from response."""
    route = respx.post("https://api.pragmatiks.io/ops/dead-letter/retry-all").mock(
        return_value=httpx.Response(200, json={"retried_count": 5})
    )

    with PragmaClient(auth_token=None) as client:
        count = client.retry_all_dead_letter_events()

    assert route.called
    assert count == 5


@respx.mock
def test_delete_dead_letter_event_makes_delete_returns_none() -> None:
    """Makes DELETE request and returns None on success."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter/evt_123").mock(return_value=httpx.Response(204))

    with PragmaClient(auth_token=None) as client:
        result = client.delete_dead_letter_event("evt_123")

    assert route.called
    assert result is None


@respx.mock
def test_delete_dead_letter_events_with_all_returns_count() -> None:
    """Returns deleted count when all=True."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(200, json={"deleted_count": 10})
    )

    with PragmaClient(auth_token=None) as client:
        count = client.delete_dead_letter_events(all=True)

    assert route.called
    assert route.calls[0].request.url.params["all"] == "true"
    assert count == 10


@respx.mock
def test_delete_dead_letter_events_with_provider_returns_count() -> None:
    """Returns deleted count when provider specified."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(200, json={"deleted_count": 3})
    )

    with PragmaClient(auth_token=None) as client:
        count = client.delete_dead_letter_events(provider="postgres")

    assert route.called
    assert route.calls[0].request.url.params["provider"] == "postgres"
    assert count == 3


def test_delete_dead_letter_events_without_args_raises_value_error() -> None:
    """Raises ValueError when neither provider nor all is specified."""
    with PragmaClient(auth_token=None) as client:
        with pytest.raises(ValueError, match="Must specify either provider or all=True"):
            client.delete_dead_letter_events()


@respx.mock
async def test_async_list_dead_letter_events_without_filter() -> None:
    """Returns list of dead letter events when no filter provided."""
    route = respx.get("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "evt_1", "provider": "postgres", "error": "Connection failed"},
                {"id": "evt_2", "provider": "redis", "error": "Timeout"},
            ],
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        events = await client.list_dead_letter_events()

    assert route.called
    assert len(events) == 2
    assert events[0]["id"] == "evt_1"
    assert events[1]["provider"] == "redis"


@respx.mock
async def test_async_list_dead_letter_events_with_provider_filter() -> None:
    """Passes provider filter as query parameter."""
    route = respx.get("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "evt_1", "provider": "postgres", "error": "Connection failed"}],
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        events = await client.list_dead_letter_events(provider="postgres")

    assert route.called
    assert route.calls[0].request.url.params["provider"] == "postgres"
    assert len(events) == 1
    assert events[0]["provider"] == "postgres"


@respx.mock
async def test_async_get_dead_letter_event_returns_event_dict() -> None:
    """Returns dead letter event as dict."""
    respx.get("https://api.pragmatiks.io/ops/dead-letter/evt_123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_123",
                "provider": "postgres",
                "error": "Connection failed",
                "payload": {"action": "create"},
            },
        )
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        event = await client.get_dead_letter_event("evt_123")

    assert event["id"] == "evt_123"
    assert event["provider"] == "postgres"
    assert event["payload"]["action"] == "create"


@respx.mock
async def test_async_retry_dead_letter_event_makes_post_returns_none() -> None:
    """Makes POST request and returns None on success."""
    route = respx.post("https://api.pragmatiks.io/ops/dead-letter/evt_123/retry").mock(return_value=httpx.Response(204))

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.retry_dead_letter_event("evt_123")

    assert route.called
    assert result is None


@respx.mock
async def test_async_retry_all_dead_letter_events_returns_count() -> None:
    """Returns retried count from response."""
    route = respx.post("https://api.pragmatiks.io/ops/dead-letter/retry-all").mock(
        return_value=httpx.Response(200, json={"retried_count": 5})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        count = await client.retry_all_dead_letter_events()

    assert route.called
    assert count == 5


@respx.mock
async def test_async_delete_dead_letter_event_makes_delete_returns_none() -> None:
    """Makes DELETE request and returns None on success."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter/evt_123").mock(return_value=httpx.Response(204))

    async with AsyncPragmaClient(auth_token=None) as client:
        result = await client.delete_dead_letter_event("evt_123")

    assert route.called
    assert result is None


@respx.mock
async def test_async_delete_dead_letter_events_with_all_returns_count() -> None:
    """Returns deleted count when all=True."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(200, json={"deleted_count": 10})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        count = await client.delete_dead_letter_events(all=True)

    assert route.called
    assert route.calls[0].request.url.params["all"] == "true"
    assert count == 10


@respx.mock
async def test_async_delete_dead_letter_events_with_provider_returns_count() -> None:
    """Returns deleted count when provider specified."""
    route = respx.delete("https://api.pragmatiks.io/ops/dead-letter").mock(
        return_value=httpx.Response(200, json={"deleted_count": 3})
    )

    async with AsyncPragmaClient(auth_token=None) as client:
        count = await client.delete_dead_letter_events(provider="postgres")

    assert route.called
    assert route.calls[0].request.url.params["provider"] == "postgres"
    assert count == 3


async def test_async_delete_dead_letter_events_without_args_raises_value_error() -> None:
    """Raises ValueError when neither provider nor all is specified."""
    async with AsyncPragmaClient(auth_token=None) as client:
        with pytest.raises(ValueError, match="Must specify either provider or all=True"):
            await client.delete_dead_letter_events()
