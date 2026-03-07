"""Provider models for the provider catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from pragma_sdk.models.enums import ProviderScope, ResourceTier, TrustTier, UpgradePolicy, VersionStatus


class ProviderAuthor(BaseModel):
    """Author information for a provider."""

    tenant_id: str
    org_name: str


class Provider(BaseModel):
    """Full provider metadata."""

    name: str
    display_name: str
    description: str
    author: ProviderAuthor
    trust_tier: TrustTier
    scope: ProviderScope = ProviderScope.PUBLIC
    icon_url: str | None = None
    readme: str | None = None
    tags: list[str]
    latest_version: str | None = None
    install_count: int
    created_at: datetime
    updated_at: datetime


class ProviderVersion(BaseModel):
    """A published version of a provider."""

    provider_name: str
    version: str
    runtime_version: str
    image_url: str | None = None
    source_hash: str | None = None
    build_id: str | None = None
    schemas: list[dict[str, Any]] | None = None
    changelog: str | None = None
    status: VersionStatus
    published_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderInstallation(BaseModel):
    """A provider installed in the current tenant."""

    provider_name: str
    installed_version: str
    upgrade_policy: UpgradePolicy
    resource_tier: ResourceTier
    current_version: str | None = None
    current_image: str | None = None
    installed_at: datetime
    created_at: datetime
    updated_at: datetime


class PaginatedResponse[T](BaseModel):
    """Paginated API response wrapper."""

    items: list[T]
    total: int
    limit: int
    offset: int
