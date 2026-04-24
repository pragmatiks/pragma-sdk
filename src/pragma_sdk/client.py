"""HTTP clients for the Pragma API."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import AbstractAsyncContextManager, AbstractContextManager
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
    CostEstimate,
    DeploymentResult,
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

    def publish_provider(
        self,
        provider_name: str,
        tarball: bytes,
        version: str,
        changelog: str | None = None,
        *,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        icon_url: str | None = None,
    ) -> ProviderVersion:
        """Publish a new version of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            display_name: Human-friendly provider name for the catalog listing.
            description: Provider description for the catalog listing.
            tags: Tags for the catalog listing.
            icon_url: URL to provider icon image for the catalog listing.

        Returns:
            Published version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if display_name is not None:
            data["display_name"] = display_name

        if description is not None:
            data["description"] = description

        if tags is not None:
            data["tags"] = json.dumps(tags)

        if icon_url is not None:
            data["icon_url"] = icon_url

        response = self._request(
            "POST",
            f"/providers/{path}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return ProviderVersion.model_validate(response)

    def get_publish_status(self, provider_name: str, version: str) -> ProviderVersion:
        """Check build/publish status for a provider version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version string.

        Returns:
            Version with current build status.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("GET", f"/providers/{path}/versions/{version}/status")
        return ProviderVersion.model_validate(response)

    def stream_publish_logs(self, provider_name: str, version: str) -> AbstractContextManager[httpx.Response]:
        """Stream build logs for a provider version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version string.

        Returns:
            Context manager yielding httpx.Response with build logs (text/plain).

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.

        Example:
            >>> with client.stream_publish_logs("pragma/qdrant", "1.2.0") as response:
            ...     for line in response.iter_lines():
            ...         print(line)
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        return self._client.stream("GET", f"/providers/{path}/versions/{version}/logs")

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

    async def publish_provider(
        self,
        provider_name: str,
        tarball: bytes,
        version: str,
        changelog: str | None = None,
        *,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        icon_url: str | None = None,
    ) -> ProviderVersion:
        """Publish a new version of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            display_name: Human-friendly provider name for the catalog listing.
            description: Provider description for the catalog listing.
            tags: Tags for the catalog listing.
            icon_url: URL to provider icon image for the catalog listing.

        Returns:
            Published version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if display_name is not None:
            data["display_name"] = display_name

        if description is not None:
            data["description"] = description

        if tags is not None:
            data["tags"] = json.dumps(tags)

        if icon_url is not None:
            data["icon_url"] = icon_url

        response = await self._request(
            "POST",
            f"/providers/{path}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return ProviderVersion.model_validate(response)

    async def get_publish_status(self, provider_name: str, version: str) -> ProviderVersion:
        """Check build/publish status for a provider version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version string.

        Returns:
            Version with current build status.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("GET", f"/providers/{path}/versions/{version}/status")
        return ProviderVersion.model_validate(response)

    def stream_publish_logs(self, provider_name: str, version: str) -> AbstractAsyncContextManager[httpx.Response]:
        """Stream build logs for a provider version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Version string.

        Returns:
            Async context manager yielding httpx.Response with build logs (text/plain).

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.

        Example:
            >>> async with client.stream_publish_logs("pragma/qdrant", "1.2.0") as response:
            ...     async for line in response.aiter_lines():
            ...         print(line)
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        return self._client.stream("GET", f"/providers/{path}/versions/{version}/logs")

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
