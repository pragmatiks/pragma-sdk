"""Shared type definitions for the Pragmatiks SDK.

This module contains pure data types (enums, models) that are used across
multiple SDK modules. It has no dependencies on other SDK modules to avoid
circular imports.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class LifecycleState(StrEnum):
    """Lifecycle states for resources."""

    DRAFT = "draft"
    WAITING = "waiting"
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class LogEntry(BaseModel):
    """A single log entry from a resource."""

    timestamp: datetime
    level: Literal["debug", "info", "warn", "error"]
    message: str
    metadata: dict[str, Any] | None = None


class HealthStatus(BaseModel):
    """Health status of a resource."""

    status: Literal["healthy", "unhealthy", "degraded"]
    message: str | None = None
    details: dict[str, Any] | None = None
