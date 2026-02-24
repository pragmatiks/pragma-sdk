"""Store models for the Provider Store feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from pragma_sdk.models.enums import ResourceTier, TrustTier, UpgradePolicy, VersionStatus


class StoreAuthor(BaseModel):
    """Author information for a store provider."""

    tenant_id: str
    org_name: str


class StoreProviderSummary(BaseModel):
    """Summary view of a store provider for catalog listings."""

    name: str
    display_name: str
    description: str
    author: StoreAuthor
    trust_tier: TrustTier
    icon_url: str | None = None
    tags: list[str]
    latest_version: str | None = None
    install_count: int


class StoreProvider(BaseModel):
    """Full store provider metadata."""

    name: str
    display_name: str
    description: str
    author: StoreAuthor
    trust_tier: TrustTier
    icon_url: str | None = None
    readme: str | None = None
    tags: list[str]
    latest_version: str | None = None
    install_count: int
    created_at: datetime
    updated_at: datetime


class StoreVersion(BaseModel):
    """A published version of a store provider."""

    provider_name: str
    version: str
    runtime_version: str
    image_url: str | None = None
    source_hash: str | None = None
    cloud_build_id: str | None = None
    schemas: list[dict] | None = None
    changelog: str | None = None
    status: VersionStatus
    published_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class StoreProviderDetail(BaseModel):
    """Detailed store provider info including version history."""

    provider: StoreProvider
    versions: list[StoreVersion]


class StoreVersionDetail(BaseModel):
    """Detail view for a single store provider version."""

    version: StoreVersion


class InstalledProvider(BaseModel):
    """A store provider installed in the current tenant."""

    store_provider_name: str
    installed_version: str
    upgrade_policy: UpgradePolicy
    resource_tier: ResourceTier
    installed_at: datetime
    created_at: datetime
    updated_at: datetime


class InstalledProviderSummary(BaseModel):
    """Summary of an installed store provider with upgrade info."""

    store_provider_name: str
    installed_version: str
    upgrade_policy: UpgradePolicy
    resource_tier: ResourceTier
    installed_at: datetime
    latest_version: str | None = None
    upgrade_available: bool


class PaginatedResponse[T](BaseModel):
    """Paginated API response wrapper."""

    items: list[T]
    total: int
    limit: int
    offset: int
