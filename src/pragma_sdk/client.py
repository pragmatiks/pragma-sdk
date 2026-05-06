"""HTTP clients for the Pragma API."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from pragma_sdk.auth import BearerAuth
from pragma_sdk.config import get_token_for_context
from pragma_sdk.exceptions import (
    ProjectHasResourcesError,
    ProjectMismatchError,
    ResourceFailedError,
)
from pragma_sdk.models import (
    AgentInstance,
    AgentInstanceStatus,
    AgentType,
    AgentTypeCreate,
    AgentTypeUpdate,
    BoardSummary,
    CostEstimate,
    DeepAnalysisRequest,
    DeepAnalysisResponse,
    DeploymentResult,
    ExplainTaskRequest,
    ExplainTaskResponse,
    GenerateSubtasksRequest,
    GenerateSubtasksResponse,
    GraphDiff,
    ImproveTaskRequest,
    ImproveTaskResponse,
    LLMProviderSummary,
    Organization,
    OrganizationSettings,
    PaginatedResponse,
    PerformanceProfile,
    Provider,
    ProviderInstallation,
    ProviderScope,
    ProviderVersion,
    Resource,
    ResourceSchema,
    ResourceTier,
    ReviewSummaryRequest,
    ReviewSummaryResponse,
    SuggestAssigneeRequest,
    SuggestAssigneeResponse,
    SummarizeThreadRequest,
    SummarizeThreadResponse,
    Task,
    TaskActivityEntry,
    TaskAssign,
    TaskComment,
    TaskCommentCreate,
    TaskCommentUpdate,
    TaskCreate,
    TaskMutationPage,
    TaskStatus,
    TaskTransition,
    TaskUpdate,
    UpgradePolicy,
    UserInfo,
)
from pragma_sdk.models.identity import _validate_segment
from pragma_sdk.models.project import (
    CreateProjectRequest,
    DeleteProjectRequest,
    Project,
    UpdateProjectRequest,
)
from pragma_sdk.types import LifecycleState


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_wheel_url(wheel_url: str) -> str:
    """Validate that ``wheel_url`` is an HTTPS URL ending in ``.whl``.

    The SDK has no opinion about where wheels live — only that the
    URL is HTTPS and points at a wheel.

    Args:
        wheel_url: Candidate URL.

    Returns:
        The URL unchanged.

    Raises:
        ValueError: If the URL is not HTTPS or does not end in ``.whl``.
    """
    if not wheel_url.startswith("https://"):
        raise ValueError(f"wheel_url must be an HTTPS URL, got: {wheel_url!r}")

    if not wheel_url.endswith(".whl"):
        raise ValueError(f"wheel_url must end in '.whl', got: {wheel_url!r}")

    return wheel_url


def _validate_sha256(sha256: str) -> str:
    """Validate a hex-encoded SHA-256 digest.

    Args:
        sha256: Candidate digest.

    Returns:
        The digest unchanged.

    Raises:
        ValueError: If the value is not 64 lowercase hex characters.
    """
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"sha256 must be 64 lowercase hex characters, got: {sha256!r}")

    return sha256


def _validate_provider_name(provider_name: str) -> str:
    """Validate and return a namespaced provider name for URL construction.

    Args:
        provider_name: Provider name in 'org/name' format.

    Returns:
        The validated provider name, already suitable for URL paths.

    Raises:
        ValueError: If name is not in 'org/name' format.
    """
    if "/" not in provider_name:
        raise ValueError(f"Provider name must be namespaced as 'org/name', got: {provider_name!r}")

    org, name = provider_name.split("/", 1)

    if not org or not name or "/" in name:
        raise ValueError(f"Provider name must be namespaced as 'org/name', got: {provider_name!r}")

    return provider_name


def _build_provider_version_request_body(
    *,
    name: str,
    version: str,
    wheel_url: str,
    sha256: str,
    schemas: dict[str, dict[str, Any]],
    metadata: dict[str, Any],
    changelog: str | None,
) -> dict[str, Any]:
    """Assemble the JSON body for ``POST /provider-versions``.

    Args:
        name: Namespaced provider name in ``"org/short"`` form.
        version: Semver string for this release.
        wheel_url: HTTPS URL pointing at the published ``.whl``.
        sha256: Hex-encoded SHA-256 of the wheel; the API verifies it
            against the bytes it fetches from ``wheel_url``.
        schemas: Per-resource schema map keyed by resource type name.
        metadata: Display fields (``display_name``, ``description``,
            ``icon_url``, ``tags``) recorded on the catalog row.
        changelog: Optional release notes.

    Returns:
        Dict matching the ``ProviderVersionRegister`` shape on the API.
    """
    body: dict[str, Any] = {
        "name": name,
        "version": version,
        "wheel_url": wheel_url,
        "sha256": sha256,
        "schemas": schemas,
        "metadata": metadata,
    }

    if changelog is not None:
        body["changelog"] = changelog

    return body


def _raise_project_has_resources(error: httpx.HTTPStatusError) -> None:
    """Translate a project-has-resources 409 into a typed SDK exception.

    Inspects ``error.response`` for the structured body the API emits
    when a project delete is refused because resources remain. When all
    expected fields are present the helper raises
    :class:`ProjectHasResourcesError`; otherwise it re-raises the original
    :class:`httpx.HTTPStatusError` unchanged so callers still see the raw
    response.

    Args:
        error: The HTTP error raised by ``response.raise_for_status()``
            for a DELETE /projects/{id} call that returned 409.

    Raises:
        ProjectHasResourcesError: If the 409 body matches the expected
            shape for a project-has-resources rejection.
        httpx.HTTPStatusError: Re-raised unchanged if the body does not
            match the expected shape, is not valid JSON, or is missing
            any of the required fields.
    """  # noqa: DOC502
    response = error.response

    try:
        body = response.json()
    except ValueError as json_error:
        raise error from json_error

    detail = body.get("detail") if isinstance(body, dict) else None

    if not isinstance(detail, dict):
        raise error

    project_id = detail.get("project_id")
    resource_count = detail.get("resource_count")
    resources = detail.get("resources")

    if not isinstance(project_id, str) or not isinstance(resource_count, int) or not isinstance(resources, list):
        raise error

    raise ProjectHasResourcesError(
        project_id=project_id,
        resource_count=resource_count,
        resources=list(resources),
    ) from error


class BaseClient:
    """Base class for Pragma API clients with shared initialization logic."""

    base_url: str
    timeout: float

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        auth_token: str | None | object = ...,
        context: str | None = None,
        require_auth: bool = False,
    ):
        """Initialize client with automatic token discovery.

        Args:
            base_url: API URL. Defaults to PRAGMA_API_URL env var or https://api.pragmatiks.io.
            timeout: Request timeout in seconds.
            auth_token: Bearer token. Omit for auto-discovery, pass None to disable auth.
            context: Named context for token lookup (e.g., 'production').
            require_auth: Raise if no token can be discovered.

        Raises:
            ValueError: If require_auth is True and no token is found.
        """
        self.base_url = base_url or os.getenv("PRAGMA_API_URL", "https://api.pragmatiks.io")
        self.timeout = timeout

        if auth_token is ...:
            resolved_token = get_token_for_context(context)

            if require_auth and resolved_token is None:
                context_display = context or "default"
                raise ValueError(
                    f"Authentication required but no token found for context '{context_display}'. "
                    f"Set PRAGMA_AUTH_TOKEN environment variable, "
                    f"set PRAGMA_AUTH_TOKEN_{context_display.upper()} for context-specific auth, "
                    f"or run 'pragma login'."
                )
        else:
            resolved_token = auth_token if isinstance(auth_token, str) else None

        self._auth = BearerAuth(resolved_token) if resolved_token else None


class PragmaClient(BaseClient):
    """Synchronous client for the Pragma API.

    Example:
        >>> with PragmaClient() as client:
        ...     resources = client.list_resources(provider="example")
        ...     resource = client.get_resource("example", "database", "my-db")
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        auth_token: str | None | object = ...,
        context: str | None = None,
        require_auth: bool = False,
    ):
        """Initialize the synchronous Pragma client.

        See BaseClient for parameter documentation.
        """
        super().__init__(base_url, timeout, auth_token, context, require_auth)
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout, auth=self._auth)

    def __enter__(self):
        """Enter context manager.

        Returns:
            Self for use in with statement.
        """
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        """Exit context manager and close client."""
        self.close()

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make an HTTP request to the Pragma API.

        Returns:
            Parsed JSON response, raw text, or None for 204 responses.

        Raises:
            httpx.HTTPStatusError: If the API returns an error response.
        """  # noqa: DOC502
        response = self._client.request(
            method=method,
            url=path,
            params=params,
            json=json_data,
            **kwargs,
        )

        response.raise_for_status()
        if response.status_code == 204:
            return None
        if response.headers.get("content-type") == "application/json":
            return response.json()
        return response.text

    def is_healthy(self) -> bool:
        """Check if the Pragma API is healthy.

        Returns:
            True if API returns healthy status, False otherwise.
        """
        try:
            response = self._request("GET", "/health")
            return response.get("status") == "ok"
        except httpx.HTTPError:
            return False

    def get_me(self) -> UserInfo:
        """Get current authenticated user information.

        Returns:
            UserInfo with user ID, email, organization ID and name.
        """
        response = self._request("GET", "/auth/me")
        return UserInfo.model_validate(response)

    def project(self, project_id: str) -> ProjectResources:
        """Build a project-scoped resource handle.

        Resource operations live under the returned handle: the ``project_id``
        is interpolated into all request paths and validated against every
        resource submitted through :meth:`ProjectResources.apply_resource`.

        Args:
            project_id: Project identifier to scope operations to.

        Returns:
            :class:`ProjectResources` bound to this client.

        Raises:
            InvalidResourceIdentityError: If ``project_id`` is empty, contains
                the reserved ``::`` separator, or has control characters.
        """  # noqa: DOC502
        return ProjectResources(self, project_id)

    def list_projects(self) -> list[Project]:
        """List projects visible to the current caller.

        Returns:
            Projects owned by the caller's organization. Private projects
            are never returned.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/projects")
        return [Project.model_validate(item) for item in response]

    def get_project(self, project_id: str) -> Project:
        """Fetch a single project by ID.

        Args:
            project_id: Project identifier.

        Returns:
            The project with metadata.

        Raises:
            httpx.HTTPStatusError: If the project is not found or the request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/projects/{quote(project_id, safe='')}")
        return Project.model_validate(response)

    def create_project(self, request: CreateProjectRequest) -> Project:
        """Create a new project.

        Args:
            request: Project creation payload.

        Returns:
            The newly created project.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = self._request("POST", "/projects", json_data=request.model_dump(exclude_none=True))
        return Project.model_validate(response)

    def update_project(self, project_id: str, request: UpdateProjectRequest) -> Project:
        """Update project metadata.

        Args:
            project_id: Project identifier.
            request: Fields to update. ``slug`` is immutable and not included.

        Returns:
            The updated project.

        Raises:
            httpx.HTTPStatusError: If the project is not found or the update fails.
        """  # noqa: DOC502
        response = self._request(
            "PATCH",
            f"/projects/{quote(project_id, safe='')}",
            json_data=request.model_dump(exclude_none=True),
        )
        return Project.model_validate(response)

    def delete_project(self, project_id: str, request: DeleteProjectRequest) -> None:
        """Hard-delete a project with typed confirmation.

        The caller must pass the project's slug in ``request.confirmation``;
        the server rejects the request if the value does not match. By
        default the server also refuses to delete a project that still
        holds resources. Set ``request.orphan_resources`` to ``True`` to
        bypass that safety check and orphan the resources.

        Args:
            project_id: Project identifier to delete.
            request: Confirmation payload carrying the project's slug and
                optional ``orphan_resources`` flag.

        Raises:
            ProjectHasResourcesError: If the server returns 409 with a
                project-has-resources body, typically because the project
                still contains resources and ``orphan_resources`` was not
                set.
            httpx.HTTPStatusError: If confirmation fails, the project is
                not found, the 409 body does not match the expected
                project-has-resources shape, or the request otherwise
                fails.
        """  # noqa: DOC502
        try:
            self._request(
                "DELETE",
                f"/projects/{quote(project_id, safe='')}",
                json_data=request.model_dump(),
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                _raise_project_has_resources(error)
            raise

    def list_resource_schemas(self, provider: str | None = None) -> list[ResourceSchema]:
        """List available resource schemas from deployed providers.

        Args:
            provider: Filter by provider name.

        Returns:
            List of resource schemas containing provider, resource, schema, description.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        response = self._request("GET", "/resources/schemas", params=params)
        return [ResourceSchema.model_validate(item) for item in response]

    def list_dead_letter_events(self, provider: str | None = None) -> list[dict[str, Any]]:
        """List dead letter events with optional provider filter.

        Args:
            provider: Filter by provider name.

        Returns:
            List of dead letter events as raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        return self._request("GET", "/ops/dead-letter", params=params)

    def get_dead_letter_event(self, event_id: str) -> dict[str, Any]:
        """Get a dead letter event by ID.

        Args:
            event_id: The dead letter event ID.

        Returns:
            Dead letter event as raw dict.

        Raises:
            httpx.HTTPStatusError: If event not found or request fails.
        """  # noqa: DOC502
        return self._request("GET", f"/ops/dead-letter/{event_id}")

    def retry_dead_letter_event(self, event_id: str) -> None:
        """Retry a dead letter event.

        Args:
            event_id: The dead letter event ID to retry.

        Raises:
            httpx.HTTPStatusError: If event not found or retry fails.
        """  # noqa: DOC502
        self._request("POST", f"/ops/dead-letter/{event_id}/retry")

    def retry_all_dead_letter_events(self) -> int:
        """Retry all dead letter events.

        Returns:
            Number of events retried.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("POST", "/ops/dead-letter/retry-all")
        return response["retried_count"]

    def delete_dead_letter_event(self, event_id: str) -> None:
        """Delete a dead letter event.

        Args:
            event_id: The dead letter event ID to delete.

        Raises:
            httpx.HTTPStatusError: If event not found or deletion fails.
        """  # noqa: DOC502
        self._request("DELETE", f"/ops/dead-letter/{event_id}")

    def delete_dead_letter_events(self, provider: str | None = None, *, all: bool = False) -> int:
        """Delete multiple dead letter events.

        Args:
            provider: Delete events for this provider only.
            all: Delete all dead letter events (ignores provider filter).

        Returns:
            Number of events deleted.

        Raises:
            ValueError: If neither provider nor all is specified.
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        if not provider and not all:
            raise ValueError("Must specify either provider or all=True")

        params: dict[str, Any] = {}
        if all:
            params["all"] = "true"
        elif provider:
            params["provider"] = provider

        response = self._request("DELETE", "/ops/dead-letter", params=params)
        return response["deleted_count"]

    def upload_file(self, name: str, content: bytes, content_type: str) -> dict[str, Any]:
        """Upload a file to the Pragma file storage.

        Args:
            name: Name of the file (used in the storage path).
            content: Raw file content as bytes.
            content_type: MIME type of the file (e.g., "image/png", "application/pdf").

        Returns:
            Dict containing url, public_url, size, content_type, checksum, uploaded_at.

        Raises:
            httpx.HTTPStatusError: If the upload fails.
        """  # noqa: DOC502
        return self._request(
            "POST",
            f"/files/{name}/upload",
            files={"file": (name, content, content_type)},
        )

    def list_providers(
        self,
        query: str | None = None,
        scope: ProviderScope | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[Provider]:
        """Browse and search the provider catalog.

        Args:
            query: Search query string.
            scope: Filter by provider scope (e.g. 'public', 'tenant').
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of providers.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if query is not None:
            params["q"] = query

        if scope is not None:
            params["scope"] = scope

        if tags is not None:
            params["tags"] = tags

        response = self._request("GET", "/providers", params=params)
        return PaginatedResponse[Provider].model_validate(response)

    def get_provider(self, provider_name: str) -> Provider:
        """Get provider info.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            Provider metadata.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("GET", f"/providers/{path}")
        return Provider.model_validate(response)

    def list_provider_versions(self, provider_name: str) -> list[ProviderVersion]:
        """List all versions of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            List of provider versions.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("GET", f"/providers/{path}/versions")
        return [ProviderVersion.model_validate(item) for item in response]

    def update_provider(self, provider_name: str, metadata: dict[str, Any]) -> Provider:
        """Update provider metadata.

        Args:
            provider_name: Namespaced provider name ('org/name').
            metadata: Fields to update (e.g. display_name, description, tags).

        Returns:
            Updated provider.

        Raises:
            httpx.HTTPStatusError: If provider not found or update fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("PATCH", f"/providers/{path}", json_data=metadata)
        return Provider.model_validate(response)

    def delete_provider(self, provider_name: str) -> None:
        """Delete a provider from the catalog.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Raises:
            httpx.HTTPStatusError: If provider not found or deletion fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        self._request("DELETE", f"/providers/{path}")

    def register_provider_version(
        self,
        *,
        name: str,
        version: str,
        wheel_url: str,
        sha256: str,
        schemas: dict[str, dict[str, Any]],
        metadata: dict[str, Any],
        changelog: str | None = None,
    ) -> ProviderVersion:
        """Register a new provider version against an externally hosted wheel.

        The SDK does not build, upload, or hash the wheel — the caller
        is responsible for placing the wheel on any HTTPS host and
        supplying the URL plus SHA-256 digest. This method only POSTs
        the metadata record to ``POST /provider-versions`` and returns
        the persisted version.

        Args:
            name: Namespaced provider name in ``"org/short"`` form
                (e.g. ``"pragmatiks/gcp"``).
            version: Semver string for this release.
            wheel_url: HTTPS URL ending in ``.whl``.
            sha256: 64-character lowercase hex SHA-256 of the wheel; the
                API verifies it after fetching the URL.
            schemas: Per-resource schema map keyed by resource type
                name, in the shape the API expects on the request body.
            metadata: Catalog display fields (``display_name``,
                ``description``, ``icon_url``, ``tags``).
            changelog: Optional release notes for this version.

        Returns:
            The persisted ``ProviderVersion``.

        Raises:
            ValueError: If ``wheel_url`` is not HTTPS, does not end in
                ``.whl``, or ``sha256`` is not 64 lowercase hex chars.
            httpx.HTTPStatusError: If the API rejects the request (e.g.
                already-published version, schema validation failure,
                unreachable wheel URL).
        """  # noqa: DOC502
        body = _build_provider_version_request_body(
            name=name,
            version=version,
            wheel_url=_validate_wheel_url(wheel_url),
            sha256=_validate_sha256(sha256),
            schemas=schemas,
            metadata=metadata,
            changelog=changelog,
        )

        response = self._request("POST", "/provider-versions", json_data=body)
        return ProviderVersion.model_validate(response)

    def install_provider(
        self,
        provider_name: str,
        version: str | None = None,
        resource_tier: ResourceTier | str = ResourceTier.STANDARD,
        upgrade_policy: UpgradePolicy | str = UpgradePolicy.MANUAL,
        config: dict[str, str] | None = None,
    ) -> ProviderInstallation:
        """Install a provider from the catalog.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.
            config: Key-value pairs injected as environment variables
                on the provider deployment.

        Returns:
            Installed provider info.

        Raises:
            httpx.HTTPStatusError: If installation fails.
        """  # noqa: DOC502
        _validate_provider_name(provider_name)
        data: dict = {
            "provider_name": provider_name,
            "resource_tier": resource_tier,
            "upgrade_policy": upgrade_policy,
        }

        if version is not None:
            data["version"] = version

        if config is not None:
            data["config"] = config

        response = self._request("POST", "/providers/install", json_data=data)
        return ProviderInstallation.model_validate(response)

    def list_installations(self) -> list[ProviderInstallation]:
        """List installed providers for the current tenant.

        Returns:
            List of provider installations.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/providers/installed")
        return [ProviderInstallation.model_validate(item) for item in response]

    def uninstall_provider(self, provider_name: str, *, cascade: bool = False) -> None:
        """Uninstall an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            cascade: If True, delete all resources managed by this provider.

        Raises:
            httpx.HTTPStatusError: If uninstall fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        params = {}

        if cascade:
            params["cascade"] = "true"

        self._request("DELETE", f"/providers/installed/{path}", params=params)

    def upgrade_provider(self, provider_name: str, target_version: str | None = None) -> ProviderInstallation:
        """Upgrade an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version (latest if None).

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If upgrade fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {}

        if target_version is not None:
            data["version"] = target_version

        response = self._request("POST", f"/providers/installed/{path}/upgrade", json_data=data)
        return ProviderInstallation.model_validate(response)

    def downgrade_provider(self, provider_name: str, target_version: str) -> ProviderInstallation:
        """Downgrade an installed provider to a specific version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version to downgrade to.

        Returns:
            Updated installed provider info.

        Raises:
            ValueError: If target_version is empty or whitespace.
            httpx.HTTPStatusError: If downgrade fails.
        """  # noqa: DOC502
        if not target_version.strip():
            raise ValueError("target_version must be a non-empty version string")

        path = _validate_provider_name(provider_name)
        data: dict = {"target_version": target_version}

        response = self._request("POST", f"/providers/installed/{path}/downgrade", json_data=data)
        return ProviderInstallation.model_validate(response)

    def deploy_provider(self, provider_name: str, version: str | None = None) -> DeploymentResult:
        """Deploy or redeploy an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version to deploy. If None, uses the installed version.

        Returns:
            DeploymentResult with deployment state and replica info.

        Raises:
            httpx.HTTPStatusError: If deploy fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {}

        if version is not None:
            data["version"] = version

        response = self._request("POST", f"/providers/installed/{path}/deploy", json_data=data)
        return DeploymentResult.model_validate(response)

    def get_deployment_status(self, provider_name: str) -> DeploymentResult:
        """Get deployment status for an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            DeploymentResult with status, version, replicas, and update timestamp.

        Raises:
            httpx.HTTPStatusError: If deployment not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("GET", f"/providers/installed/{path}/deployment")
        return DeploymentResult.model_validate(response)

    def list_organizations(self) -> list[Organization]:
        """List all organizations accessible to the current user.

        Returns:
            List of organizations.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/organizations")
        return [Organization.model_validate(item) for item in response]

    def get_organization(self, organization_id: str) -> Organization:
        """Get organization details by ID.

        Args:
            organization_id: Unique identifier for the organization.

        Returns:
            Organization details.

        Raises:
            httpx.HTTPStatusError: If organization not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/organizations/{organization_id}")
        return Organization.model_validate(response)

    def cleanup_organization(self, organization_id: str) -> None:
        """Trigger cleanup for an organization.

        Initiates resource teardown and deprovisioning for the organization.

        Args:
            organization_id: Unique identifier for the organization.

        Raises:
            httpx.HTTPStatusError: If organization not found or cleanup fails.
        """  # noqa: DOC502
        self._request("POST", f"/organizations/{organization_id}/cleanup")

    def list_agent_types(self, organization_id: str) -> list[AgentType]:
        """List agent types for an organization.

        Args:
            organization_id: Organization to list agent types for.

        Returns:
            List of agent types.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {"organization_id": organization_id}
        response = self._request("GET", "/agents/types", params=params)
        return [AgentType.model_validate(item) for item in response]

    def get_agent_type(self, agent_type_id: str) -> AgentType:
        """Get an agent type by ID.

        Args:
            agent_type_id: Unique identifier for the agent type.

        Returns:
            Agent type details.

        Raises:
            httpx.HTTPStatusError: If agent type not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/agents/types/{agent_type_id}")
        return AgentType.model_validate(response)

    def create_agent_type(self, data: AgentTypeCreate) -> AgentType:
        """Create a new agent type.

        Args:
            data: Agent type creation data.

        Returns:
            Created agent type.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = self._request("POST", "/agents/types", json_data=data.model_dump(exclude_none=True))
        return AgentType.model_validate(response)

    def update_agent_type(self, agent_type_id: str, data: AgentTypeUpdate) -> AgentType:
        """Update an existing agent type.

        Args:
            agent_type_id: Unique identifier for the agent type.
            data: Fields to update.

        Returns:
            Updated agent type.

        Raises:
            httpx.HTTPStatusError: If agent type not found or update fails.
        """  # noqa: DOC502
        response = self._request(
            "PATCH",
            f"/agents/types/{agent_type_id}",
            json_data=data.model_dump(exclude_unset=True),
        )
        return AgentType.model_validate(response)

    def delete_agent_type(self, agent_type_id: str) -> None:
        """Delete an agent type.

        Args:
            agent_type_id: Unique identifier for the agent type.

        Raises:
            httpx.HTTPStatusError: If agent type not found or deletion fails.
        """  # noqa: DOC502
        self._request("DELETE", f"/agents/types/{agent_type_id}")

    def list_agent_instances(
        self,
        organization_id: str,
        *,
        agent_type_id: str | None = None,
        status: AgentInstanceStatus | None = None,
    ) -> list[AgentInstance]:
        """List agent instances for an organization.

        Args:
            organization_id: Organization to list instances for.
            agent_type_id: Filter by agent type.
            status: Filter by instance status.

        Returns:
            List of agent instances.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"organization_id": organization_id}

        if agent_type_id is not None:
            params["agent_type_id"] = agent_type_id

        if status is not None:
            params["status"] = status

        response = self._request("GET", "/agents/instances", params=params)
        return [AgentInstance.model_validate(item) for item in response]

    def get_agent_instance(self, instance_id: str) -> AgentInstance:
        """Get an agent instance by ID.

        Args:
            instance_id: Unique identifier for the agent instance.

        Returns:
            Agent instance details.

        Raises:
            httpx.HTTPStatusError: If instance not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/agents/instances/{instance_id}")
        return AgentInstance.model_validate(response)

    def get_organization_settings(self, organization_id: str) -> OrganizationSettings:
        """Get LLM settings for an organization.

        Args:
            organization_id: Organization to fetch settings for.

        Returns:
            Current LLM provider resource and performance profile
            selection for the organization.

        Raises:
            httpx.HTTPStatusError: If organization not found or the
                request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/organizations/{organization_id}/settings")
        return OrganizationSettings.model_validate(response)

    def update_organization_settings(
        self,
        organization_id: str,
        provider: str,
        performance_profile: PerformanceProfile,
    ) -> OrganizationSettings:
        """Update LLM settings for an organization.

        Args:
            organization_id: Organization to update settings for.
            provider: Resource identifier of the LLM provider resource
                the organization is selecting.
            performance_profile: Performance profile tier to use for
                platform agent invocations.

        Returns:
            Updated organization settings as persisted by the API.

        Raises:
            httpx.HTTPStatusError: If the organization or referenced
                provider resource is not found, or if the update fails.
        """  # noqa: DOC502
        payload = {"provider": provider, "performance_profile": performance_profile}
        response = self._request(
            "PATCH",
            f"/organizations/{organization_id}/settings",
            json_data=payload,
        )
        return OrganizationSettings.model_validate(response)

    def get_cost_estimate(
        self,
        organization_id: str,
        provider: str,
        performance_profile: PerformanceProfile,
    ) -> CostEstimate:
        """Get a cost estimate for a proposed provider and profile.

        Used by the settings UI to show a live cost preview with a
        provider comparison row before the user commits to a change.

        Args:
            organization_id: Organization the estimate is scoped to.
            provider: Provider slug to estimate costs for (e.g.
                'anthropic', 'openai', 'google').
            performance_profile: Performance profile tier to estimate.

        Returns:
            Cost estimate including monthly and per-call projections plus
            a comparison row for alternative providers.

        Raises:
            httpx.HTTPStatusError: If the organization is not found or
                the request fails.
        """  # noqa: DOC502
        params = {"provider": provider, "profile": performance_profile}
        response = self._request(
            "GET",
            f"/organizations/{organization_id}/settings/cost-estimate",
            params=params,
        )
        return CostEstimate.model_validate(response)

    def list_llm_providers(self, organization_id: str) -> list[LLMProviderSummary]:
        """List LLM providers available to an organization.

        Returns the set of provider options the organization can select
        in the settings UI, including the shared platform-default entry
        and any LLM provider resources the organization has connected.

        Args:
            organization_id: Organization to list providers for.

        Returns:
            List of provider summaries ordered for display in the
            settings picker.

        Raises:
            httpx.HTTPStatusError: If the organization is not found or
                the request fails.
        """  # noqa: DOC502
        response = self._request(
            "GET",
            f"/organizations/{organization_id}/settings/available-providers",
        )
        return [LLMProviderSummary.model_validate(item) for item in response]

    def get_task_board(self) -> BoardSummary:
        """Fetch the board summary with task counts per status.

        Calls ``GET /agents/tasks/board``.

        Returns:
            Board summary with per-status counts and the total.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/agents/tasks/board")
        return BoardSummary.model_validate(response)

    def list_tasks(
        self,
        *,
        status: TaskStatus | str | None = None,
        assigned_to_instance_id: str | None = None,
    ) -> list[Task]:
        """List tasks for the authenticated organization.

        Calls ``GET /agents/tasks/``.

        Args:
            status: Filter by task status.
            assigned_to_instance_id: Filter by assigned agent instance.

        Returns:
            Tasks matching the filters.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {}

        if status is not None:
            params["status"] = status.value if isinstance(status, TaskStatus) else status

        if assigned_to_instance_id is not None:
            params["assigned_to_instance_id"] = assigned_to_instance_id

        response = self._request("GET", "/agents/tasks/", params=params or None)
        return [Task.model_validate(item) for item in response]

    def get_task(self, task_id: str) -> Task:
        """Fetch a single task by ID.

        Calls ``GET /agents/tasks/{task_id}``.

        Args:
            task_id: Task identifier.

        Returns:
            The requested task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/agents/tasks/{quote(task_id, safe='')}")
        return Task.model_validate(response)

    def create_task(self, data: TaskCreate) -> Task:
        """Create a new task.

        Calls ``POST /agents/tasks/``. The owning organization and creator
        are taken from the authenticated request context.

        Args:
            data: Task creation payload.

        Returns:
            The created task with server-populated fields.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/tasks/",
            json_data=data.model_dump(exclude_none=True),
        )
        return Task.model_validate(response)

    def update_task(self, task_id: str, data: TaskUpdate) -> Task:
        """Update an existing task.

        Calls ``PATCH /agents/tasks/{task_id}``. Only the provided fields
        are updated. Status changes are not allowed here — use
        :meth:`transition_task` instead.

        Args:
            task_id: Task identifier.
            data: Fields to update.

        Returns:
            Updated task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the update fails.
        """  # noqa: DOC502
        response = self._request(
            "PATCH",
            f"/agents/tasks/{quote(task_id, safe='')}",
            json_data=data.model_dump(exclude_unset=True),
        )
        return Task.model_validate(response)

    def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Calls ``DELETE /agents/tasks/{task_id}``.

        Args:
            task_id: Task identifier.

        Raises:
            httpx.HTTPStatusError: If the task is not found or deletion fails.
        """  # noqa: DOC502
        self._request("DELETE", f"/agents/tasks/{quote(task_id, safe='')}")

    def assign_task(self, task_id: str, data: TaskAssign) -> Task:
        """Assign a task to an agent instance, user, or agent type.

        Calls ``POST /agents/tasks/{task_id}/assign``. Exactly one of
        ``instance_id``, ``user_id``, or ``type_id`` should be provided
        on ``data``.

        Args:
            task_id: Task identifier.
            data: Assignment payload.

        Returns:
            Updated task with the new assignment.

        Raises:
            httpx.HTTPStatusError: If the task is not found or assignment fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/assign",
            json_data=data.model_dump(exclude_none=True),
        )
        return Task.model_validate(response)

    def transition_task(self, task_id: str, data: TaskTransition) -> Task:
        """Transition a task to a new status.

        Calls ``POST /agents/tasks/{task_id}/transition``. The transition
        is validated against the allowed status graph on the server;
        invalid transitions return ``InvalidLifecycleTransitionError``.

        Args:
            task_id: Task identifier.
            data: Transition payload carrying the target status.

        Returns:
            Updated task with the new status.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the transition is rejected.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/transition",
            json_data=data.model_dump(),
        )
        return Task.model_validate(response)

    def list_subtasks(self, task_id: str) -> list[Task]:
        """List direct subtasks of a task.

        Calls ``GET /agents/tasks/{task_id}/subtasks``. Subtask
        relationships are graph edges, so this method traverses the
        ``has_subtask`` edges starting at ``task_id``.

        Args:
            task_id: Parent task identifier.

        Returns:
            Direct child tasks ordered by priority then creation time.

        Raises:
            httpx.HTTPStatusError: If the parent task is not found or the request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/agents/tasks/{quote(task_id, safe='')}/subtasks")
        return [Task.model_validate(item) for item in response]

    def create_subtask(self, task_id: str, data: TaskCreate) -> Task:
        """Create a new subtask under an existing task.

        Calls ``POST /agents/tasks/{task_id}/subtasks``. The new task
        inherits priority and assignee fields from the parent when the
        request body leaves them at their defaults; the ``has_subtask``
        edge is created server-side as part of the same operation.

        Args:
            task_id: Parent task identifier.
            data: Subtask creation payload.

        Returns:
            The created subtask.

        Raises:
            httpx.HTTPStatusError: If the parent task is not found or creation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks",
            json_data=data.model_dump(exclude_unset=True),
        )
        return Task.model_validate(response)

    def link_subtask(self, task_id: str, child_id: str) -> None:
        """Link an existing task as a subtask of another task.

        Calls ``POST /agents/tasks/{task_id}/subtasks/link``. The server
        rejects links that would create a cycle in the subtask graph.

        Args:
            task_id: Parent task identifier.
            child_id: Existing task to attach as a subtask.

        Raises:
            httpx.HTTPStatusError: If either task is not found or the
                link would create a cycle.
        """  # noqa: DOC502
        self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks/link",
            json_data={"child_id": child_id},
        )

    def unlink_subtask(self, task_id: str, child_id: str) -> None:
        """Remove the ``has_subtask`` edge between two tasks.

        Calls ``DELETE /agents/tasks/{task_id}/subtasks/{child_id}``. The
        child task is not deleted; only the link is removed.

        Args:
            task_id: Parent task identifier.
            child_id: Subtask identifier to unlink.

        Raises:
            httpx.HTTPStatusError: If either task is not found or the
                request fails.
        """  # noqa: DOC502
        self._request(
            "DELETE",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks/{quote(child_id, safe='')}",
        )

    def list_task_comments(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[TaskComment]:
        """List comments on a task, oldest first.

        Calls ``GET /agents/tasks/{task_id}/comments``.

        Args:
            task_id: Task identifier.
            limit: Maximum comments to return (1-200, default 50).
            cursor: Composite cursor ``"<iso-created_at>|<comment-id>"``
                returned by a previous call. Returns comments strictly
                after this point in ``(created_at ASC, id ASC)`` order.

        Returns:
            Comments for the requested page, oldest first.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit}

        if cursor is not None:
            params["cursor"] = cursor

        response = self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/comments",
            params=params,
        )
        return [TaskComment.model_validate(item) for item in response]

    def create_task_comment(self, task_id: str, data: TaskCommentCreate) -> TaskComment:
        """Create a comment on a task.

        Calls ``POST /agents/tasks/{task_id}/comments``. Authorship is
        taken from the authenticated user — agent comments are written
        by the runtime through a separate code path and are not
        creatable via this method.

        Args:
            task_id: Task identifier.
            data: Comment creation payload.

        Returns:
            Persisted comment with the server-populated id.

        Raises:
            httpx.HTTPStatusError: If the task is not found or creation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/comments",
            json_data=data.model_dump(),
        )
        return TaskComment.model_validate(response)

    def update_task_comment(
        self,
        comment_id: str,
        data: TaskCommentUpdate,
    ) -> TaskComment:
        """Edit a task comment. Only the original author may edit.

        Calls ``PATCH /agents/tasks/comments/{comment_id}``.

        Args:
            comment_id: Comment identifier.
            data: New body to replace the existing one.

        Returns:
            Updated comment with ``edited=True``.

        Raises:
            httpx.HTTPStatusError: If the comment is not found or the
                actor is not the author.
        """  # noqa: DOC502
        response = self._request(
            "PATCH",
            f"/agents/tasks/comments/{quote(comment_id, safe='')}",
            json_data=data.model_dump(),
        )
        return TaskComment.model_validate(response)

    def delete_task_comment(self, comment_id: str) -> None:
        """Delete a task comment. Only the original author may delete.

        Calls ``DELETE /agents/tasks/comments/{comment_id}``.

        Args:
            comment_id: Comment identifier.

        Raises:
            httpx.HTTPStatusError: If the comment is not found or the
                actor is not the author.
        """  # noqa: DOC502
        self._request("DELETE", f"/agents/tasks/comments/{quote(comment_id, safe='')}")

    def list_task_activity(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[TaskActivityEntry]:
        """List the activity timeline for a task, newest first.

        Calls ``GET /agents/tasks/{task_id}/activity``. Combines status
        transitions, assignments, comments, agent start events, and
        resource mutation summaries into a single newest-first stream.
        Mutation entries surface only the operation and changed field
        names; full before/after snapshots live on the mutation log.

        Args:
            task_id: Task identifier.
            limit: Maximum entries to return (1-200, default 50).
            cursor: Composite cursor ``"<iso-timestamp>|<edge-id>"``
                returned by a previous call. Returns entries strictly
                older than this point in
                ``(timestamp DESC, edge_id DESC)`` order.

        Returns:
            Activity entries for the requested page, newest first.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit}

        if cursor is not None:
            params["cursor"] = cursor

        response = self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/activity",
            params=params,
        )
        return [TaskActivityEntry.model_validate(item) for item in response]

    def list_task_mutations(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        reveal: bool = False,
    ) -> TaskMutationPage:
        """Return the paginated mutation log for a task.

        Calls ``GET /agents/tasks/{task_id}/mutations``. Each entry
        carries a full before/after snapshot plus the list of fields
        that changed. Sensitive fields are masked by default — pass
        ``reveal=True`` to receive actual values.

        Args:
            task_id: Task identifier.
            limit: Maximum mutations per page (1-200, default 50).
            cursor: Composite cursor ``"<iso-timestamp>|<edge-id>"``
                returned by a previous call. Returns mutations strictly
                older than this point in
                ``(timestamp DESC, edge_id DESC)`` order.
            reveal: When True, return actual values for sensitive
                fields on the before/after snapshots.

        Returns:
            One page of the mutation log plus a ``next_cursor`` for the
            next page (``None`` when there are no more entries).

        Raises:
            httpx.HTTPStatusError: If the task is not found, the cursor
                is malformed, or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit, "reveal": reveal}

        if cursor is not None:
            params["cursor"] = cursor

        response = self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/mutations",
            params=params,
        )
        return TaskMutationPage.model_validate(response)

    def get_task_graph_diff(self, task_id: str, *, reveal: bool = False) -> GraphDiff:
        """Return the net delta per resource for a task.

        Calls ``GET /agents/tasks/{task_id}/graph-diff``. The server
        rolls every ``task->mutated->resource`` edge into a per-resource
        net delta — a create + many updates collapse to a single
        ``create``, a create + delete collapses to ``noop``, etc.

        The endpoint scans up to a server-configured cap of mutations
        per request. Tasks that exceed the cap return a partial rollup
        with ``GraphDiff.truncated=True`` and ``GraphDiff.has_more=True``;
        callers should direct users to :meth:`list_task_mutations` for
        the full audit trail. Sensitive fields on the snapshots are
        masked by default — pass ``reveal=True`` for actual values.

        Args:
            task_id: Task identifier.
            reveal: When True, return actual values for sensitive fields
                on the before/after snapshots.

        Returns:
            Per-resource net deltas plus the truncation flag and
            scanned-mutation count.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"reveal": reveal}
        response = self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/graph-diff",
            params=params,
        )
        return GraphDiff.model_validate(response)

    def improve_task(self, data: ImproveTaskRequest) -> ImproveTaskResponse:
        """Run the ``improve-task`` AI assist on a draft task.

        Calls ``POST /agents/assists/improve-task``. Returns a refined
        title, description, and a short rationale explaining the change.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``fast`` model tier.

        Args:
            data: Existing title and (optional) description to refine.

        Returns:
            Suggested replacement title, description, and rationale.

        Raises:
            httpx.HTTPStatusError: If the assist invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/improve-task",
            json_data=data.model_dump(exclude_none=True),
        )
        return ImproveTaskResponse.model_validate(response)

    def explain_task(self, data: ExplainTaskRequest) -> ExplainTaskResponse:
        """Run the ``explain-task`` AI assist for a task.

        Calls ``POST /agents/assists/explain-task``. The server fetches
        the task referenced by ``task_id`` and, when provided, the
        correlation bucket, then returns a plain-language summary, key
        points, and a suggested next action. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``fast`` model
        tier.

        Args:
            data: Task identifier and optional correlation bucket
                providing richer context.

        Returns:
            Summary, key points, and suggested next action for the task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/explain-task",
            json_data=data.model_dump(exclude_none=True),
        )
        return ExplainTaskResponse.model_validate(response)

    def summarize_thread(self, data: SummarizeThreadRequest) -> SummarizeThreadResponse:
        """Run the ``summarize-thread`` AI assist on a task's comments.

        Calls ``POST /agents/assists/summarize-thread``. The server
        pulls the comment thread for ``task_id`` and returns a summary
        plus structured decisions, open questions, and action items.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``fast`` model tier.

        Args:
            data: Task whose comment thread should be summarized.

        Returns:
            Summary, decisions, open questions, and action items.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/summarize-thread",
            json_data=data.model_dump(exclude_none=True),
        )
        return SummarizeThreadResponse.model_validate(response)

    def suggest_assignee(self, data: SuggestAssigneeRequest) -> SuggestAssigneeResponse:
        """Run the ``suggest-assignee`` AI assist for a task.

        Calls ``POST /agents/assists/suggest-assignee``. Ranks the
        provided candidates and returns the best fit with a rationale
        and confidence score in ``[0.0, 1.0]``. Dispatched server-side
        via ``AgentInvoker.invoke_platform_agent()`` on the ``fast``
        model tier.

        Args:
            data: Task identifier and the candidate agent instances to
                consider.

        Returns:
            The recommended candidate's instance ID, the rationale for
            the pick, and the model's confidence.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/suggest-assignee",
            json_data=data.model_dump(exclude_none=True),
        )
        return SuggestAssigneeResponse.model_validate(response)

    def generate_subtasks(self, data: GenerateSubtasksRequest) -> GenerateSubtasksResponse:
        """Run the ``generate-subtasks`` AI assist.

        Calls ``POST /agents/assists/generate-subtasks``. Returns a list
        of proposed subtasks for the caller to review before persisting.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``balanced`` model tier.

        Args:
            data: Title and description of the parent task, or
                ``parent_context`` when generating subtasks under an
                already-created task.

        Returns:
            Ordered list of proposed subtasks.

        Raises:
            httpx.HTTPStatusError: If the assist invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/generate-subtasks",
            json_data=data.model_dump(exclude_none=True),
        )
        return GenerateSubtasksResponse.model_validate(response)

    def review_summary(self, data: ReviewSummaryRequest) -> ReviewSummaryResponse:
        """Run the ``review-summary`` AI assist on a task's changes.

        Calls ``POST /agents/assists/review-summary``. The server pulls
        the task's graph diff, affected resources, and risk signals,
        and returns a summary, a risk classification, and a reviewer
        checklist. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``balanced``
        model tier.

        Args:
            data: Task identifier whose proposed changes should be
                reviewed.

        Returns:
            Summary, risk level (``low``/``medium``/``high``), and
            review checklist.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/review-summary",
            json_data=data.model_dump(exclude_none=True),
        )
        return ReviewSummaryResponse.model_validate(response)

    def deep_analysis(self, data: DeepAnalysisRequest) -> DeepAnalysisResponse:
        """Run the ``deep-analysis`` AI assist for a task.

        Calls ``POST /agents/assists/deep-analysis``. Answers a free-form
        question grounded in the referenced task, returning analysis,
        concerns, and recommendations. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``reasoning``
        model tier.

        Args:
            data: Task identifier anchoring the analysis and the
                free-form question to answer.

        Returns:
            Detailed analysis, surfaced concerns, and recommendations.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            "/agents/assists/deep-analysis",
            json_data=data.model_dump(exclude_none=True),
        )
        return DeepAnalysisResponse.model_validate(response)


class AsyncPragmaClient(BaseClient):
    """Asynchronous client for the Pragma API.

    Example:
        >>> async with AsyncPragmaClient() as client:
        ...     resources = await client.list_resources(provider="example")
        ...     resource = await client.get_resource("example", "database", "my-db")
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        auth_token: str | None | object = ...,
        context: str | None = None,
        require_auth: bool = False,
    ):
        """Initialize the asynchronous Pragma client.

        See BaseClient for parameter documentation.
        """
        super().__init__(base_url, timeout, auth_token, context, require_auth)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, auth=self._auth)

    async def __aenter__(self):
        """Enter async context manager.

        Returns:
            Self for use in async with statement.
        """
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager and close client."""
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make an HTTP request to the Pragma API.

        Returns:
            Parsed JSON response, raw text, or None for 204 responses.

        Raises:
            httpx.HTTPStatusError: If the API returns an error response.
        """  # noqa: DOC502
        response = await self._client.request(
            method=method,
            url=path,
            params=params,
            json=json_data,
            **kwargs,
        )

        response.raise_for_status()
        if response.status_code == 204:
            return None
        if response.headers.get("content-type") == "application/json":
            return response.json()
        return response.text

    async def is_healthy(self) -> bool:
        """Check if the Pragma API is healthy.

        Returns:
            True if API returns healthy status, False otherwise.
        """
        try:
            response = await self._request("GET", "/health")
            return response.get("status") == "ok"
        except httpx.HTTPError:
            return False

    async def get_me(self) -> UserInfo:
        """Get current authenticated user information.

        Returns:
            UserInfo with user ID, email, organization ID and name.
        """
        response = await self._request("GET", "/auth/me")
        return UserInfo.model_validate(response)

    def project(self, project_id: str) -> AsyncProjectResources:
        """Build a project-scoped async resource handle.

        Args:
            project_id: Project identifier to scope operations to.

        Returns:
            :class:`AsyncProjectResources` bound to this client.

        Raises:
            InvalidResourceIdentityError: If ``project_id`` is empty, contains
                the reserved ``::`` separator, or has control characters.
        """  # noqa: DOC502
        return AsyncProjectResources(self, project_id)

    async def list_projects(self) -> list[Project]:
        """List projects visible to the current caller.

        Returns:
            Projects owned by the caller's organization. Private projects
            are never returned.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/projects")
        return [Project.model_validate(item) for item in response]

    async def get_project(self, project_id: str) -> Project:
        """Fetch a single project by ID.

        Args:
            project_id: Project identifier.

        Returns:
            The project with metadata.

        Raises:
            httpx.HTTPStatusError: If the project is not found or the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/projects/{quote(project_id, safe='')}")
        return Project.model_validate(response)

    async def create_project(self, request: CreateProjectRequest) -> Project:
        """Create a new project.

        Args:
            request: Project creation payload.

        Returns:
            The newly created project.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = await self._request("POST", "/projects", json_data=request.model_dump(exclude_none=True))
        return Project.model_validate(response)

    async def update_project(self, project_id: str, request: UpdateProjectRequest) -> Project:
        """Update project metadata.

        Args:
            project_id: Project identifier.
            request: Fields to update. ``slug`` is immutable and not included.

        Returns:
            The updated project.

        Raises:
            httpx.HTTPStatusError: If the project is not found or the update fails.
        """  # noqa: DOC502
        response = await self._request(
            "PATCH",
            f"/projects/{quote(project_id, safe='')}",
            json_data=request.model_dump(exclude_none=True),
        )
        return Project.model_validate(response)

    async def delete_project(self, project_id: str, request: DeleteProjectRequest) -> None:
        """Hard-delete a project with typed confirmation.

        The caller must pass the project's slug in ``request.confirmation``;
        the server rejects the request if the value does not match. By
        default the server also refuses to delete a project that still
        holds resources. Set ``request.orphan_resources`` to ``True`` to
        bypass that safety check and orphan the resources.

        Args:
            project_id: Project identifier to delete.
            request: Confirmation payload carrying the project's slug and
                optional ``orphan_resources`` flag.

        Raises:
            ProjectHasResourcesError: If the server returns 409 with a
                project-has-resources body, typically because the project
                still contains resources and ``orphan_resources`` was not
                set.
            httpx.HTTPStatusError: If confirmation fails, the project is
                not found, the 409 body does not match the expected
                project-has-resources shape, or the request otherwise
                fails.
        """  # noqa: DOC502
        try:
            await self._request(
                "DELETE",
                f"/projects/{quote(project_id, safe='')}",
                json_data=request.model_dump(),
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                _raise_project_has_resources(error)
            raise

    async def list_resource_schemas(self, provider: str | None = None) -> list[ResourceSchema]:
        """List available resource schemas from deployed providers.

        Args:
            provider: Filter by provider name.

        Returns:
            List of resource schemas containing provider, resource, schema, description.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        response = await self._request("GET", "/resources/schemas", params=params)
        return [ResourceSchema.model_validate(item) for item in response]

    async def list_dead_letter_events(self, provider: str | None = None) -> list[dict[str, Any]]:
        """List dead letter events with optional provider filter.

        Args:
            provider: Filter by provider name.

        Returns:
            List of dead letter events as raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        return await self._request("GET", "/ops/dead-letter", params=params)

    async def get_dead_letter_event(self, event_id: str) -> dict[str, Any]:
        """Get a dead letter event by ID.

        Args:
            event_id: The dead letter event ID.

        Returns:
            Dead letter event as raw dict.

        Raises:
            httpx.HTTPStatusError: If event not found or request fails.
        """  # noqa: DOC502
        return await self._request("GET", f"/ops/dead-letter/{event_id}")

    async def retry_dead_letter_event(self, event_id: str) -> None:
        """Retry a dead letter event.

        Args:
            event_id: The dead letter event ID to retry.

        Raises:
            httpx.HTTPStatusError: If event not found or retry fails.
        """  # noqa: DOC502
        await self._request("POST", f"/ops/dead-letter/{event_id}/retry")

    async def retry_all_dead_letter_events(self) -> int:
        """Retry all dead letter events.

        Returns:
            Number of events retried.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("POST", "/ops/dead-letter/retry-all")
        return response["retried_count"]

    async def delete_dead_letter_event(self, event_id: str) -> None:
        """Delete a dead letter event.

        Args:
            event_id: The dead letter event ID to delete.

        Raises:
            httpx.HTTPStatusError: If event not found or deletion fails.
        """  # noqa: DOC502
        await self._request("DELETE", f"/ops/dead-letter/{event_id}")

    async def delete_dead_letter_events(self, provider: str | None = None, *, all: bool = False) -> int:
        """Delete multiple dead letter events.

        Args:
            provider: Delete events for this provider only.
            all: Delete all dead letter events (ignores provider filter).

        Returns:
            Number of events deleted.

        Raises:
            ValueError: If neither provider nor all is specified.
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        if not provider and not all:
            raise ValueError("Must specify either provider or all=True")

        params: dict[str, Any] = {}
        if all:
            params["all"] = "true"
        elif provider:
            params["provider"] = provider

        response = await self._request("DELETE", "/ops/dead-letter", params=params)
        return response["deleted_count"]

    async def upload_file(self, name: str, content: bytes, content_type: str) -> dict[str, Any]:
        """Upload a file to the Pragma file storage.

        Args:
            name: Name of the file (used in the storage path).
            content: Raw file content as bytes.
            content_type: MIME type of the file (e.g., "image/png", "application/pdf").

        Returns:
            Dict containing url, public_url, size, content_type, checksum, uploaded_at.

        Raises:
            httpx.HTTPStatusError: If the upload fails.
        """  # noqa: DOC502
        return await self._request(
            "POST",
            f"/files/{name}/upload",
            files={"file": (name, content, content_type)},
        )

    async def list_providers(
        self,
        query: str | None = None,
        scope: ProviderScope | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[Provider]:
        """Browse and search the provider catalog.

        Args:
            query: Search query string.
            scope: Filter by provider scope (e.g. 'public', 'tenant').
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of providers.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if query is not None:
            params["q"] = query

        if scope is not None:
            params["scope"] = scope

        if tags is not None:
            params["tags"] = tags

        response = await self._request("GET", "/providers", params=params)
        return PaginatedResponse[Provider].model_validate(response)

    async def get_provider(self, provider_name: str) -> Provider:
        """Get provider info.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            Provider metadata.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("GET", f"/providers/{path}")
        return Provider.model_validate(response)

    async def list_provider_versions(self, provider_name: str) -> list[ProviderVersion]:
        """List all versions of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            List of provider versions.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("GET", f"/providers/{path}/versions")
        return [ProviderVersion.model_validate(item) for item in response]

    async def update_provider(self, provider_name: str, metadata: dict[str, Any]) -> Provider:
        """Update provider metadata.

        Args:
            provider_name: Namespaced provider name ('org/name').
            metadata: Fields to update (e.g. display_name, description, tags).

        Returns:
            Updated provider.

        Raises:
            httpx.HTTPStatusError: If provider not found or update fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("PATCH", f"/providers/{path}", json_data=metadata)
        return Provider.model_validate(response)

    async def delete_provider(self, provider_name: str) -> None:
        """Delete a provider from the catalog.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Raises:
            httpx.HTTPStatusError: If provider not found or deletion fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        await self._request("DELETE", f"/providers/{path}")

    async def register_provider_version(
        self,
        *,
        name: str,
        version: str,
        wheel_url: str,
        sha256: str,
        schemas: dict[str, dict[str, Any]],
        metadata: dict[str, Any],
        changelog: str | None = None,
    ) -> ProviderVersion:
        """Register a new provider version against an externally hosted wheel.

        Async mirror of :meth:`PragmaClient.register_provider_version`.
        The SDK does not build, upload, or hash the wheel — the caller
        is responsible for placing the wheel on any HTTPS host and
        supplying the URL plus SHA-256 digest. This method only POSTs
        the metadata record to ``POST /provider-versions`` and returns
        the persisted version.

        Args:
            name: Namespaced provider name in ``"org/short"`` form
                (e.g. ``"pragmatiks/gcp"``).
            version: Semver string for this release.
            wheel_url: HTTPS URL ending in ``.whl``.
            sha256: 64-character lowercase hex SHA-256 of the wheel; the
                API verifies it after fetching the URL.
            schemas: Per-resource schema map keyed by resource type
                name, in the shape the API expects on the request body.
            metadata: Catalog display fields (``display_name``,
                ``description``, ``icon_url``, ``tags``).
            changelog: Optional release notes for this version.

        Returns:
            The persisted ``ProviderVersion``.

        Raises:
            ValueError: If ``wheel_url`` is not HTTPS, does not end in
                ``.whl``, or ``sha256`` is not 64 lowercase hex chars.
            httpx.HTTPStatusError: If the API rejects the request (e.g.
                already-published version, schema validation failure,
                unreachable wheel URL).
        """  # noqa: DOC502
        body = _build_provider_version_request_body(
            name=name,
            version=version,
            wheel_url=_validate_wheel_url(wheel_url),
            sha256=_validate_sha256(sha256),
            schemas=schemas,
            metadata=metadata,
            changelog=changelog,
        )

        response = await self._request("POST", "/provider-versions", json_data=body)
        return ProviderVersion.model_validate(response)

    async def install_provider(
        self,
        provider_name: str,
        version: str | None = None,
        resource_tier: ResourceTier | str = ResourceTier.STANDARD,
        upgrade_policy: UpgradePolicy | str = UpgradePolicy.MANUAL,
        config: dict[str, str] | None = None,
    ) -> ProviderInstallation:
        """Install a provider from the catalog.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.
            config: Key-value pairs injected as environment variables
                on the provider deployment.

        Returns:
            Installed provider info.

        Raises:
            httpx.HTTPStatusError: If installation fails.
        """  # noqa: DOC502
        _validate_provider_name(provider_name)
        data: dict = {
            "provider_name": provider_name,
            "resource_tier": resource_tier,
            "upgrade_policy": upgrade_policy,
        }

        if version is not None:
            data["version"] = version

        if config is not None:
            data["config"] = config

        response = await self._request("POST", "/providers/install", json_data=data)
        return ProviderInstallation.model_validate(response)

    async def list_installations(self) -> list[ProviderInstallation]:
        """List installed providers for the current tenant.

        Returns:
            List of provider installations.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/providers/installed")
        return [ProviderInstallation.model_validate(item) for item in response]

    async def uninstall_provider(self, provider_name: str, *, cascade: bool = False) -> None:
        """Uninstall an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            cascade: If True, delete all resources managed by this provider.

        Raises:
            httpx.HTTPStatusError: If uninstall fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        params = {}

        if cascade:
            params["cascade"] = "true"

        await self._request("DELETE", f"/providers/installed/{path}", params=params)

    async def upgrade_provider(self, provider_name: str, target_version: str | None = None) -> ProviderInstallation:
        """Upgrade an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version (latest if None).

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If upgrade fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {}

        if target_version is not None:
            data["version"] = target_version

        response = await self._request("POST", f"/providers/installed/{path}/upgrade", json_data=data)
        return ProviderInstallation.model_validate(response)

    async def downgrade_provider(self, provider_name: str, target_version: str) -> ProviderInstallation:
        """Downgrade an installed provider to a specific version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version to downgrade to.

        Returns:
            Updated installed provider info.

        Raises:
            ValueError: If target_version is empty or whitespace.
            httpx.HTTPStatusError: If downgrade fails.
        """  # noqa: DOC502
        if not target_version.strip():
            raise ValueError("target_version must be a non-empty version string")

        path = _validate_provider_name(provider_name)
        data: dict = {"target_version": target_version}

        response = await self._request("POST", f"/providers/installed/{path}/downgrade", json_data=data)
        return ProviderInstallation.model_validate(response)

    async def deploy_provider(self, provider_name: str, version: str | None = None) -> DeploymentResult:
        """Deploy or redeploy an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version to deploy. If None, uses the installed version.

        Returns:
            DeploymentResult with deployment state and replica info.

        Raises:
            httpx.HTTPStatusError: If deploy fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {}

        if version is not None:
            data["version"] = version

        response = await self._request("POST", f"/providers/installed/{path}/deploy", json_data=data)
        return DeploymentResult.model_validate(response)

    async def get_deployment_status(self, provider_name: str) -> DeploymentResult:
        """Get deployment status for an installed provider.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            DeploymentResult with status, version, replicas, and update timestamp.

        Raises:
            httpx.HTTPStatusError: If deployment not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("GET", f"/providers/installed/{path}/deployment")
        return DeploymentResult.model_validate(response)

    async def list_organizations(self) -> list[Organization]:
        """List all organizations accessible to the current user.

        Returns:
            List of organizations.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/organizations")
        return [Organization.model_validate(item) for item in response]

    async def get_organization(self, organization_id: str) -> Organization:
        """Get organization details by ID.

        Args:
            organization_id: Unique identifier for the organization.

        Returns:
            Organization details.

        Raises:
            httpx.HTTPStatusError: If organization not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/organizations/{organization_id}")
        return Organization.model_validate(response)

    async def cleanup_organization(self, organization_id: str) -> None:
        """Trigger cleanup for an organization.

        Initiates resource teardown and deprovisioning for the organization.

        Args:
            organization_id: Unique identifier for the organization.

        Raises:
            httpx.HTTPStatusError: If organization not found or cleanup fails.
        """  # noqa: DOC502
        await self._request("POST", f"/organizations/{organization_id}/cleanup")

    async def list_agent_types(self, organization_id: str) -> list[AgentType]:
        """List agent types for an organization.

        Args:
            organization_id: Organization to list agent types for.

        Returns:
            List of agent types.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {"organization_id": organization_id}
        response = await self._request("GET", "/agents/types", params=params)
        return [AgentType.model_validate(item) for item in response]

    async def get_agent_type(self, agent_type_id: str) -> AgentType:
        """Get an agent type by ID.

        Args:
            agent_type_id: Unique identifier for the agent type.

        Returns:
            Agent type details.

        Raises:
            httpx.HTTPStatusError: If agent type not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/agents/types/{agent_type_id}")
        return AgentType.model_validate(response)

    async def create_agent_type(self, data: AgentTypeCreate) -> AgentType:
        """Create a new agent type.

        Args:
            data: Agent type creation data.

        Returns:
            Created agent type.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = await self._request("POST", "/agents/types", json_data=data.model_dump(exclude_none=True))
        return AgentType.model_validate(response)

    async def update_agent_type(self, agent_type_id: str, data: AgentTypeUpdate) -> AgentType:
        """Update an existing agent type.

        Args:
            agent_type_id: Unique identifier for the agent type.
            data: Fields to update.

        Returns:
            Updated agent type.

        Raises:
            httpx.HTTPStatusError: If agent type not found or update fails.
        """  # noqa: DOC502
        response = await self._request(
            "PATCH",
            f"/agents/types/{agent_type_id}",
            json_data=data.model_dump(exclude_unset=True),
        )
        return AgentType.model_validate(response)

    async def delete_agent_type(self, agent_type_id: str) -> None:
        """Delete an agent type.

        Args:
            agent_type_id: Unique identifier for the agent type.

        Raises:
            httpx.HTTPStatusError: If agent type not found or deletion fails.
        """  # noqa: DOC502
        await self._request("DELETE", f"/agents/types/{agent_type_id}")

    async def list_agent_instances(
        self,
        organization_id: str,
        *,
        agent_type_id: str | None = None,
        status: AgentInstanceStatus | None = None,
    ) -> list[AgentInstance]:
        """List agent instances for an organization.

        Args:
            organization_id: Organization to list instances for.
            agent_type_id: Filter by agent type.
            status: Filter by instance status.

        Returns:
            List of agent instances.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"organization_id": organization_id}

        if agent_type_id is not None:
            params["agent_type_id"] = agent_type_id

        if status is not None:
            params["status"] = status

        response = await self._request("GET", "/agents/instances", params=params)
        return [AgentInstance.model_validate(item) for item in response]

    async def get_agent_instance(self, instance_id: str) -> AgentInstance:
        """Get an agent instance by ID.

        Args:
            instance_id: Unique identifier for the agent instance.

        Returns:
            Agent instance details.

        Raises:
            httpx.HTTPStatusError: If instance not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/agents/instances/{instance_id}")
        return AgentInstance.model_validate(response)

    async def get_organization_settings(self, organization_id: str) -> OrganizationSettings:
        """Get LLM settings for an organization.

        Args:
            organization_id: Organization to fetch settings for.

        Returns:
            Current LLM provider resource and performance profile
            selection for the organization.

        Raises:
            httpx.HTTPStatusError: If organization not found or the
                request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/organizations/{organization_id}/settings")
        return OrganizationSettings.model_validate(response)

    async def update_organization_settings(
        self,
        organization_id: str,
        provider: str,
        performance_profile: PerformanceProfile,
    ) -> OrganizationSettings:
        """Update LLM settings for an organization.

        Args:
            organization_id: Organization to update settings for.
            provider: Resource identifier of the LLM provider resource
                the organization is selecting.
            performance_profile: Performance profile tier to use for
                platform agent invocations.

        Returns:
            Updated organization settings as persisted by the API.

        Raises:
            httpx.HTTPStatusError: If the organization or referenced
                provider resource is not found, or if the update fails.
        """  # noqa: DOC502
        payload = {"provider": provider, "performance_profile": performance_profile}
        response = await self._request(
            "PATCH",
            f"/organizations/{organization_id}/settings",
            json_data=payload,
        )
        return OrganizationSettings.model_validate(response)

    async def get_cost_estimate(
        self,
        organization_id: str,
        provider: str,
        performance_profile: PerformanceProfile,
    ) -> CostEstimate:
        """Get a cost estimate for a proposed provider and profile.

        Used by the settings UI to show a live cost preview with a
        provider comparison row before the user commits to a change.

        Args:
            organization_id: Organization the estimate is scoped to.
            provider: Provider slug to estimate costs for (e.g.
                'anthropic', 'openai', 'google').
            performance_profile: Performance profile tier to estimate.

        Returns:
            Cost estimate including monthly and per-call projections plus
            a comparison row for alternative providers.

        Raises:
            httpx.HTTPStatusError: If the organization is not found or
                the request fails.
        """  # noqa: DOC502
        params = {"provider": provider, "profile": performance_profile}
        response = await self._request(
            "GET",
            f"/organizations/{organization_id}/settings/cost-estimate",
            params=params,
        )
        return CostEstimate.model_validate(response)

    async def list_llm_providers(self, organization_id: str) -> list[LLMProviderSummary]:
        """List LLM providers available to an organization.

        Returns the set of provider options the organization can select
        in the settings UI, including the shared platform-default entry
        and any LLM provider resources the organization has connected.

        Args:
            organization_id: Organization to list providers for.

        Returns:
            List of provider summaries ordered for display in the
            settings picker.

        Raises:
            httpx.HTTPStatusError: If the organization is not found or
                the request fails.
        """  # noqa: DOC502
        response = await self._request(
            "GET",
            f"/organizations/{organization_id}/settings/available-providers",
        )
        return [LLMProviderSummary.model_validate(item) for item in response]

    async def get_task_board(self) -> BoardSummary:
        """Fetch the board summary with task counts per status.

        Calls ``GET /agents/tasks/board``.

        Returns:
            Board summary with per-status counts and the total.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/agents/tasks/board")
        return BoardSummary.model_validate(response)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | str | None = None,
        assigned_to_instance_id: str | None = None,
    ) -> list[Task]:
        """List tasks for the authenticated organization.

        Calls ``GET /agents/tasks/``.

        Args:
            status: Filter by task status.
            assigned_to_instance_id: Filter by assigned agent instance.

        Returns:
            Tasks matching the filters.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {}

        if status is not None:
            params["status"] = status.value if isinstance(status, TaskStatus) else status

        if assigned_to_instance_id is not None:
            params["assigned_to_instance_id"] = assigned_to_instance_id

        response = await self._request("GET", "/agents/tasks/", params=params or None)
        return [Task.model_validate(item) for item in response]

    async def get_task(self, task_id: str) -> Task:
        """Fetch a single task by ID.

        Calls ``GET /agents/tasks/{task_id}``.

        Args:
            task_id: Task identifier.

        Returns:
            The requested task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/agents/tasks/{quote(task_id, safe='')}")
        return Task.model_validate(response)

    async def create_task(self, data: TaskCreate) -> Task:
        """Create a new task.

        Calls ``POST /agents/tasks/``. The owning organization and creator
        are taken from the authenticated request context.

        Args:
            data: Task creation payload.

        Returns:
            The created task with server-populated fields.

        Raises:
            httpx.HTTPStatusError: If creation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/tasks/",
            json_data=data.model_dump(exclude_none=True),
        )
        return Task.model_validate(response)

    async def update_task(self, task_id: str, data: TaskUpdate) -> Task:
        """Update an existing task.

        Calls ``PATCH /agents/tasks/{task_id}``. Only the provided fields
        are updated. Status changes are not allowed here — use
        :meth:`transition_task` instead.

        Args:
            task_id: Task identifier.
            data: Fields to update.

        Returns:
            Updated task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the update fails.
        """  # noqa: DOC502
        response = await self._request(
            "PATCH",
            f"/agents/tasks/{quote(task_id, safe='')}",
            json_data=data.model_dump(exclude_unset=True),
        )
        return Task.model_validate(response)

    async def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Calls ``DELETE /agents/tasks/{task_id}``.

        Args:
            task_id: Task identifier.

        Raises:
            httpx.HTTPStatusError: If the task is not found or deletion fails.
        """  # noqa: DOC502
        await self._request("DELETE", f"/agents/tasks/{quote(task_id, safe='')}")

    async def assign_task(self, task_id: str, data: TaskAssign) -> Task:
        """Assign a task to an agent instance, user, or agent type.

        Calls ``POST /agents/tasks/{task_id}/assign``. Exactly one of
        ``instance_id``, ``user_id``, or ``type_id`` should be provided
        on ``data``.

        Args:
            task_id: Task identifier.
            data: Assignment payload.

        Returns:
            Updated task with the new assignment.

        Raises:
            httpx.HTTPStatusError: If the task is not found or assignment fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/assign",
            json_data=data.model_dump(exclude_none=True),
        )
        return Task.model_validate(response)

    async def transition_task(self, task_id: str, data: TaskTransition) -> Task:
        """Transition a task to a new status.

        Calls ``POST /agents/tasks/{task_id}/transition``. The transition
        is validated against the allowed status graph on the server;
        invalid transitions return ``InvalidLifecycleTransitionError``.

        Args:
            task_id: Task identifier.
            data: Transition payload carrying the target status.

        Returns:
            Updated task with the new status.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the transition is rejected.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/transition",
            json_data=data.model_dump(),
        )
        return Task.model_validate(response)

    async def list_subtasks(self, task_id: str) -> list[Task]:
        """List direct subtasks of a task.

        Calls ``GET /agents/tasks/{task_id}/subtasks``. Subtask
        relationships are graph edges, so this method traverses the
        ``has_subtask`` edges starting at ``task_id``.

        Args:
            task_id: Parent task identifier.

        Returns:
            Direct child tasks ordered by priority then creation time.

        Raises:
            httpx.HTTPStatusError: If the parent task is not found or the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/agents/tasks/{quote(task_id, safe='')}/subtasks")
        return [Task.model_validate(item) for item in response]

    async def create_subtask(self, task_id: str, data: TaskCreate) -> Task:
        """Create a new subtask under an existing task.

        Calls ``POST /agents/tasks/{task_id}/subtasks``. The new task
        inherits priority and assignee fields from the parent when the
        request body leaves them at their defaults; the ``has_subtask``
        edge is created server-side as part of the same operation.

        Args:
            task_id: Parent task identifier.
            data: Subtask creation payload.

        Returns:
            The created subtask.

        Raises:
            httpx.HTTPStatusError: If the parent task is not found or creation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks",
            json_data=data.model_dump(exclude_unset=True),
        )
        return Task.model_validate(response)

    async def link_subtask(self, task_id: str, child_id: str) -> None:
        """Link an existing task as a subtask of another task.

        Calls ``POST /agents/tasks/{task_id}/subtasks/link``. The server
        rejects links that would create a cycle in the subtask graph.

        Args:
            task_id: Parent task identifier.
            child_id: Existing task to attach as a subtask.

        Raises:
            httpx.HTTPStatusError: If either task is not found or the
                link would create a cycle.
        """  # noqa: DOC502
        await self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks/link",
            json_data={"child_id": child_id},
        )

    async def unlink_subtask(self, task_id: str, child_id: str) -> None:
        """Remove the ``has_subtask`` edge between two tasks.

        Calls ``DELETE /agents/tasks/{task_id}/subtasks/{child_id}``. The
        child task is not deleted; only the link is removed.

        Args:
            task_id: Parent task identifier.
            child_id: Subtask identifier to unlink.

        Raises:
            httpx.HTTPStatusError: If either task is not found or the
                request fails.
        """  # noqa: DOC502
        await self._request(
            "DELETE",
            f"/agents/tasks/{quote(task_id, safe='')}/subtasks/{quote(child_id, safe='')}",
        )

    async def list_task_comments(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[TaskComment]:
        """List comments on a task, oldest first.

        Calls ``GET /agents/tasks/{task_id}/comments``.

        Args:
            task_id: Task identifier.
            limit: Maximum comments to return (1-200, default 50).
            cursor: Composite cursor ``"<iso-created_at>|<comment-id>"``
                returned by a previous call. Returns comments strictly
                after this point in ``(created_at ASC, id ASC)`` order.

        Returns:
            Comments for the requested page, oldest first.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit}

        if cursor is not None:
            params["cursor"] = cursor

        response = await self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/comments",
            params=params,
        )
        return [TaskComment.model_validate(item) for item in response]

    async def create_task_comment(
        self,
        task_id: str,
        data: TaskCommentCreate,
    ) -> TaskComment:
        """Create a comment on a task.

        Calls ``POST /agents/tasks/{task_id}/comments``. Authorship is
        taken from the authenticated user — agent comments are written
        by the runtime through a separate code path and are not
        creatable via this method.

        Args:
            task_id: Task identifier.
            data: Comment creation payload.

        Returns:
            Persisted comment with the server-populated id.

        Raises:
            httpx.HTTPStatusError: If the task is not found or creation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/agents/tasks/{quote(task_id, safe='')}/comments",
            json_data=data.model_dump(),
        )
        return TaskComment.model_validate(response)

    async def update_task_comment(
        self,
        comment_id: str,
        data: TaskCommentUpdate,
    ) -> TaskComment:
        """Edit a task comment. Only the original author may edit.

        Calls ``PATCH /agents/tasks/comments/{comment_id}``.

        Args:
            comment_id: Comment identifier.
            data: New body to replace the existing one.

        Returns:
            Updated comment with ``edited=True``.

        Raises:
            httpx.HTTPStatusError: If the comment is not found or the
                actor is not the author.
        """  # noqa: DOC502
        response = await self._request(
            "PATCH",
            f"/agents/tasks/comments/{quote(comment_id, safe='')}",
            json_data=data.model_dump(),
        )
        return TaskComment.model_validate(response)

    async def delete_task_comment(self, comment_id: str) -> None:
        """Delete a task comment. Only the original author may delete.

        Calls ``DELETE /agents/tasks/comments/{comment_id}``.

        Args:
            comment_id: Comment identifier.

        Raises:
            httpx.HTTPStatusError: If the comment is not found or the
                actor is not the author.
        """  # noqa: DOC502
        await self._request("DELETE", f"/agents/tasks/comments/{quote(comment_id, safe='')}")

    async def list_task_activity(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[TaskActivityEntry]:
        """List the activity timeline for a task, newest first.

        Calls ``GET /agents/tasks/{task_id}/activity``. Combines status
        transitions, assignments, comments, agent start events, and
        resource mutation summaries into a single newest-first stream.
        Mutation entries surface only the operation and changed field
        names; full before/after snapshots live on the mutation log.

        Args:
            task_id: Task identifier.
            limit: Maximum entries to return (1-200, default 50).
            cursor: Composite cursor ``"<iso-timestamp>|<edge-id>"``
                returned by a previous call. Returns entries strictly
                older than this point in
                ``(timestamp DESC, edge_id DESC)`` order.

        Returns:
            Activity entries for the requested page, newest first.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit}

        if cursor is not None:
            params["cursor"] = cursor

        response = await self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/activity",
            params=params,
        )
        return [TaskActivityEntry.model_validate(item) for item in response]

    async def list_task_mutations(
        self,
        task_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        reveal: bool = False,
    ) -> TaskMutationPage:
        """Return the paginated mutation log for a task.

        Calls ``GET /agents/tasks/{task_id}/mutations``. Each entry
        carries a full before/after snapshot plus the list of fields
        that changed. Sensitive fields are masked by default — pass
        ``reveal=True`` to receive actual values.

        Args:
            task_id: Task identifier.
            limit: Maximum mutations per page (1-200, default 50).
            cursor: Composite cursor ``"<iso-timestamp>|<edge-id>"``
                returned by a previous call. Returns mutations strictly
                older than this point in
                ``(timestamp DESC, edge_id DESC)`` order.
            reveal: When True, return actual values for sensitive
                fields on the before/after snapshots.

        Returns:
            One page of the mutation log plus a ``next_cursor`` for the
            next page (``None`` when there are no more entries).

        Raises:
            httpx.HTTPStatusError: If the task is not found, the cursor
                is malformed, or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"limit": limit, "reveal": reveal}

        if cursor is not None:
            params["cursor"] = cursor

        response = await self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/mutations",
            params=params,
        )
        return TaskMutationPage.model_validate(response)

    async def get_task_graph_diff(self, task_id: str, *, reveal: bool = False) -> GraphDiff:
        """Return the net delta per resource for a task.

        Calls ``GET /agents/tasks/{task_id}/graph-diff``. The server
        rolls every ``task->mutated->resource`` edge into a per-resource
        net delta — a create + many updates collapse to a single
        ``create``, a create + delete collapses to ``noop``, etc.

        The endpoint scans up to a server-configured cap of mutations
        per request. Tasks that exceed the cap return a partial rollup
        with ``GraphDiff.truncated=True`` and ``GraphDiff.has_more=True``;
        callers should direct users to :meth:`list_task_mutations` for
        the full audit trail. Sensitive fields on the snapshots are
        masked by default — pass ``reveal=True`` for actual values.

        Args:
            task_id: Task identifier.
            reveal: When True, return actual values for sensitive fields
                on the before/after snapshots.

        Returns:
            Per-resource net deltas plus the truncation flag and
            scanned-mutation count.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"reveal": reveal}
        response = await self._request(
            "GET",
            f"/agents/tasks/{quote(task_id, safe='')}/graph-diff",
            params=params,
        )
        return GraphDiff.model_validate(response)

    async def improve_task(self, data: ImproveTaskRequest) -> ImproveTaskResponse:
        """Run the ``improve-task`` AI assist on a draft task.

        Calls ``POST /agents/assists/improve-task``. Returns a refined
        title, description, and a short rationale explaining the change.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``fast`` model tier.

        Args:
            data: Existing title and (optional) description to refine.

        Returns:
            Suggested replacement title, description, and rationale.

        Raises:
            httpx.HTTPStatusError: If the assist invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/improve-task",
            json_data=data.model_dump(exclude_none=True),
        )
        return ImproveTaskResponse.model_validate(response)

    async def explain_task(self, data: ExplainTaskRequest) -> ExplainTaskResponse:
        """Run the ``explain-task`` AI assist for a task.

        Calls ``POST /agents/assists/explain-task``. The server fetches
        the task referenced by ``task_id`` and, when provided, the
        correlation bucket, then returns a plain-language summary, key
        points, and a suggested next action. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``fast`` model
        tier.

        Args:
            data: Task identifier and optional correlation bucket
                providing richer context.

        Returns:
            Summary, key points, and suggested next action for the task.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/explain-task",
            json_data=data.model_dump(exclude_none=True),
        )
        return ExplainTaskResponse.model_validate(response)

    async def summarize_thread(self, data: SummarizeThreadRequest) -> SummarizeThreadResponse:
        """Run the ``summarize-thread`` AI assist on a task's comments.

        Calls ``POST /agents/assists/summarize-thread``. The server
        pulls the comment thread for ``task_id`` and returns a summary
        plus structured decisions, open questions, and action items.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``fast`` model tier.

        Args:
            data: Task whose comment thread should be summarized.

        Returns:
            Summary, decisions, open questions, and action items.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/summarize-thread",
            json_data=data.model_dump(exclude_none=True),
        )
        return SummarizeThreadResponse.model_validate(response)

    async def suggest_assignee(self, data: SuggestAssigneeRequest) -> SuggestAssigneeResponse:
        """Run the ``suggest-assignee`` AI assist for a task.

        Calls ``POST /agents/assists/suggest-assignee``. Ranks the
        provided candidates and returns the best fit with a rationale
        and confidence score in ``[0.0, 1.0]``. Dispatched server-side
        via ``AgentInvoker.invoke_platform_agent()`` on the ``fast``
        model tier.

        Args:
            data: Task identifier and the candidate agent instances to
                consider.

        Returns:
            The recommended candidate's instance ID, the rationale for
            the pick, and the model's confidence.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/suggest-assignee",
            json_data=data.model_dump(exclude_none=True),
        )
        return SuggestAssigneeResponse.model_validate(response)

    async def generate_subtasks(self, data: GenerateSubtasksRequest) -> GenerateSubtasksResponse:
        """Run the ``generate-subtasks`` AI assist.

        Calls ``POST /agents/assists/generate-subtasks``. Returns a list
        of proposed subtasks for the caller to review before persisting.
        Dispatched server-side via ``AgentInvoker.invoke_platform_agent()``
        on the ``balanced`` model tier.

        Args:
            data: Title and description of the parent task, or
                ``parent_context`` when generating subtasks under an
                already-created task.

        Returns:
            Ordered list of proposed subtasks.

        Raises:
            httpx.HTTPStatusError: If the assist invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/generate-subtasks",
            json_data=data.model_dump(exclude_none=True),
        )
        return GenerateSubtasksResponse.model_validate(response)

    async def review_summary(self, data: ReviewSummaryRequest) -> ReviewSummaryResponse:
        """Run the ``review-summary`` AI assist on a task's changes.

        Calls ``POST /agents/assists/review-summary``. The server pulls
        the task's graph diff, affected resources, and risk signals,
        and returns a summary, a risk classification, and a reviewer
        checklist. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``balanced``
        model tier.

        Args:
            data: Task identifier whose proposed changes should be
                reviewed.

        Returns:
            Summary, risk level (``low``/``medium``/``high``), and
            review checklist.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/review-summary",
            json_data=data.model_dump(exclude_none=True),
        )
        return ReviewSummaryResponse.model_validate(response)

    async def deep_analysis(self, data: DeepAnalysisRequest) -> DeepAnalysisResponse:
        """Run the ``deep-analysis`` AI assist for a task.

        Calls ``POST /agents/assists/deep-analysis``. Answers a free-form
        question grounded in the referenced task, returning analysis,
        concerns, and recommendations. Dispatched server-side via
        ``AgentInvoker.invoke_platform_agent()`` on the ``reasoning``
        model tier.

        Args:
            data: Task identifier anchoring the analysis and the
                free-form question to answer.

        Returns:
            Detailed analysis, surfaced concerns, and recommendations.

        Raises:
            httpx.HTTPStatusError: If the task is not found or the assist
                invocation fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            "/agents/assists/deep-analysis",
            json_data=data.model_dump(exclude_none=True),
        )
        return DeepAnalysisResponse.model_validate(response)


