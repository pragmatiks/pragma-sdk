"""Provider models for the provider catalog."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from pragma_sdk.models.enums import ProviderScope, ResourceTier, UpgradePolicy, VersionStatus


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProviderAuthor(BaseModel):
    """Author information for a provider.

    ``kind`` discriminates between providers owned by Pragmatiks
    (``"platform"``) and providers owned by a customer organization
    (``"customer"``). Platform-owned providers leave ``organization_id``
    as ``None``; customer-owned providers must populate it.
    ``display_name`` is the human-facing label shown in catalog listings
    and the web UI.
    """

    kind: Literal["customer", "platform"]
    organization_id: str | None = None
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
    """A published version of a provider."""

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    version: str = Field(frozen=True)
    runtime_version: str
    image_url: str | None = None
    wheel_url: str | None = None
    runtime_image: str | None = None
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


class RegisterProviderVersionRequest(BaseModel):
    """Request body for ``POST /provider-versions``.

    The SDK has no opinion about where the wheel lives — only that
    ``wheel_url`` is HTTPS and ends in ``.whl``, and that ``sha256``
    matches the bytes the API fetches. Building, uploading, and
    hashing the wheel are the caller's responsibility.

    Attributes:
        name: Namespaced provider name in ``"org/short"`` form.
        version: Semver string for this release.
        wheel_url: HTTPS URL pointing at the published ``.whl``.
        sha256: 64-character lowercase hex SHA-256 of the wheel.
        schemas: Per-resource schema map keyed by resource type name.
        metadata: Catalog display fields (``display_name``,
            ``description``, ``icon_url``, ``tags``).
        changelog: Optional release notes.
    """

    name: str
    version: str
    wheel_url: str
    sha256: str
    schemas: dict[str, dict[str, Any]]
    metadata: dict[str, Any]
    changelog: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        """Reject names that are not in ``"org/short"`` form.

        Args:
            value: Candidate name.

        Returns:
            The validated name.

        Raises:
            ValueError: If the name does not contain exactly one slash
                or either segment is empty.
        """
        org, sep, short = value.partition("/")

        if not sep or not org or not short or "/" in short:
            raise ValueError(f"name must be namespaced as 'org/short', got: {value!r}")

        return value

    @field_validator("wheel_url")
    @classmethod
    def _check_wheel_url(cls, value: str) -> str:
        """Reject wheel URLs that are not HTTPS or do not end in ``.whl``.

        Args:
            value: Candidate URL.

        Returns:
            The validated URL.

        Raises:
            ValueError: If the URL is not HTTPS or does not end in ``.whl``.
        """
        if not value.startswith("https://"):
            raise ValueError(f"wheel_url must be an HTTPS URL, got: {value!r}")

        if not value.endswith(".whl"):
            raise ValueError(f"wheel_url must end in '.whl', got: {value!r}")

        return value

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        """Reject digests that are not 64 lowercase hex characters.

        Args:
            value: Candidate digest.

        Returns:
            The validated digest.

        Raises:
            ValueError: If the value is not 64 lowercase hex characters.
        """
        if not _SHA256_RE.fullmatch(value):
            raise ValueError(f"sha256 must be 64 lowercase hex characters, got: {value!r}")

        return value


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
