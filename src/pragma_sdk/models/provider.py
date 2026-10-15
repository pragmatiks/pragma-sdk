"""Provider models for the provider catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Base64Bytes, BaseModel, Field, computed_field

from pragma_sdk.models.enums import AdmissionFailureCategory, ProviderScope, UpgradePolicy, VersionStatus


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
        """Slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity, used in CLI output,
            web UI labels, and URL paths.
        """
        return f"{self.prefix}/{self.name}"


class ProviderVersion(BaseModel):
    """A version of a provider, from its declaration through admission.

    Declaring the version returns it ``awaiting_wheel``, as a
    :class:`DeclaredProviderVersion` carrying the ``operation_id`` its wheel
    is uploaded under. The upload moves it through ``staged`` and
    ``queued``; the organization's provider host then admits it
    (``admitting``) and the platform publishes it (``promoting``). Admission
    ends it ``published``, which fills the fields read from the admitted
    wheel, or ``failed``, which sets ``failure_category`` and
    ``error_message``.

    Attributes:
        distribution_name: Distribution name the wheel is published under,
            derived from the organization slug and the provider's short name.
        wheel_filename: Filename the wheel is stored under, carrying
            ``distribution_name``.
        wheel_sha256: SHA-256 digest of the wheel as stored under
            ``distribution_name``; it differs from the digest of the
            uploaded file.
        package: Importable Python package the provider's entry point names.
        summary: Summary from the wheel's metadata; it becomes the catalog
            description when this version creates the provider.
        keywords: Keywords from the wheel's metadata; they become the catalog
            tags when this version creates the provider.
        python_version: Exact Python version admission resolved the provider
            on, such as ``"3.14.2"``; installs use this same patch release.
            ``None`` until admitted.
        sdk_version: Pragmatiks SDK version the provider was admitted with.
            ``None`` until admitted.
        protocol_versions: Host protocol versions the provider speaks.
            Empty until admitted; ``status`` says whether it was.
        embedded_providers: Pragmatiks providers installed with this one,
            mapping distribution name to version. Empty until admitted.
        schemas: Resource type schemas the provider declares. ``None`` until
            admitted.
        operation_deadline_at: When admission must finish. ``None`` until the
            organization's provider host takes the version.
        status: Where the version is between publish and admission.
        failure_category: Why admission failed. Set only when ``status``
            is ``failed``.
        error_message: Human-readable reason the version failed, ready to show.
    """

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    version: str = Field(frozen=True)
    distribution_name: str = Field(frozen=True)
    wheel_filename: str | None = None
    wheel_sha256: str | None = None
    package: str | None = None
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    python_version: str | None = None
    sdk_version: str | None = None
    protocol_versions: list[int] = Field(default_factory=list)
    embedded_providers: dict[str, str] = Field(default_factory=dict)
    schemas: list[dict[str, Any]] | None = None
    operation_deadline_at: datetime | None = None
    changelog: str | None = None
    status: VersionStatus
    published_at: datetime | None = None
    failure_category: AdmissionFailureCategory | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def canonical(self) -> str:
        """Slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity this version belongs to.
        """
        return f"{self.prefix}/{self.name}"


class DeclaredProviderVersion(ProviderVersion):
    """A provider version as its publish declaration returns it.

    Only the declaration response carries ``operation_id``; every other
    response leaves it out.

    Attributes:
        operation_id: Publish operation the version's wheel is uploaded to,
            at ``PUT /providers/publish/{operation_id}/wheel``.
    """

    operation_id: str = Field(min_length=1)


class ProviderVersionDeclaration(BaseModel):
    """What a provider wheel claims about itself, declared before it is uploaded.

    ``POST /providers/publish`` checks the declaration and records the version
    ``awaiting_wheel``; the wheel uploaded afterwards must match it.
    ``metadata`` and ``entry_points`` are given as base64 text, hold the
    decoded bytes, and serialize back to base64 text.

    Attributes:
        wheel_filename: Filename of the built wheel.
        wheel_sha256: Hex SHA-256 digest of the wheel file.
        wheel_size: Size of the wheel file in bytes.
        dist_info_directory: Name of the wheel's ``.dist-info`` directory,
            such as ``postgresql-1.0.0.dist-info``.
        metadata: Contents of the wheel's ``METADATA`` file.
        entry_points: Contents of the wheel's ``entry_points.txt``, or
            ``None`` when the wheel has none.
        changelog: Release notes for this version, or ``None``.
    """

    wheel_filename: str
    wheel_sha256: str
    wheel_size: int
    dist_info_directory: str
    metadata: Base64Bytes
    entry_points: Base64Bytes | None = None
    changelog: str | None = None


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
        """Slash-joined ``prefix/name`` canonical string.

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
