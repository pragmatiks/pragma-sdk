"""Provider models for the provider catalog."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, SecretStr, computed_field, field_validator

from pragma_sdk.models.enums import (
    AdmissionFailureCategory,
    InstallationLifecycleState,
    InstallationOperationType,
    ProviderScope,
    VersionStatus,
)


UPLOAD_URL_SCHEMES = frozenset({"http", "https"})
"""URL schemes a signed upload address may use."""

UPLOAD_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
"""An upload header name httpx can send: an HTTP token."""

UPLOAD_HEADER_VALUE_PATTERN = re.compile(r"(?:[!-~]+(?:[ \t]+[!-~]+)*)?")
"""An upload header value httpx can send.

Empty, or printable ASCII words joined by spaces or tabs, with none around them.
"""


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
    :class:`DeclaredProviderVersion` carrying the address its wheel is
    uploaded to. Once the wheel is in storage the platform records it
    ``staged`` and ``queued``; the organization's provider host then admits
    it (``admitting``) and the platform publishes it (``promoting``).
    Admission ends it ``published``, which fills the fields read from the
    admitted wheel, or ``failed``, which sets ``failure_category`` and
    ``error_message``.

    Attributes:
        distribution_name: Distribution name the wheel is published under,
            derived from the organization slug and the provider's short name.
        operation_id: Publish operation that declared the version; a new
            declaration of the same version replaces it. Set only when the
            caller's organization authored the version, ``None`` for every
            other reader.
        wheel_filename: Filename the wheel is stored under, carrying
            ``distribution_name``.
        wheel_sha256: Declared SHA-256 digest of the wheel, renamed to
            ``distribution_name`` before it was declared.
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
    operation_id: str | None = None
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


class WheelUploadAddress(BaseModel):
    """Where a declared wheel is uploaded, straight to storage.

    The wheel is sent as the body of one ``PUT`` to ``url`` carrying
    ``headers`` verbatim; the storage accepts exactly the declared size,
    once.

    Whoever holds ``url`` can write the declared wheel's bytes until
    ``expires_at``, so it is a :class:`~pydantic.SecretStr`, masked in the
    model's representation and JSON serialization, and a failed validation's
    ``str()`` and ``repr()`` leave the input out; its ``errors()`` and
    ``json()`` keep the input unless called with ``include_input=False``.
    A JSON-serialized address carries the mask, not the address, so it does
    not validate back; a Python-mode ``model_dump()`` keeps the ``SecretStr``
    and validates back to the address.

    Validation refuses a ``url`` that is not an ``http`` or ``https`` URL
    with a host, and ``headers`` whose names or values httpx cannot send;
    neither refusal's message carries the address.

    Attributes:
        url: Signed address the wheel is uploaded to; ``get_secret_value()``
            gives it.
        headers: Headers the upload must send unchanged; the address is
            signed over them.
        expires_at: When the address stops accepting the upload.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    url: SecretStr
    headers: dict[str, str]
    expires_at: datetime

    @field_validator("url")
    @classmethod
    def require_http_url(cls, url: SecretStr) -> SecretStr:
        """Refuse an address that is not an ``http`` or ``https`` URL with a host.

        Args:
            url: The signed address.

        Returns:
            The address, unchanged.

        Raises:
            ValueError: When httpx cannot parse the address, or it has
                another scheme or no host; the message never carries the
                address.
        """
        try:
            parsed_url: httpx.URL | None = httpx.URL(url.get_secret_value())
        except httpx.InvalidURL:
            parsed_url = None

        if parsed_url is None or parsed_url.scheme not in UPLOAD_URL_SCHEMES or not parsed_url.host:
            raise ValueError("The upload address must be an http or https URL with a host")

        return url

    @field_validator("headers")
    @classmethod
    def require_sendable_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """Refuse headers whose names or values httpx cannot send.

        Args:
            headers: The headers the upload must send unchanged.

        Returns:
            The headers, unchanged.

        Raises:
            ValueError: When a name is not an HTTP token, or a value is
                neither empty nor printable ASCII words joined by spaces or
                tabs, with none around them; the message names the header,
                not its value.
        """
        for name, value in headers.items():
            if not UPLOAD_HEADER_NAME_PATTERN.fullmatch(name) or not UPLOAD_HEADER_VALUE_PATTERN.fullmatch(value):
                raise ValueError(f"The upload header {name!r} cannot be sent in an HTTP request")

        return headers


