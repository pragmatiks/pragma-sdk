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
    DELETED = "deleted"


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


class CopyStrategy(StrEnum):
    """Strategy for how a resource should be copied.

    Attributes:
        STATELESS: Config-only copy. The resource is duplicated by creating a new
            instance with the same (or modified) configuration. No data migration.
        STATEFUL: Data-bearing copy. The resource needs to clone underlying data
            (e.g., database contents, vector indices) and may produce patches for
            ongoing synchronization.
    """

    STATELESS = "stateless"
    STATEFUL = "stateful"


class CopyContext(BaseModel):
    """Context passed to on_copy describing how and where to copy a resource.

    Attributes:
        tags: Tags to apply to the copied resource (e.g., ["env:feature-348"]).
        target_name: Name for the new copied resource.
        strategy: Copy strategy (stateless or stateful).
        metadata: Additional provider-specific context for the copy operation.
    """

    model_config = {"extra": "forbid"}

    tags: list[str] = []
    target_name: str
    strategy: CopyStrategy = CopyStrategy.STATELESS
    metadata: dict[str, Any] = {}


class CopyResult(BaseModel):
    """Result of an on_copy lifecycle method.

    Attributes:
        config: Configuration dict for the new copied resource.
        outputs: Outputs dict for the new resource, if immediately available.
        tags: Tags to apply to the copied resource. Defaults to the tags from
            the CopyContext if not set.
    """

    model_config = {"extra": "forbid"}

    config: dict[str, Any]
    outputs: dict[str, Any] | None = None
    tags: list[str] | None = None


class CompatibilityConstraint(BaseModel):
    """A single constraint expression for patch eligibility.

    Constraints are evaluated against the resource's current configuration
    to determine whether a patch can be applied.

    Attributes:
        field: Config field name to check (e.g., "postgresql_version").
        operator: Comparison operator (e.g., ">=", "==", "!=", "<", ">", "<=", "in").
        value: Value to compare against. Type depends on operator.
    """

    model_config = {"extra": "forbid"}

    field: str
    operator: Literal["==", "!=", ">", ">=", "<", "<=", "in"]
    value: Any


class PatchDefinition(BaseModel):
    """Definition of a patch to apply to a resource.

    Patches are migration scripts, schema diffs, or configuration changes
    that can be applied to a resource. They include compatibility constraints
    to ensure they are only applied to eligible resources.

    Attributes:
        patch_id: Unique identifier for this patch.
        description: Human-readable description of what this patch does.
        constraints: Compatibility constraints the resource must satisfy.
        payload: Patch-specific data (e.g., migration script, schema diff).
        metadata: Additional context about the patch (author, source, etc.).
    """

    model_config = {"extra": "forbid"}

    patch_id: str
    description: str = ""
    constraints: list[CompatibilityConstraint] = []
    payload: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


class PatchResult(BaseModel):
    """Result of an on_patch lifecycle method.

    Attributes:
        success: Whether the patch was applied successfully.
        message: Human-readable description of the outcome.
        modified_config: Updated config fields after patching, if any.
        modified_outputs: Updated output fields after patching, if any.
    """

    model_config = {"extra": "forbid"}

    success: bool
    message: str = ""
    modified_config: dict[str, Any] | None = None
    modified_outputs: dict[str, Any] | None = None
