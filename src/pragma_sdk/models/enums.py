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
    """Resource lifecycle event type: CREATE, UPDATE, or DELETE."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    MIGRATE_UP = "MIGRATE_UP"
    MIGRATE_DOWN = "MIGRATE_DOWN"


class ResponseStatus(StrEnum):
    """Provider response status: SUCCESS or FAILURE."""

    SUCCESS = "success"
    FAILURE = "failure"


class TrustTier(StrEnum):
    """Trust level for store providers."""

    OFFICIAL = "official"
    VERIFIED = "verified"
    COMMUNITY = "community"


class VersionStatus(StrEnum):
    """Build/publish status for a store provider version."""

    BUILDING = "building"
    PUBLISHED = "published"
    FAILED = "failed"
    YANKED = "yanked"


class UpgradePolicy(StrEnum):
    """Upgrade policy for installed store providers."""

    AUTO = "auto"
    MANUAL = "manual"


class ProviderScope(StrEnum):
    """Scope of a provider in the unified store."""

    PUBLIC = "public"
    TENANT = "tenant"


class ResourceTier(StrEnum):
    """Resource tier for installed store providers."""

    FREE = "free"
    STANDARD = "standard"
    PERFORMANCE = "performance"
