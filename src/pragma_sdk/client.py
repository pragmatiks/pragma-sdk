"""HTTP clients for the Pragma API."""

from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any

import httpx

from pragma_sdk.auth import BearerAuth
from pragma_sdk.config import get_token_for_context
from pragma_sdk.models import (
    BuildInfo,
    DeploymentResult,
    InstalledProvider,
    InstalledProviderSummary,
    PaginatedResponse,
    ProviderDeleteResult,
    ProviderInfo,
    ProviderStatus,
    PushResult,
    Resource,
    ResourceTier,
    StoreProviderDetail,
    StoreProviderSummary,
    StoreVersion,
    StoreVersionDetail,
    TrustTier,
    UpgradePolicy,
    UserInfo,
    format_resource_id,
)


def _validate_name(name: str) -> str:
    """Validate a name used in URL path segments.

    Returns:
        The validated name.

    Raises:
        ValueError: If name is empty or contains a slash.
    """
    if not name or "/" in name:
        raise ValueError(f"Invalid name: {name!r}")
    return name


class BaseClient:
    """Base class for Pragma API clients with shared initialization logic."""

    base_url: str
    timeout: float

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
        auth_token: str | None | object = ...,
        context: str | None = None,
        require_auth: bool = False,
    ):
        """Initialize client with automatic token discovery.

        Args:
            base_url: API URL. Defaults to PRAGMA_API_URL env var or localhost:8000.
            timeout: Request timeout in seconds.
            auth_token: Bearer token. Omit for auto-discovery, pass None to disable auth.
            context: Named context for token lookup (e.g., 'production').
            require_auth: Raise if no token can be discovered.

        Raises:
            ValueError: If require_auth is True and no token is found.
        """
        self.base_url = base_url or os.getenv("PRAGMA_API_URL", "http://localhost:8000")
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
        timeout: float = 10.0,
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

    def list_resources[ResourceT: Resource](
        self,
        provider: str | None = None,
        resource: str | None = None,
        tags: list[str] | None = None,
        *,
        model: type[ResourceT] | None = None,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources with optional filters.

        Args:
            provider: Filter by provider name.
            resource: Filter by resource type.
            tags: Filter by tags (must match all).
            model: Resource subclass for typed response; returns raw dicts if None.

        Returns:
            List of resources as typed instances or raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        if resource:
            params["resource"] = resource
        if tags:
            params["tags"] = tags

        response = self._request("GET", "/resources/", params=params)
        if model is not None:
            return [model.model_validate(item) for item in response]
        return response

    def list_resource_types(self, provider: str | None = None) -> list[dict[str, Any]]:
        """List available resource types from deployed providers.

        Args:
            provider: Filter by provider name.

        Returns:
            List of resource definitions containing provider, resource, schema, description.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        return self._request("GET", "/resources/types", params=params)

    def get_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Get a resource by its full identifier.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or request fails.
        """  # noqa: DOC502
        resource_id = format_resource_id(provider, resource, name)
        response = self._request("GET", f"/resources/{resource_id}")
        if model is not None:
            return model.model_validate(response)
        return response

    def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT | dict[str, Any],
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Apply a resource (create or update).

        Args:
            resource: Resource to apply as typed instance or raw dict.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        json_data = resource.model_dump() if isinstance(resource, Resource) else resource
        response = self._request("POST", "/resources/apply", json_data=json_data)
        if model is not None:
            return model.model_validate(response)
        return response

    def delete_resource(self, provider: str, resource: str, name: str) -> None:
        """Delete a resource.

        Raises:
            httpx.HTTPStatusError: If resource not found or deletion fails.
        """  # noqa: DOC502
        resource_id = format_resource_id(provider, resource, name)
        self._request("DELETE", f"/resources/{resource_id}")

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

    def push_provider(self, provider_id: str, tarball: bytes) -> PushResult:
        """Push provider code and trigger a build.

        Uploads a tarball containing provider source code and starts a
        BuildKit job to build a container image.

        Args:
            provider_id: Unique identifier for the provider.
            tarball: Gzipped tarball (tar.gz) containing provider source code.

        Returns:
            PushResult with build ID and job name for tracking.

        Raises:
            httpx.HTTPStatusError: If the push fails.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/providers/{provider_id}/push",
            files={"code": ("code.tar.gz", tarball, "application/gzip")},
        )
        return PushResult.model_validate(response)

    def get_build_status(self, provider_id: str, version: str) -> BuildInfo:
        """Get the status of a build by version.

        Args:
            provider_id: Provider identifier.
            version: CalVer version string (YYYYMMDD.HHMMSS).

        Returns:
            BuildInfo with current build state.

        Raises:
            httpx.HTTPStatusError: If build not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/providers/{provider_id}/builds/{version}")
        return BuildInfo.model_validate(response)

    def stream_build_logs(self, provider_id: str, version: str) -> AbstractContextManager[httpx.Response]:
        """Stream logs from a build.

        Returns a streaming response for real-time monitoring of build progress.
        The caller is responsible for iterating over the response and closing it.

        Args:
            provider_id: Provider identifier.
            version: CalVer version string (YYYYMMDD.HHMMSS).

        Returns:
            Context manager yielding httpx.Response with build logs (text/plain).

        Raises:
            httpx.HTTPStatusError: If build not found or request fails.

        Example:
            >>> with client.stream_build_logs("my-provider", "20250115.120000") as response:
            ...     for line in response.iter_lines():
            ...         print(line)
        """  # noqa: DOC502
        return self._client.stream("GET", f"/providers/{provider_id}/builds/{version}/logs")

    def deploy_provider(self, provider_id: str, version: str | None = None) -> ProviderStatus:
        """Deploy a provider to a specific version.

        Creates or updates the Kubernetes Deployment for the provider.
        If no version is specified, deploys the latest successful build.

        Args:
            provider_id: Unique identifier for the provider.
            version: CalVer version string (YYYYMMDD.HHMMSS) to deploy.
                If None, deploys the latest successful build.

        Returns:
            ProviderStatus with status, version, updated_at, and healthy flag.

        Raises:
            httpx.HTTPStatusError: 404 if version not found or no deployable build exists.
        """  # noqa: DOC502
        json_data = {"version": version} if version else {}
        response = self._request(
            "POST",
            f"/providers/{provider_id}/deploy",
            json_data=json_data,
        )
        return ProviderStatus.model_validate(response)

    def list_builds(self, provider_id: str) -> list[BuildInfo]:
        """List builds for a provider.

        Returns the last 10 builds ordered by creation time (newest first).

        Args:
            provider_id: Unique identifier for the provider.

        Returns:
            List of BuildInfo for the provider's builds.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/providers/{provider_id}/builds")
        return [BuildInfo.model_validate(build) for build in response]

    def rollback_provider(self, provider_id: str, version: str) -> DeploymentResult:
        """Rollback a provider to a previous build version.

        Deploys the specified build version. The build must exist and
        have status SUCCESS.

        Args:
            provider_id: Unique identifier for the provider.
            version: CalVer version string (YYYYMMDD.HHMMSS) to rollback to.

        Returns:
            DeploymentResult with deployment state.

        Raises:
            httpx.HTTPStatusError: 404 if build not found, 400 if build not deployable.
        """  # noqa: DOC502
        response = self._request(
            "POST",
            f"/providers/{provider_id}/rollback",
            json_data={"version": version},
        )
        return DeploymentResult.model_validate(response)

    def get_deployment_status(self, provider_id: str) -> ProviderStatus:
        """Get the deployment status for a provider.

        Returns a minimal status without internal K8s details like replica
        counts, deployment names, or container images.

        Args:
            provider_id: Unique identifier for the provider.

        Returns:
            ProviderStatus with status, version, updated_at, and healthy flag.

        Raises:
            httpx.HTTPStatusError: If deployment not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/providers/{provider_id}/deployment")
        return ProviderStatus.model_validate(response)

    def delete_provider(self, provider_id: str, *, cascade: bool = False) -> ProviderDeleteResult:
        """Delete a provider and all associated resources.

        Removes the provider deployment, resource definitions, and pending events.
        By default, fails if the provider has any resources. Use cascade=True to
        delete all resources along with the provider.

        Args:
            provider_id: Unique identifier for the provider to delete.
            cascade: If True, delete all resources. If False (default), fail if resources exist.

        Returns:
            ProviderDeleteResult with cleanup summary.

        Raises:
            httpx.HTTPStatusError: If provider has resources (409) or deletion fails.
        """  # noqa: DOC502
        params = {"cascade": "true"} if cascade else {}
        response = self._request("DELETE", f"/providers/{provider_id}", params=params)
        return ProviderDeleteResult.model_validate(response)

    def list_providers(self) -> list[ProviderInfo]:
        """List all providers for the current tenant.

        Returns providers with their deployment status. Providers that have
        been pushed but not deployed will have deployment_status=None.

        Returns:
            List of ProviderInfo with provider metadata and deployment status.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/providers/")
        return [ProviderInfo.model_validate(item) for item in response]

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

    def list_store_providers(
        self,
        q: str | None = None,
        trust_tier: TrustTier | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[StoreProviderSummary]:
        """List store providers with optional search and filtering.

        Args:
            q: Search query string.
            trust_tier: Filter by trust tier.
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of store provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if q is not None:
            params["q"] = q

        if trust_tier is not None:
            params["trust_tier"] = trust_tier

        if tags is not None:
            params["tags"] = tags

        response = self._request("GET", "/store/providers", params=params)
        return PaginatedResponse[StoreProviderSummary].model_validate(response)

    def get_store_provider(self, name: str) -> StoreProviderDetail:
        """Get detailed info for a store provider including versions.

        Args:
            name: Store provider name.

        Returns:
            Store provider detail with version history.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/store/providers/{_validate_name(name)}")
        return StoreProviderDetail.model_validate(response)

    def get_store_version(self, name: str, version: str) -> StoreVersionDetail:
        """Get detail for a specific provider version.

        Args:
            name: Store provider name.
            version: Version string.

        Returns:
            Version detail.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        response = self._request("GET", f"/store/providers/{_validate_name(name)}/versions/{_validate_name(version)}")
        return StoreVersionDetail.model_validate(response)

    def install_store_provider(
        self,
        name: str,
        version: str | None = None,
        resource_tier: ResourceTier | str = ResourceTier.STANDARD,
        upgrade_policy: UpgradePolicy | str = UpgradePolicy.MANUAL,
    ) -> InstalledProvider:
        """Install a provider from the store.

        Args:
            name: Store provider name.
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.

        Returns:
            Installed provider info.

        Raises:
            httpx.HTTPStatusError: If installation fails.
        """  # noqa: DOC502
        data: dict = {
            "provider_name": name,
            "resource_tier": resource_tier,
            "upgrade_policy": upgrade_policy,
        }

        if version is not None:
            data["version"] = version

        response = self._request("POST", "/store/install", json_data=data)
        return InstalledProvider.model_validate(response)

    def uninstall_store_provider(self, name: str, *, cascade: bool = False) -> None:
        """Uninstall a store provider.

        Args:
            name: Store provider name.
            cascade: If True, delete all resources managed by this provider.

        Raises:
            httpx.HTTPStatusError: If uninstall fails.
        """  # noqa: DOC502
        params = {}

        if cascade:
            params["cascade"] = "true"

        self._request("DELETE", f"/store/installed/{_validate_name(name)}", params=params)

    def upgrade_store_provider(self, name: str, version: str | None = None) -> InstalledProvider:
        """Upgrade an installed store provider.

        Args:
            name: Store provider name.
            version: Target version (latest if None).

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If upgrade fails.
        """  # noqa: DOC502
        data: dict = {}

        if version is not None:
            data["version"] = version

        response = self._request("POST", f"/store/installed/{_validate_name(name)}/upgrade", json_data=data)
        return InstalledProvider.model_validate(response)

    def list_installed_providers(self) -> list[InstalledProviderSummary]:
        """List installed store providers for the current tenant.

        Returns:
            List of installed provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/store/installed")
        return [InstalledProviderSummary.model_validate(item) for item in response]

    def publish_store_provider(
        self,
        name: str,
        tarball: bytes,
        version: str,
        changelog: str | None = None,
        *,
        force: bool = False,
    ) -> StoreVersion:
        """Publish a new version of a store provider.

        Args:
            name: Store provider name.
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            force: If True, allow overwriting an existing version.

        Returns:
            Published store version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if force:
            data["force"] = "true"

        response = self._request(
            "POST",
            f"/store/providers/{_validate_name(name)}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return StoreVersion.model_validate(response)

    def get_store_build_status(self, name: str, version: str) -> StoreVersion:
        """Get build status for a store provider version.

        Args:
            name: Store provider name.
            version: Version string.

        Returns:
            Store version with current build status.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        response = self._request(
            "GET", f"/store/providers/{_validate_name(name)}/versions/{_validate_name(version)}/status"
        )
        return StoreVersion.model_validate(response)


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
        timeout: float = 10.0,
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

    async def list_resources[ResourceT: Resource](
        self,
        provider: str | None = None,
        resource: str | None = None,
        tags: list[str] | None = None,
        *,
        model: type[ResourceT] | None = None,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources with optional filters.

        Args:
            provider: Filter by provider name.
            resource: Filter by resource type.
            tags: Filter by tags (must match all).
            model: Resource subclass for typed response; returns raw dicts if None.

        Returns:
            List of resources as typed instances or raw dicts.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        if resource:
            params["resource"] = resource
        if tags:
            params["tags"] = tags

        response = await self._request("GET", "/resources/", params=params)
        if model is not None:
            return [model.model_validate(item) for item in response]
        return response

    async def list_resource_types(self, provider: str | None = None) -> list[dict[str, Any]]:
        """List available resource types from deployed providers.

        Args:
            provider: Filter by provider name.

        Returns:
            List of resource definitions containing provider, resource, schema, description.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params = {}
        if provider:
            params["provider"] = provider
        return await self._request("GET", "/resources/types", params=params)

    async def get_resource[ResourceT: Resource](
        self,
        provider: str,
        resource: str,
        name: str,
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Get a resource by its full identifier.

        Args:
            provider: Provider that manages the resource.
            resource: Resource type name.
            name: Resource instance name.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If resource not found or request fails.
        """  # noqa: DOC502
        resource_id = format_resource_id(provider, resource, name)
        response = await self._request("GET", f"/resources/{resource_id}")
        if model is not None:
            return model.model_validate(response)
        return response

    async def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT | dict[str, Any],
        *,
        model: type[ResourceT] | None = None,
    ) -> ResourceT | dict[str, Any]:
        """Apply a resource (create or update).

        Args:
            resource: Resource to apply as typed instance or raw dict.
            model: Resource subclass for typed response; returns raw dict if None.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        json_data = resource.model_dump() if isinstance(resource, Resource) else resource
        response = await self._request("POST", "/resources/apply", json_data=json_data)
        if model is not None:
            return model.model_validate(response)
        return response

    async def delete_resource(self, provider: str, resource: str, name: str) -> None:
        """Delete a resource.

        Raises:
            httpx.HTTPStatusError: If resource not found or deletion fails.
        """  # noqa: DOC502
        resource_id = format_resource_id(provider, resource, name)
        await self._request("DELETE", f"/resources/{resource_id}")

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

    async def push_provider(self, provider_id: str, tarball: bytes) -> PushResult:
        """Push provider code and trigger a build.

        Uploads a tarball containing provider source code and starts a
        BuildKit job to build a container image.

        Args:
            provider_id: Unique identifier for the provider.
            tarball: Gzipped tarball (tar.gz) containing provider source code.

        Returns:
            PushResult with build ID and job name for tracking.

        Raises:
            httpx.HTTPStatusError: If the push fails.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/providers/{provider_id}/push",
            files={"code": ("code.tar.gz", tarball, "application/gzip")},
        )
        return PushResult.model_validate(response)

    async def get_build_status(self, provider_id: str, version: str) -> BuildInfo:
        """Get the status of a build by version.

        Args:
            provider_id: Provider identifier.
            version: CalVer version string (YYYYMMDD.HHMMSS).

        Returns:
            BuildInfo with current build state.

        Raises:
            httpx.HTTPStatusError: If build not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/providers/{provider_id}/builds/{version}")
        return BuildInfo.model_validate(response)

    def stream_build_logs(self, provider_id: str, version: str) -> AbstractAsyncContextManager[httpx.Response]:
        """Stream logs from a build.

        Returns a streaming response for real-time monitoring of build progress.
        The caller is responsible for iterating over the response and closing it.

        Args:
            provider_id: Provider identifier.
            version: CalVer version string (YYYYMMDD.HHMMSS).

        Returns:
            Async context manager yielding httpx.Response with build logs (text/plain).

        Raises:
            httpx.HTTPStatusError: If build not found or request fails.

        Example:
            >>> async with client.stream_build_logs("my-provider", "20250115.120000") as response:
            ...     async for line in response.aiter_lines():
            ...         print(line)
        """  # noqa: DOC502
        return self._client.stream("GET", f"/providers/{provider_id}/builds/{version}/logs")

    async def deploy_provider(self, provider_id: str, version: str | None = None) -> ProviderStatus:
        """Deploy a provider to a specific version.

        Creates or updates the Kubernetes Deployment for the provider.
        If no version is specified, deploys the latest successful build.

        Args:
            provider_id: Unique identifier for the provider.
            version: CalVer version string (YYYYMMDD.HHMMSS) to deploy.
                If None, deploys the latest successful build.

        Returns:
            ProviderStatus with status, version, updated_at, and healthy flag.

        Raises:
            httpx.HTTPStatusError: 404 if version not found or no deployable build exists.
        """  # noqa: DOC502
        json_data = {"version": version} if version else {}
        response = await self._request(
            "POST",
            f"/providers/{provider_id}/deploy",
            json_data=json_data,
        )
        return ProviderStatus.model_validate(response)

    async def list_builds(self, provider_id: str) -> list[BuildInfo]:
        """List builds for a provider.

        Returns the last 10 builds ordered by creation time (newest first).

        Args:
            provider_id: Unique identifier for the provider.

        Returns:
            List of BuildInfo for the provider's builds.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/providers/{provider_id}/builds")
        return [BuildInfo.model_validate(build) for build in response]

    async def rollback_provider(self, provider_id: str, version: str) -> DeploymentResult:
        """Rollback a provider to a previous build version.

        Deploys the specified build version. The build must exist and
        have status SUCCESS.

        Args:
            provider_id: Unique identifier for the provider.
            version: CalVer version string (YYYYMMDD.HHMMSS) to rollback to.

        Returns:
            DeploymentResult with deployment state.

        Raises:
            httpx.HTTPStatusError: 404 if build not found, 400 if build not deployable.
        """  # noqa: DOC502
        response = await self._request(
            "POST",
            f"/providers/{provider_id}/rollback",
            json_data={"version": version},
        )
        return DeploymentResult.model_validate(response)

    async def get_deployment_status(self, provider_id: str) -> ProviderStatus:
        """Get the deployment status for a provider.

        Returns a minimal status without internal K8s details like replica
        counts, deployment names, or container images.

        Args:
            provider_id: Unique identifier for the provider.

        Returns:
            ProviderStatus with status, version, updated_at, and healthy flag.

        Raises:
            httpx.HTTPStatusError: If deployment not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/providers/{provider_id}/deployment")
        return ProviderStatus.model_validate(response)

    async def delete_provider(self, provider_id: str, *, cascade: bool = False) -> ProviderDeleteResult:
        """Delete a provider and all associated resources.

        Removes the provider deployment, resource definitions, and pending events.
        By default, fails if the provider has any resources. Use cascade=True to
        delete all resources along with the provider.

        Args:
            provider_id: Unique identifier for the provider to delete.
            cascade: If True, delete all resources. If False (default), fail if resources exist.

        Returns:
            ProviderDeleteResult with cleanup summary.

        Raises:
            httpx.HTTPStatusError: If provider has resources (409) or deletion fails.
        """  # noqa: DOC502
        params = {"cascade": "true"} if cascade else {}
        response = await self._request("DELETE", f"/providers/{provider_id}", params=params)
        return ProviderDeleteResult.model_validate(response)

    async def list_providers(self) -> list[ProviderInfo]:
        """List all providers for the current tenant.

        Returns providers with their deployment status. Providers that have
        been pushed but not deployed will have deployment_status=None.

        Returns:
            List of ProviderInfo with provider metadata and deployment status.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/providers/")
        return [ProviderInfo.model_validate(item) for item in response]

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

    async def list_store_providers(
        self,
        q: str | None = None,
        trust_tier: TrustTier | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[StoreProviderSummary]:
        """List store providers with optional search and filtering.

        Args:
            q: Search query string.
            trust_tier: Filter by trust tier.
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of store provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if q is not None:
            params["q"] = q

        if trust_tier is not None:
            params["trust_tier"] = trust_tier

        if tags is not None:
            params["tags"] = tags

        response = await self._request("GET", "/store/providers", params=params)
        return PaginatedResponse[StoreProviderSummary].model_validate(response)

    async def get_store_provider(self, name: str) -> StoreProviderDetail:
        """Get detailed info for a store provider including versions.

        Args:
            name: Store provider name.

        Returns:
            Store provider detail with version history.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        response = await self._request("GET", f"/store/providers/{_validate_name(name)}")
        return StoreProviderDetail.model_validate(response)

    async def get_store_version(self, name: str, version: str) -> StoreVersionDetail:
        """Get detail for a specific provider version.

        Args:
            name: Store provider name.
            version: Version string.

        Returns:
            Version detail.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        response = await self._request(
            "GET", f"/store/providers/{_validate_name(name)}/versions/{_validate_name(version)}"
        )
        return StoreVersionDetail.model_validate(response)

    async def install_store_provider(
        self,
        name: str,
        version: str | None = None,
        resource_tier: ResourceTier | str = ResourceTier.STANDARD,
        upgrade_policy: UpgradePolicy | str = UpgradePolicy.MANUAL,
    ) -> InstalledProvider:
        """Install a provider from the store.

        Args:
            name: Store provider name.
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.

        Returns:
            Installed provider info.

        Raises:
            httpx.HTTPStatusError: If installation fails.
        """  # noqa: DOC502
        data: dict = {
            "provider_name": name,
            "resource_tier": resource_tier,
            "upgrade_policy": upgrade_policy,
        }

        if version is not None:
            data["version"] = version

        response = await self._request("POST", "/store/install", json_data=data)
        return InstalledProvider.model_validate(response)

    async def uninstall_store_provider(self, name: str, *, cascade: bool = False) -> None:
        """Uninstall a store provider.

        Args:
            name: Store provider name.
            cascade: If True, delete all resources managed by this provider.

        Raises:
            httpx.HTTPStatusError: If uninstall fails.
        """  # noqa: DOC502
        params = {}

        if cascade:
            params["cascade"] = "true"

        await self._request("DELETE", f"/store/installed/{_validate_name(name)}", params=params)

    async def upgrade_store_provider(self, name: str, version: str | None = None) -> InstalledProvider:
        """Upgrade an installed store provider.

        Args:
            name: Store provider name.
            version: Target version (latest if None).

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If upgrade fails.
        """  # noqa: DOC502
        data: dict = {}

        if version is not None:
            data["version"] = version

        response = await self._request("POST", f"/store/installed/{_validate_name(name)}/upgrade", json_data=data)
        return InstalledProvider.model_validate(response)

    async def list_installed_providers(self) -> list[InstalledProviderSummary]:
        """List installed store providers for the current tenant.

        Returns:
            List of installed provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/store/installed")
        return [InstalledProviderSummary.model_validate(item) for item in response]

    async def publish_store_provider(
        self,
        name: str,
        tarball: bytes,
        version: str,
        changelog: str | None = None,
        *,
        force: bool = False,
    ) -> StoreVersion:
        """Publish a new version of a store provider.

        Args:
            name: Store provider name.
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            force: If True, allow overwriting an existing version.

        Returns:
            Published store version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if force:
            data["force"] = "true"

        response = await self._request(
            "POST",
            f"/store/providers/{_validate_name(name)}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return StoreVersion.model_validate(response)

    async def get_store_build_status(self, name: str, version: str) -> StoreVersion:
        """Get build status for a store provider version.

        Args:
            name: Store provider name.
            version: Version string.

        Returns:
            Store version with current build status.

        Raises:
            httpx.HTTPStatusError: If version not found or request fails.
        """  # noqa: DOC502
        response = await self._request(
            "GET", f"/store/providers/{_validate_name(name)}/versions/{_validate_name(version)}/status"
        )
        return StoreVersion.model_validate(response)
