"""API response models for provider, organization, and user operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as PydanticField

from pragma_sdk.models.enums import (
    MemberRole,
    OrganizationStatus,
    ProviderFailureCode,
    ResponseStatus,
    TeardownAction,
)
from pragma_sdk.protocol.frames import CHILDREN_PENDING_WITH_OUTPUTS_MESSAGE
from pragma_sdk.types import EventType, LifecycleState


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
    """Provider response reporting the outcome of a lifecycle event.

    Attributes:
        event_id: Id of the lifecycle event answered.
        event_type: Lifecycle event answered.
        resource_id: Canonical id of the resource the event acts on.
        organization_id: Organization owning the resource.
        provider_version: Version of the provider that ran the operation and
            built the reply; the platform records it with the outputs of a
            success.
        status: Whether the event succeeded.
        outputs: Outputs of a successful create or update, or ``None``.
        children_pending: Whether a successful create or update waits for
            the resources its operation applied to be ready; such a response
            carries no outputs.
        error: Why the event failed, or ``None``.
        failure_code: Kind of a failure whose reason the platform words
            itself, or ``None`` for any other failure and for a success.
        timestamp: When the response was built.
    """

    event_id: str
    event_type: EventType
    resource_id: str
    organization_id: str
    provider_version: str = PydanticField(min_length=1)
    status: ResponseStatus
    outputs: dict | None = None
    children_pending: bool = False
    error: str | None = None
    failure_code: ProviderFailureCode | None = None
    timestamp: datetime

    @model_validator(mode="after")
    def refuse_misplaced_children_pending(self) -> Self:
        """Refuse ``children_pending`` on a failure or together with outputs.

        Returns:
            The validated response.

        Raises:
            ValueError: If ``children_pending`` is set on a response whose
                status is not ``SUCCESS``, or whose outputs are not ``None``.
        """
        if not self.children_pending:
            return self

        if self.status != ResponseStatus.SUCCESS:
            raise ValueError("Only a successful response can wait for its children.")

        if self.outputs is not None:
            raise ValueError(CHILDREN_PENDING_WITH_OUTPUTS_MESSAGE)

        return self


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


class OrganizationMember(BaseModel):
    """One member of the caller's organization.

    Attributes:
        user_id: Pragmatiks user ID of the member.
        identifier: Email address or username the member signs in to
            Pragmatiks with, or None when none is shared.
        role: Role the member holds in the organization.
    """

    user_id: str
    identifier: str | None = None
    role: MemberRole


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
