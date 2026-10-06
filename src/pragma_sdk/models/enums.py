"""Enumeration types for the Pragma SDK."""

from __future__ import annotations

from enum import StrEnum

from pragma_sdk.types import EventType as EventType


class DeploymentStatus(StrEnum):
    """Status of a provider deployment."""

    PENDING = "pending"
    PROGRESSING = "progressing"
    AVAILABLE = "available"
    FAILED = "failed"


class ResponseStatus(StrEnum):
    """Provider response status: SUCCESS or FAILURE."""

    SUCCESS = "success"
    FAILURE = "failure"


class VersionStatus(StrEnum):
    """Admission status of a provider version.

    A publish leaves the version ``PENDING`` until the organization's provider
    host admits it; admission ends it ``PUBLISHED``, which makes it
    installable, or ``FAILED``. A failed version can be published again.
    """

    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class AdmissionFailureCategory(StrEnum):
    """Why a provider version failed admission."""

    UPLOAD_FAILED = "upload_failed"
    DIGEST_MISMATCH = "digest_mismatch"
    PYTHON_UNAVAILABLE = "python_unavailable"
    DEPENDENCY_RESOLUTION_FAILED = "dependency_resolution_failed"
    UNDECLARED_EMBEDDED_PROVIDER = "undeclared_embedded_provider"
    UNREGISTERED_EMBEDDED_PROVIDER = "unregistered_embedded_provider"
    EMBEDDED_PROVIDER_NOT_DEPENDED_ON = "embedded_provider_not_depended_on"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    IMPORT_FAILED = "import_failed"
    MISSING_OBSERVE = "missing_observe"
    STORED_WHEEL_CONFLICT = "stored_wheel_conflict"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    PLATFORM_ERROR = "platform_error"
    ORGANIZATION_DELETED = "organization_deleted"


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
    if the bootstrap deadline passes before setup finishes. ``DEACTIVATING``
    and ``DELETED`` cover teardown.
    """

    BOOTSTRAPPING = "bootstrapping"
    READY = "ready"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    DEACTIVATING = "deactivating"
    DELETED = "deleted"
