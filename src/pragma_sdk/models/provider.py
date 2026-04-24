"""Provider models for the provider catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field

from pragma_sdk.models.enums import ProviderScope, ResourceTier, TrustTier, UpgradePolicy, VersionStatus


class ProviderAuthor(BaseModel):
    """Author information for a provider.

    When ``organization_id`` is ``None``, the provider is platform-owned
    (not attributed to a specific customer organization). ``org_name``
    remains required as a display label.
    """

    organization_id: str | None = None
    org_name: str


class Provider(BaseModel):
    """Full provider metadata.

    Provider identity is stored as two separate fields: ``prefix`` and
    ``name``. The ``prefix`` is an opaque namespace token (either the
    literal ``"platform"`` for catalog providers owned by Pragmatiks or
    a customer organization slug). The ``name`` is the provider's short
    name (e.g. ``pragma``, ``gcp``). Use the :attr:`canonical` property
    when a display string or URL path is needed.
    """

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
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

    @computed_field
    @property
    def canonical(self) -> str:
        """Return the slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity, used in CLI output,
            web UI labels, and URL paths.
        """
        return f"{self.prefix}/{self.name}"


class ProviderVersion(BaseModel):
    """A published version of a provider."""

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
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

    @computed_field
    @property
    def canonical(self) -> str:
        """Return the slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity this version belongs to.
        """
        return f"{self.prefix}/{self.name}"


class ProviderInstallation(BaseModel):
    """A provider installed in the current tenant."""

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    installed_version: str
    upgrade_policy: UpgradePolicy
    resource_tier: ResourceTier
    config: dict[str, str] | None = None
    current_version: str | None = None
    current_image: str | None = None
    installed_at: datetime
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def canonical(self) -> str:
        """Return the slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity this installation
            targets.
        """
        return f"{self.prefix}/{self.name}"


class PaginatedResponse[T](BaseModel):
    """Paginated API response wrapper."""

    items: list[T]
    total: int
    limit: int
    offset: int
