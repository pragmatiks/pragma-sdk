"""API response models for build, deployment, provider, and user operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

from pragma_sdk.models.enums import (
    BuildStatus,
    DeploymentStatus,
    EventType,
    OrganizationStatus,
    ResponseStatus,
    TeardownAction,
)
from pragma_sdk.types import LifecycleState


class BuildInfo(BaseModel):
    """Build information for a provider version.

    Attributes:
        provider_id: Provider identifier.
        version: CalVer version string (YYYYMMDD.HHMMSS).
        status: Current build status.
        error_message: Error message (set on failure).
        created_at: When the build was created.
    """

    provider_id: str
    version: str
    status: BuildStatus
    error_message: str | None = None
    created_at: datetime


class PushResult(BaseModel):
    """Result from pushing provider code to start a build.

    Attributes:
        version: CalVer version for the build (YYYYMMDD.HHMMSS).
        status: Initial build status (typically pending).
        message: Status message from the API.
    """

    version: str
    status: BuildStatus
    message: str


class DeploymentResult(BaseModel):
    """Result of a deployment operation (deploy/rollback).

    Contains internal K8s details needed for deployment commands.

    Attributes:
        deployment_name: Name of the Kubernetes Deployment.
        status: Current deployment status.
        available_replicas: Number of available replicas.
        ready_replicas: Number of ready replicas.
        version: Deployed version (CalVer format YYYYMMDD.HHMMSS).
        image: Container image reference (internal, may not be exposed).
        updated_at: Last update timestamp from deployment conditions.
        message: Status message or error details.
    """

    deployment_name: str
    status: DeploymentStatus
    available_replicas: int = 0
    ready_replicas: int = 0
    version: str | None = None
    image: str | None = None
    updated_at: datetime | None = None
    message: str | None = None


class ProviderStatus(BaseModel):
    """User-facing provider deployment status.

    Minimal representation without internal K8s details like replica
    counts, deployment names, or container images.

    Attributes:
        status: Current deployment status.
        version: CalVer version string of deployed build.
        updated_at: Last update timestamp.
        healthy: Whether the provider is healthy (available with ready replicas).
    """

    status: DeploymentStatus
    version: str | None = None
    updated_at: datetime | None = None
    healthy: bool = False


class ProviderInfo(BaseModel):
    """Provider information from API list endpoint.

    Attributes:
        provider_id: Unique identifier for the provider.
        current_version: CalVer of currently deployed build (None if never deployed).
        deployment_status: Current deployment status (None if not deployed).
        updated_at: Timestamp of last provider update (typically last deployment).
    """

    provider_id: str
    current_version: str | None = None
    deployment_status: DeploymentStatus | None = None
    updated_at: datetime | None = None


class ProviderDeleteResult(BaseModel):
    """User-facing result of a provider delete operation.

    Minimal representation without internal infrastructure details.

    Attributes:
        provider_id: Provider that was deleted.
        deployment_deleted: Whether the running deployment was removed.
        resources_deleted: Number of resources deleted (if cascade was used).
    """

    provider_id: str
    deployment_deleted: bool = False
    resources_deleted: int = 0


class ProviderResponse(BaseModel):
    """Provider response reporting the outcome of a lifecycle event."""

    event_id: str
    event_type: EventType
    resource_id: str
    organization_id: str
    status: ResponseStatus
    outputs: dict | None = None
    error: str | None = None
    timestamp: datetime


class ResourceSchema(BaseModel):
    """Metadata about a registered resource type."""

    provider: str
    resource: str
    config_schema: dict[str, Any] | None = None
    outputs_schema: dict[str, Any] = PydanticField(default_factory=dict)
    description: str | None = None
    tags: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TeardownImpact(BaseModel):
    """One resource a teardown reaches, and what happens to it.

    Attributes:
        id: Resource identity in ``provider/resource/name`` form.
        name: Resource instance name.
        provider: Provider that manages the resource.
        resource: Resource type name.
        lifecycle_state: State the resource is in before the teardown runs.
        action: What the teardown does to the resource.
    """

    id: str
    name: str
    provider: str
    resource: str
    lifecycle_state: LifecycleState
    action: TeardownAction


class TeardownResponse(BaseModel):
    """A deactivated or removed resource, with everything the teardown reaches.

    Mirrors the API response: the target resource's own fields sit alongside
    the impact list. Resource fields other than ``lifecycle_state`` are kept as
    model extras and reachable through ``model_extra``.

    Attributes:
        lifecycle_state: State the target resource is in after the call, or its
            untouched state for a dry run.
        impact: Resources the teardown reaches, the requested one first. This
            is what the pre-flight walk predicted, not a record of what
            happened: a write landing in between can still divert an individual
            resource on a real call.
        dry_run: Whether the call only previewed the teardown.
    """

    model_config = ConfigDict(extra="allow")

    lifecycle_state: LifecycleState
    impact: list[TeardownImpact]
    dry_run: bool


class LifecycleEventFrame(BaseModel):
    """One frame from the API's lifecycle event stream.

    The API streams the full resource record after each state transition with
    the transition metadata folded in. Resource fields the SDK does not declare
    are kept as model extras and reachable through ``model_extra``.

    Attributes:
        id: Resource identity in ``provider/resource/name`` form.
        project_id: Project that owns the resource.
        provider: Provider that manages the resource.
        resource: Resource type name.
        name: Resource instance name.
        lifecycle_state: State the resource is in after the transition.
        from_state: State before the transition, absent when the API pushed the
            record without transition metadata.
        event_type: Lifecycle event that caused the transition.
        action: Action the API attached to the notification.
        error: Error message when the resource is FAILED.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    project_id: str
    provider: str
    resource: str
    name: str
    lifecycle_state: LifecycleState
    from_state: LifecycleState | None = None
    event_type: str | None = None
    action: str | None = None
    error: str | None = None


class Organization(BaseModel):
    """Organization details.

    Attributes:
        organization_id: Unique identifier for the organization.
        name: Display name of the organization.
        slug: URL-friendly identifier for the organization.
        status: Lifecycle status of the organization.
        created_at: When the organization was created.
        updated_at: When the organization was last updated.
    """

    organization_id: str
    name: str
    slug: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime


class UserInfo(BaseModel):
    """Current user information from authentication.

    Attributes:
        user_id: Unique identifier from Clerk authentication.
        email: User's primary email address (None if not set).
        organization_id: Clerk organization identifier.
        organization_name: Name of the user's organization (None if not available).
    """

    user_id: str
    email: str | None = None
    organization_id: str
    organization_name: str | None = None
