"""Provider models for the provider catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field

from pragma_sdk.models.enums import ProviderScope, UpgradePolicy, VersionStatus


class ProviderAuthor(BaseModel):
    """Author information for a provider.

    Every provider is owned by exactly one publishing organization,
    identified by ``organization_id``. First-party providers are owned
    by the reserved ``pragmatiks`` organization. ``display_name`` is the
    human-facing label shown in catalog listings and the web UI.
    """

    organization_id: str
    display_name: str


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
    """A published version of a provider.

    Attributes:
        wheel_sha256: SHA-256 digest of the wheel bytes, computed
            server-side at publish time as a catalog audit field.
        package_name: Importable Python package name inside the
            published wheel. ``None`` when the runtime must infer it
            from installed wheel metadata.
    """

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    version: str = Field(frozen=True)
    wheel_sha256: str | None = None
    package_name: str | None = None
    entrypoint: list[str] | None = None
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


class ProviderVersionMetadata(BaseModel):
    """Catalog display fields recorded on the provider catalog row.

    Attributes:
        display_name: Human-facing label shown in catalog listings.
        description: Long-form description of the provider.
        icon_url: Optional URL to an icon shown alongside the listing.
        tags: Optional list of catalog tags.
    """

    display_name: str
    description: str
    icon_url: str | None = None
    tags: list[str] = Field(default_factory=list)


class ProviderInstallation(BaseModel):
    """A provider installed in the current tenant."""

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    installed_version: str
    upgrade_policy: UpgradePolicy
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
