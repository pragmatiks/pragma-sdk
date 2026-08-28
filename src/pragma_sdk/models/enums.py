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


class TeardownAction(StrEnum):
    """What a teardown does to one resource its cascade reaches.

    ``TEARDOWN`` tears the resource down; ``WALK_THROUGH`` leaves it as it is
    and carries on into what it owns; ``RELEASED`` keeps it alive because an
    owner outside the teardown still holds it, dropping only the ownership
    link; ``DEPENDENT_WAITING`` parks it in waiting until the resource it reads
    from is available again.
    """

    TEARDOWN = "teardown"
    WALK_THROUGH = "walk_through"
    RELEASED = "released"
    DEPENDENT_WAITING = "dependent_waiting"


class ProviderScope(StrEnum):
    """Scope of a provider in the catalog."""

    PUBLIC = "public"
    TENANT = "tenant"


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