def _scoped_path(project_id: str, *suffix: str) -> str:
    """Build a project-scoped API path with the project ID URL-encoded.

    Args:
        project_id: Project identifier to interpolate into the path.
        *suffix: Additional path segments appended after ``/resources``.

    Returns:
        URL path rooted at ``/projects/{project_id}/resources``.
    """
    tail = "/".join(suffix)
    return f"/projects/{quote(project_id, safe='')}/resources" + (f"/{tail}" if tail else "")


class ProjectResources:
    """Project-scoped resource operations for :class:`PragmaClient`.

    Holds a reference to the parent client and the target ``project_id``.
    All resource operations are routed under
    ``/projects/{project_id}/resources/...`` so aliased resource identities
    across projects are impossible.

    Cross-project submissions are rejected client-side via
    :class:`ProjectMismatchError` before any network call is made.
    """

    def __init__(self, client: PragmaClient, project_id: str) -> None:
        """Scope the parent client to a specific project.

        Args:
            client: Parent synchronous client.
            project_id: Project identifier to scope operations to.

        Raises:
            InvalidResourceIdentityError: If ``project_id`` is empty, contains
                the reserved ``::`` separator, or has control characters.
        """  # noqa: DOC502
        _validate_segment(project_id, "project_id")
        self._client = client
        self._project_id = project_id

    @property
    def project_id(self) -> str:
        """Project identifier this handle is scoped to."""
        return self._project_id

    def list_resources[ResourceT: Resource](
        self,
        provider: str | None = None,
        resource: str | None = None,
        tags: list[str] | None = None,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources in this project with optional filters.

        Args:
            provider: Filter by provider name.
            resource: Filter by resource type.
            tags: Filter by tags (must match all).
            model: Resource subclass for typed response; returns raw dicts if None.
            reveal: Include sensitive field values in the response.

        Returns:
            List of resources as typed instances or raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {}
        if provider:
            params["provider"] = provider
        if resource:
            params["resource"] = resource
        if tags:
            params["tags"] = tags
        if reveal:
            params["reveal"] = "true"

        response = self._client._request("GET", _scoped_path(self._project_id), params=params)
        if model is not None:
            return [model.model_validate(item) for item in response]
        return response

    def get_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Fetch a single resource by coordinates within this project.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"provider": provider, "resource": resource, "name": name}
        if reveal:
            params["reveal"] = "true"

        response = self._client._request("GET", _scoped_path(self._project_id, "by-name"), params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Create or update a resource in this project.

        The resource's ``project_id`` must match the handle's ``project_id``.
        Mismatches raise :class:`ProjectMismatchError` before the HTTP call.

        Args:
            resource: Typed :class:`Resource` to apply.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            ProjectMismatchError: If ``resource.project_id`` does not match
                the scoped project identifier.
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        if resource.project_id != self._project_id:
            raise ProjectMismatchError(self._project_id, resource.project_id)

        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"

        response = self._client._request(
            "POST",
            _scoped_path(self._project_id, "apply"),
            json_data=resource.model_dump(),
            params=params,
        )
        if model is not None:
            return model.model_validate(response)
        return response

    def deactivate_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Deactivate a resource in this project, triggering provider teardown.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Resource in deleting state as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or deactivation fails.
        """  # noqa: DOC502
        params = {"provider": provider, "resource": resource, "name": name}
        response = self._client._request("POST", _scoped_path(self._project_id, "deactivate"), params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    def delete_resource(self, provider: str, resource: str, name: str) -> None:
        """Hard-delete a resource in this project.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.

        Raises:
            httpx.HTTPStatusError: If resource not found or deletion fails.
        """  # noqa: DOC502
        params = {"provider": provider, "resource": resource, "name": name}
        self._client._request("DELETE", _scoped_path(self._project_id, "by-name"), params=params)

    def wait_ready(
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """Poll a project-scoped resource until it reaches READY or FAILED.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            timeout: Maximum seconds to wait before raising :class:`TimeoutError`.
            poll_interval: Seconds between polls.

        Returns:
            Final resource payload.

        Raises:
            ResourceFailedError: If the resource transitions to FAILED.
            TimeoutError: If the resource does not reach READY within ``timeout``.
        """
        deadline = time.monotonic() + timeout

        while True:
            payload = self.get_resource(provider, resource, name)
            state = payload.get("lifecycle_state") if isinstance(payload, dict) else None

            if state == LifecycleState.READY.value:
                return payload

            if state == LifecycleState.FAILED.value:
                raise ResourceFailedError(
                    resource_id=f"{self._project_id}::{provider}::{resource}::{name}",
                    error=payload.get("error") if isinstance(payload, dict) else None,
                    resource_data=payload if isinstance(payload, dict) else None,
                )

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Resource {self._project_id}::{provider}::{resource}::{name} did not reach READY within {timeout}s"
                )

            time.sleep(poll_interval)


class AsyncProjectResources:
    """Project-scoped async resource operations for :class:`AsyncPragmaClient`.

    Mirror of :class:`ProjectResources` for the async client.
    """

    def __init__(self, client: AsyncPragmaClient, project_id: str) -> None:
        """Scope the parent async client to a specific project.

        Args:
            client: Parent asynchronous client.
            project_id: Project identifier to scope operations to.

        Raises:
            InvalidResourceIdentityError: If ``project_id`` is empty, contains
                the reserved ``::`` separator, or has control characters.
        """  # noqa: DOC502
        _validate_segment(project_id, "project_id")
        self._client = client
        self._project_id = project_id

    @property
    def project_id(self) -> str:
        """Project identifier this handle is scoped to."""
        return self._project_id

    async def list_resources[ResourceT: Resource](
        self,
        provider: str | None = None,
        resource: str | None = None,
        tags: list[str] | None = None,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources in this project with optional filters.

        Args:
            provider: Filter by provider name.
            resource: Filter by resource type.
            tags: Filter by tags (must match all).
            model: Resource subclass for typed response; returns raw dicts if None.
            reveal: Include sensitive field values in the response.

        Returns:
            List of resources as typed instances or raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {}
        if provider:
            params["provider"] = provider
        if resource:
            params["resource"] = resource
        if tags:
            params["tags"] = tags
        if reveal:
            params["reveal"] = "true"

        response = await self._client._request("GET", _scoped_path(self._project_id), params=params)
        if model is not None:
            return [model.model_validate(item) for item in response]
        return response

    async def get_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Fetch a single resource by coordinates within this project.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or request fails.
        """  # noqa: DOC502
        params: dict[str, Any] = {"provider": provider, "resource": resource, "name": name}
        if reveal:
            params["reveal"] = "true"

        response = await self._client._request("GET", _scoped_path(self._project_id, "by-name"), params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    async def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT,
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Create or update a resource in this project.

        Args:
            resource: Typed :class:`Resource` to apply.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            ProjectMismatchError: If ``resource.project_id`` does not match
                the scoped project identifier.
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        if resource.project_id != self._project_id:
            raise ProjectMismatchError(self._project_id, resource.project_id)

        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"

        response = await self._client._request(
            "POST",
            _scoped_path(self._project_id, "apply"),
            json_data=resource.model_dump(),
            params=params,
        )
        if model is not None:
            return model.model_validate(response)
        return response

    async def deactivate_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Deactivate a resource in this project, triggering provider teardown.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Resource in deleting state as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or deactivation fails.
        """  # noqa: DOC502
        params = {"provider": provider, "resource": resource, "name": name}
        response = await self._client._request("POST", _scoped_path(self._project_id, "deactivate"), params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    async def delete_resource(self, provider: str, resource: str, name: str) -> None:
        """Hard-delete a resource in this project.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.

        Raises:
            httpx.HTTPStatusError: If resource not found or deletion fails.
        """  # noqa: DOC502
        params = {"provider": provider, "resource": resource, "name": name}
        await self._client._request("DELETE", _scoped_path(self._project_id, "by-name"), params=params)

    async def wait_ready(
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """Poll a project-scoped resource until it reaches READY or FAILED.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            timeout: Maximum seconds to wait before raising :class:`TimeoutError`.
            poll_interval: Seconds between polls.

        Returns:
            Final resource payload.

        Raises:
            ResourceFailedError: If the resource transitions to FAILED.
            TimeoutError: If the resource does not reach READY within ``timeout``.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            payload = await self.get_resource(provider, resource, name)
            state = payload.get("lifecycle_state") if isinstance(payload, dict) else None

            if state == LifecycleState.READY.value:
                return payload

            if state == LifecycleState.FAILED.value:
                raise ResourceFailedError(
                    resource_id=f"{self._project_id}::{provider}::{resource}::{name}",
                    error=payload.get("error") if isinstance(payload, dict) else None,
                    resource_data=payload if isinstance(payload, dict) else None,
                )

            if loop.time() >= deadline:
                raise TimeoutError(
                    f"Resource {self._project_id}::{provider}::{resource}::{name} did not reach READY within {timeout}s"
                )

            await asyncio.sleep(poll_interval)
