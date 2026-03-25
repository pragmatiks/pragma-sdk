"""Unit tests for SDK runtime context."""

from __future__ import annotations

from typing import Any

import pytest

from pragma_sdk import LifecycleState
from pragma_sdk.context import (
    _provider_name,
    get_current_resource_owner,
    get_provider_name,
    get_runtime_context,
    reset_current_resource_owner,
    reset_provider_name,
    reset_runtime_context,
    set_current_resource_owner,
    set_provider_name,
    set_runtime_context,
    wait_for_resource_state,
)
from pragma_sdk.models.references import OwnerReference


class MockRuntimeContext:
    """Mock runtime context for testing."""

    def __init__(self, return_value: dict[str, Any] | None = None):
        self.return_value = return_value or {"lifecycle_state": "ready"}
        self.wait_calls: list[tuple[str, LifecycleState, float]] = []

    async def wait_for_state(
        self,
        resource_id: str,
        target_state: LifecycleState,
        timeout: float,
    ) -> dict[str, Any]:
        self.wait_calls.append((resource_id, target_state, timeout))
        return self.return_value


def test_get_runtime_context_returns_none_when_not_set():
    """get_runtime_context() returns None when no context is set."""
    assert get_runtime_context() is None


def test_set_runtime_context_returns_token():
    """set_runtime_context() returns a token for resetting."""
    ctx = MockRuntimeContext()
    token = set_runtime_context(ctx)
    assert token is not None
    reset_runtime_context(token)


def test_get_runtime_context_returns_set_context():
    """get_runtime_context() returns the context that was set."""
    ctx = MockRuntimeContext()
    token = set_runtime_context(ctx)
    try:
        assert get_runtime_context() is ctx
    finally:
        reset_runtime_context(token)


def test_reset_runtime_context_clears_context():
    """reset_runtime_context() clears the context."""
    ctx = MockRuntimeContext()
    token = set_runtime_context(ctx)
    reset_runtime_context(token)
    assert get_runtime_context() is None


@pytest.mark.asyncio
async def test_wait_for_resource_state_raises_when_no_context():
    """wait_for_resource_state() raises RuntimeError when called without context."""
    with pytest.raises(RuntimeError, match="must be called from within a provider lifecycle handler"):
        await wait_for_resource_state("test/type/name", LifecycleState.READY)


@pytest.mark.asyncio
async def test_wait_for_resource_state_delegates_to_context():
    """wait_for_resource_state() delegates to the runtime context."""
    ctx = MockRuntimeContext({"lifecycle_state": "ready", "outputs": {"url": "http://test"}})
    token = set_runtime_context(ctx)
    try:
        result = await wait_for_resource_state(
            "provider/type/name",
            LifecycleState.READY,
            timeout=30.0,
        )
        assert result == {"lifecycle_state": "ready", "outputs": {"url": "http://test"}}
        assert len(ctx.wait_calls) == 1
        assert ctx.wait_calls[0] == ("provider/type/name", LifecycleState.READY, 30.0)
    finally:
        reset_runtime_context(token)


@pytest.mark.asyncio
async def test_wait_for_resource_state_uses_default_timeout():
    """wait_for_resource_state() uses default timeout of 60.0."""
    ctx = MockRuntimeContext()
    token = set_runtime_context(ctx)
    try:
        await wait_for_resource_state("test/type/name", LifecycleState.READY)
        assert ctx.wait_calls[0][2] == 60.0
    finally:
        reset_runtime_context(token)


def test_get_current_resource_owner_returns_none_when_not_set():
    """get_current_resource_owner() returns None when no owner is set."""
    assert get_current_resource_owner() is None


def test_set_current_resource_owner_returns_token():
    """set_current_resource_owner() returns a token for resetting."""
    owner = OwnerReference(provider="test", resource="database", name="my-db")
    token = set_current_resource_owner(owner)
    assert token is not None
    reset_current_resource_owner(token)


def test_get_current_resource_owner_returns_set_owner():
    """get_current_resource_owner() returns the owner that was set."""
    owner = OwnerReference(provider="test", resource="database", name="my-db")
    token = set_current_resource_owner(owner)
    try:
        assert get_current_resource_owner() is owner
    finally:
        reset_current_resource_owner(token)


def test_reset_current_resource_owner_clears_owner():
    """reset_current_resource_owner() clears the owner."""
    owner = OwnerReference(provider="test", resource="database", name="my-db")
    token = set_current_resource_owner(owner)
    reset_current_resource_owner(token)
    assert get_current_resource_owner() is None


def test_get_provider_name_returns_none_when_not_set():
    """get_provider_name() returns None when no provider name is set."""
    clear_token = _provider_name.set(None)
    try:
        assert get_provider_name() is None
    finally:
        _provider_name.reset(clear_token)


def test_set_provider_name_returns_token():
    """set_provider_name() returns a token for resetting."""
    clear_token = _provider_name.set(None)
    try:
        token = set_provider_name("pragmatiks/gcp")
        assert token is not None
        reset_provider_name(token)
    finally:
        _provider_name.reset(clear_token)


def test_get_provider_name_returns_set_value():
    """get_provider_name() returns the provider name that was set."""
    clear_token = _provider_name.set(None)
    try:
        token = set_provider_name("pragmatiks/gcp")
        try:
            assert get_provider_name() == "pragmatiks/gcp"
        finally:
            reset_provider_name(token)
    finally:
        _provider_name.reset(clear_token)


def test_reset_provider_name_clears_value():
    """reset_provider_name() clears the provider name."""
    clear_token = _provider_name.set(None)
    try:
        token = set_provider_name("pragmatiks/gcp")
        reset_provider_name(token)
        assert get_provider_name() is None
    finally:
        _provider_name.reset(clear_token)
