"""Enumeration types for the Pragma SDK."""

from __future__ import annotations

from enum import StrEnum

from pragma_sdk.types import EventType as EventType


class ResponseStatus(StrEnum):
    """Provider response status: SUCCESS or FAILURE."""

    SUCCESS = "success"
    FAILURE = "failure"


class VersionStatus(StrEnum):
    """Where a provider version is between its declaration and admission.

    Declaring a version leaves it ``AWAITING_WHEEL`` until its wheel is
    uploaded. Once the wheel is in storage the platform records it
    (``STAGED``) and queues it for the organization's provider host
    (``QUEUED``); the host takes it (``ADMITTING``) and, once admitted, the
    platform publishes it (``PROMOTING``). Admission ends the version
    ``PUBLISHED``, which makes it installable, or ``FAILED``, after which the
    version can be published again. Every other status means admission is
    still in flight.
    """

    AWAITING_WHEEL = "awaiting_wheel"
    STAGED = "staged"
    QUEUED = "queued"
    ADMITTING = "admitting"
    PROMOTING = "promoting"
    PUBLISHED = "published"
    FAILED = "failed"


SETTLED_VERSION_STATUSES = frozenset({VersionStatus.PUBLISHED, VersionStatus.FAILED})
"""Statuses that end an admission attempt.

``PUBLISHED`` is final; ``FAILED`` lasts until the version is published again.
"""


class AdmissionFailureCategory(StrEnum):
    """Why a provider version failed admission."""

    WHEEL_REFUSED = "wheel_refused"
    WHEEL_TOO_LARGE = "wheel_too_large"
    WHEEL_LOST = "wheel_lost"
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


class WheelUploadOutcome(StrEnum):
    """How storage took a wheel uploaded to its address.

    Either outcome leaves the wheel stored under the address.
    """

    STORED = "stored"
    ALREADY_STORED = "already_stored"


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


class InstallationLifecycleState(StrEnum):
    """Where a provider installation is.

    An operation is requested (``PENDING``), the provider host starts its
    version (``PROCESSING``) and the installation ends ``READY`` or
    ``FAILED``; ``DELETING`` lasts until an uninstall removes it.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class InstallationOperationType(StrEnum):
    """Kind of operation a provider installation runs or last ran.

    ``INSTALL`` installs it, or installs a failed one again; ``UPGRADE`` and
    ``DOWNGRADE`` move it to a newer or an older version; ``START`` starts a
    ready installation on a new provider host; ``UNINSTALL`` removes it.
    """

    INSTALL = "install"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    START = "start"
    UNINSTALL = "uninstall"


class ProviderScope(StrEnum):
    """Scope of a provider in the catalog."""

    PUBLIC = "public"
    TENANT = "tenant"


class OrganizationStatus(StrEnum):
    """Lifecycle status of an organization.

    Mirrors the API's organization lifecycle: an organization is created in
    ``BOOTSTRAPPING`` while the bootstrap worker provisions its tenant
    namespace, reaches ``READY`` once usable, or lands in ``BOOTSTRAP_FAILED``
    if the bootstrap deadline passes before setup finishes. It is
    ``DELETING`` while its deletion runs, ``DELETED`` once it is gone.
    Deletion cannot be cancelled.
    """

    BOOTSTRAPPING = "bootstrapping"
    READY = "ready"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    DELETING = "deleting"
    DELETED = "deleted"
