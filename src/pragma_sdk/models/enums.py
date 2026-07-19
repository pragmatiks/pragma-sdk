"""Enumeration types for the Pragma SDK."""

from __future__ import annotations

from enum import StrEnum


class BuildStatus(StrEnum):
    """Status of a BuildKit build job."""

    PENDING = "pending"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"


class DeploymentStatus(StrEnum):
    """Status of a provider deployment."""

    PENDING = "pending"
    PROGRESSING = "progressing"
    AVAILABLE = "available"
    FAILED = "failed"


class EventType(StrEnum):
    """Resource lifecycle event type."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    COPY = "COPY"
    PATCH = "PATCH"
    MIGRATE_UP = "MIGRATE_UP"
    MIGRATE_DOWN = "MIGRATE_DOWN"


class ResponseStatus(StrEnum):
    """Provider response status: SUCCESS or FAILURE."""

    SUCCESS = "success"
    FAILURE = "failure"


class VersionStatus(StrEnum):
    """Build/publish status for a provider version."""

    BUILDING = "building"
    PUBLISHED = "published"
    FAILED = "failed"
    YANKED = "yanked"


class UpgradePolicy(StrEnum):
    """Upgrade policy for installed providers."""

    AUTO = "auto"
    MANUAL = "manual"


class ProviderScope(StrEnum):
    """Scope of a provider in the catalog."""

    PUBLIC = "public"
    TENANT = "tenant"


class ResourceTier(StrEnum):
    """Resource tier for installed providers."""

    FREE = "free"
    STANDARD = "standard"
    PERFORMANCE = "performance"


class OrganizationStatus(StrEnum):
    """Lifecycle status of an organization.

    Mirrors the API's organization lifecycle: an organization is created in
    ``BOOTSTRAPPING`` while the bootstrap worker provisions its tenant
    namespace, reaches ``READY`` once usable, or lands in ``BOOTSTRAP_FAILED``
    if provisioning exhausts its retries. ``DEACTIVATING`` and ``DELETED``
    cover teardown.
    """

    BOOTSTRAPPING = "bootstrapping"
    READY = "ready"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    DEACTIVATING = "deactivating"
    DELETED = "deleted"


class AgentInstanceStatus(StrEnum):
    """Lifecycle status of an agent instance."""

    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class TaskStatus(StrEnum):
    """Status of an agent task."""

    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    RUNNING = "running"
    REVIEW = "review"
    DONE = "done"


class TaskSource(StrEnum):
    """Origin of an agent task."""

    TRIAGE = "triage"
    CONVERSATION = "conversation"
    MANUAL = "manual"


class AgentLogType(StrEnum):
    """Type of agent log entry."""

    TOOL_CALL = "tool_call"
    REASONING = "reasoning"
    STATE_CHANGE = "state_change"
    INPUT_REQUESTED = "input_requested"
    INPUT_RECEIVED = "input_received"
    ERROR = "error"
    SUCCESS = "success"
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"


class PerformanceProfile(StrEnum):
    """User-facing LLM performance profile selection.

    Identifies which tier of model an organization wants platform agents to use.
    The profile is chosen by the user; the API resolves it to a concrete model
    from the selected provider's catalog entries.

    `PerformanceProfile` (low/medium/high) and `ModelTier` (fast/balanced/reasoning)
    are two orthogonal axes. The org's profile + the agent's tier together select
    a model from the platform catalog.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelTier(StrEnum):
    """Classification of a catalog model's capability tier.

    Tier labels a model in the LLM catalog so the API can pick an appropriate
    concrete model for a requested PerformanceProfile.

    `PerformanceProfile` (low/medium/high) and `ModelTier` (fast/balanced/reasoning)
    are two orthogonal axes. The org's profile + the agent's tier together select
    a model from the platform catalog.
    """

    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