class DeclaredProviderVersion(ProviderVersion):
    """A provider version as its publish declaration returns it.

    Its input carries the signed upload address, which a failed validation
    shows, and its serialization carries, as :class:`WheelUploadAddress`
    describes; a malformed address fails its validation.

    Attributes:
        operation_id: Publish operation the declaration issued; the wheel is
            staged under it.
        wheel_upload: Address the wheel is uploaded to, set only while the
            version is ``awaiting_wheel``; ``None`` once the wheel was
            received.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    operation_id: str = Field(min_length=1)
    wheel_upload: WheelUploadAddress | None = None


class ProviderVersionDeclaration(BaseModel):
    """What a provider wheel claims about itself, declared before it is uploaded.

    ``POST /providers/publish`` checks the declaration and records the version
    ``awaiting_wheel``; the wheel uploaded afterwards must match it. The
    wheel is declared already renamed to its distribution name, as
    :func:`~pragma_sdk.wheel_rename.rename_provider_wheel` renames it.
    ``metadata`` and ``entry_points`` are given as base64 text, hold the
    decoded bytes, and serialize back to base64 text.

    Attributes:
        wheel_filename: Filename of the renamed wheel, such as
            ``fubar_postgresql-1.0.0-py3-none-any.whl``.
        wheel_sha256: Hex SHA-256 digest of the renamed wheel file.
        wheel_size: Size of the renamed wheel file in bytes.
        dist_info_directory: Name of the renamed wheel's ``.dist-info``
            directory, such as ``fubar_postgresql-1.0.0.dist-info``.
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
    """A provider installed in the current tenant.

    Attributes:
        prefix: Namespace token of the installed provider.
        name: Provider short name within the prefix.
        installed_version: Version the installation last served, or ``None``
            when no operation has reached ``ready`` yet.
        config: Environment variables passed only to this provider.
        installed_at: When the provider was first installed.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        lifecycle_state: Where the installation is: ``pending``,
            ``processing``, ``ready``, ``failed`` or ``deleting``.
        operation_type: The installation's latest operation: ``install``,
            ``upgrade``, ``downgrade``, ``start`` or ``uninstall``.
        operation_version: Version the latest operation targets; installing
            again without a version installs it.
        failure_message: User-facing reason of the last failed operation:
            set while the installation is ``failed``, and while it is
            ``ready`` after an upgrade or downgrade failed with the previous
            version still serving (that text reads "Upgrading <provider> to
            <version> failed: <what went wrong> The installed version keeps
            serving; <recovery>"); ``None`` otherwise.
    """

    prefix: str = Field(frozen=True)
    name: str = Field(frozen=True)
    installed_version: str | None = None
    config: dict[str, str] | None = None
    installed_at: datetime
    created_at: datetime
    updated_at: datetime
    lifecycle_state: InstallationLifecycleState | None = None
    operation_type: InstallationOperationType | None = None
    operation_version: str | None = None
    failure_message: str | None = None

    @computed_field
    @property
    def canonical(self) -> str:
        """Slash-joined ``prefix/name`` canonical string.

        Returns:
            Display form of the provider identity this installation
            targets.
        """
        return f"{self.prefix}/{self.name}"


class ProviderInstallationEvent(BaseModel):
    """One entry of an installation's history, kept for 30 days.

    The history outlives an uninstall, so it still answers after the
    installation row is gone.

    Attributes:
        from_state: State the installation left.
        to_state: State the installation entered, or ``uninstalled`` when the
            uninstall removed it.
        operation_id: Operation that caused the transition.
        operation_type: Kind of that operation: ``install``, ``upgrade``,
            ``downgrade``, ``start`` or ``uninstall``.
        version: Version the installation runs once the event's change is
            done; for a failed upgrade or downgrade that keeps the installed
            version serving, that installed version (its message names the
            target that failed).
        message: What happened, such as ``started``, ``stopped: host
            restarted`` or the failure text; ``None`` for a transition that
            needs none.
        cause: Detail behind a failure, such as the build step's output, or
            ``None``.
        occurred_at: When the transition happened.
    """

    from_state: InstallationLifecycleState | None = None
    to_state: InstallationLifecycleState | Literal["uninstalled"]
    operation_id: str | None = None
    operation_type: InstallationOperationType | None = None
    version: str | None = None
    message: str | None = None
    cause: str | None = None
    occurred_at: datetime


class PaginatedResponse[T](BaseModel):
    """Paginated API response wrapper."""

    items: list[T]
    total: int
    limit: int
    offset: int
