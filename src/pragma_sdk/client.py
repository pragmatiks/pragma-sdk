"""HTTP clients for the Pragma API."""

from __future__ import annotations

import json
import os
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any

import httpx

from pragma_sdk.auth import BearerAuth
from pragma_sdk.config import get_token_for_context
from pragma_sdk.models import (
    DeploymentResult,
    InstalledProvider,
    InstalledProviderSummary,
    PaginatedResponse,
    ProviderScope,
    Resource,
    ResourceTier,
    StoreProviderDetail,
    StoreProviderSummary,
    StoreVersion,
    TrustTier,
    UpgradePolicy,
    UserInfo,
    format_resource_id,
)


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
        reveal: bool = False,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources with optional filters.

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
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Get a resource by its full identifier.

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
        resource_id = format_resource_id(provider, resource, name)
        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"
        response = self._request("GET", f"/resources/{resource_id}", params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT | dict[str, Any],
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Apply a resource (create or update).

        Args:
            resource: Resource to apply as typed instance or raw dict.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        json_data = resource.model_dump() if isinstance(resource, Resource) else resource
        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"
        response = self._request("POST", "/resources/apply", json_data=json_data, params=params)
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
        trust_tier: TrustTier | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[StoreProviderSummary]:
        """Browse and search the provider store.

        Args:
            query: Search query string.
            scope: Filter by provider scope (e.g. 'public', 'tenant').
            trust_tier: Filter by trust tier.
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if query is not None:
            params["q"] = query

        if scope is not None:
            params["scope"] = scope

        if trust_tier is not None:
            params["trust_tier"] = trust_tier

        if tags is not None:
            params["tags"] = tags

        response = self._request("GET", "/providers", params=params)
        return PaginatedResponse[StoreProviderSummary].model_validate(response)

    def get_provider(self, provider_name: str) -> StoreProviderDetail:
        """Get detailed info for a provider including versions.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            Provider detail with version history.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("GET", f"/providers/{path}")
        return StoreProviderDetail.model_validate(response)

    def update_provider(self, provider_name: str, metadata: dict[str, Any]) -> StoreProviderDetail:
        """Update provider metadata.

        Args:
            provider_name: Namespaced provider name ('org/name').
            metadata: Fields to update (e.g. display_name, description, tags).

        Returns:
            Updated provider detail.

        Raises:
            httpx.HTTPStatusError: If provider not found or update fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = self._request("PATCH", f"/providers/{path}", json_data=metadata)
        return StoreProviderDetail.model_validate(response)

    def delete_provider(self, provider_name: str) -> None:
        """Delete a provider from the store.

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
        force: bool = False,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> StoreVersion:
        """Publish a new version of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            force: If True, allow overwriting an existing version.
            display_name: Human-friendly provider name for the store listing.
            description: Provider description for the store listing.
            tags: Tags for the store listing.

        Returns:
            Published version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if force:
            data["force"] = "true"

        if display_name is not None:
            data["display_name"] = display_name

        if description is not None:
            data["description"] = description

        if tags is not None:
            data["tags"] = json.dumps(tags)

        response = self._request(
            "POST",
            f"/providers/{path}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return StoreVersion.model_validate(response)

    def get_publish_status(self, provider_name: str, version: str) -> StoreVersion:
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
        return StoreVersion.model_validate(response)

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
    ) -> InstalledProvider:
        """Install a provider from the store.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.

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

        response = self._request("POST", "/providers/install", json_data=data)
        return InstalledProvider.model_validate(response)

    def list_installed_providers(self) -> list[InstalledProviderSummary]:
        """List installed providers for the current tenant.

        Returns:
            List of installed provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = self._request("GET", "/providers/installed")
        return [InstalledProviderSummary.model_validate(item) for item in response]

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

    def upgrade_provider(self, provider_name: str, target_version: str | None = None) -> InstalledProvider:
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
        return InstalledProvider.model_validate(response)

    def downgrade_provider(self, provider_name: str, target_version: str) -> InstalledProvider:
        """Downgrade an installed provider to a specific version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version to downgrade to.

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If downgrade fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {"target_version": target_version}

        response = self._request("POST", f"/providers/installed/{path}/downgrade", json_data=data)
        return InstalledProvider.model_validate(response)

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
        reveal: bool = False,
    ) -> list[ResourceT] | list[dict[str, Any]]:
        """List resources with optional filters.

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
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Get a resource by its full identifier.

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
        resource_id = format_resource_id(provider, resource, name)
        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"
        response = await self._request("GET", f"/resources/{resource_id}", params=params)
        if model is not None:
            return model.model_validate(response)
        return response

    async def apply_resource[ResourceT: Resource](
        self,
        resource: ResourceT | dict[str, Any],
        *,
        model: type[ResourceT] | None = None,
        reveal: bool = False,
    ) -> ResourceT | dict[str, Any]:
        """Apply a resource (create or update).

        Args:
            resource: Resource to apply as typed instance or raw dict.
            model: Resource subclass for typed response; returns raw dict if None.
            reveal: Include sensitive field values in the response.

        Returns:
            Applied resource as typed instance or raw dict.

        Raises:
            httpx.HTTPStatusError: If the apply operation fails.
        """  # noqa: DOC502
        json_data = resource.model_dump() if isinstance(resource, Resource) else resource
        params: dict[str, Any] = {}
        if reveal:
            params["reveal"] = "true"
        response = await self._request("POST", "/resources/apply", json_data=json_data, params=params)
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
        trust_tier: TrustTier | str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PaginatedResponse[StoreProviderSummary]:
        """Browse and search the provider store.

        Args:
            query: Search query string.
            scope: Filter by provider scope (e.g. 'public', 'tenant').
            trust_tier: Filter by trust tier.
            tags: Filter by tags.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            Paginated list of provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        params: dict = {"limit": limit, "offset": offset}

        if query is not None:
            params["q"] = query

        if scope is not None:
            params["scope"] = scope

        if trust_tier is not None:
            params["trust_tier"] = trust_tier

        if tags is not None:
            params["tags"] = tags

        response = await self._request("GET", "/providers", params=params)
        return PaginatedResponse[StoreProviderSummary].model_validate(response)

    async def get_provider(self, provider_name: str) -> StoreProviderDetail:
        """Get detailed info for a provider including versions.

        Args:
            provider_name: Namespaced provider name ('org/name').

        Returns:
            Provider detail with version history.

        Raises:
            httpx.HTTPStatusError: If provider not found or request fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("GET", f"/providers/{path}")
        return StoreProviderDetail.model_validate(response)

    async def update_provider(self, provider_name: str, metadata: dict[str, Any]) -> StoreProviderDetail:
        """Update provider metadata.

        Args:
            provider_name: Namespaced provider name ('org/name').
            metadata: Fields to update (e.g. display_name, description, tags).

        Returns:
            Updated provider detail.

        Raises:
            httpx.HTTPStatusError: If provider not found or update fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        response = await self._request("PATCH", f"/providers/{path}", json_data=metadata)
        return StoreProviderDetail.model_validate(response)

    async def delete_provider(self, provider_name: str) -> None:
        """Delete a provider from the store.

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
        force: bool = False,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> StoreVersion:
        """Publish a new version of a provider.

        Args:
            provider_name: Namespaced provider name ('org/name').
            tarball: Gzipped tarball containing provider source code.
            version: Version string for this release.
            changelog: Optional changelog text.
            force: If True, allow overwriting an existing version.
            display_name: Human-friendly provider name for the store listing.
            description: Provider description for the store listing.
            tags: Tags for the store listing.

        Returns:
            Published version info.

        Raises:
            httpx.HTTPStatusError: If publishing fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict[str, str] = {"version": version}

        if changelog is not None:
            data["changelog"] = changelog

        if force:
            data["force"] = "true"

        if display_name is not None:
            data["display_name"] = display_name

        if description is not None:
            data["description"] = description

        if tags is not None:
            data["tags"] = json.dumps(tags)

        response = await self._request(
            "POST",
            f"/providers/{path}/publish",
            files={"code": ("source.tar.gz", tarball, "application/gzip")},
            data=data,
        )
        return StoreVersion.model_validate(response)

    async def get_publish_status(self, provider_name: str, version: str) -> StoreVersion:
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
        return StoreVersion.model_validate(response)

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
    ) -> InstalledProvider:
        """Install a provider from the store.

        Args:
            provider_name: Namespaced provider name ('org/name').
            version: Specific version to install (latest if None).
            resource_tier: Resource tier for the installation.
            upgrade_policy: Upgrade policy for the installation.

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

        response = await self._request("POST", "/providers/install", json_data=data)
        return InstalledProvider.model_validate(response)

    async def list_installed_providers(self) -> list[InstalledProviderSummary]:
        """List installed providers for the current tenant.

        Returns:
            List of installed provider summaries.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """  # noqa: DOC502
        response = await self._request("GET", "/providers/installed")
        return [InstalledProviderSummary.model_validate(item) for item in response]

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

    async def upgrade_provider(self, provider_name: str, target_version: str | None = None) -> InstalledProvider:
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
        return InstalledProvider.model_validate(response)

    async def downgrade_provider(self, provider_name: str, target_version: str) -> InstalledProvider:
        """Downgrade an installed provider to a specific version.

        Args:
            provider_name: Namespaced provider name ('org/name').
            target_version: Target version to downgrade to.

        Returns:
            Updated installed provider info.

        Raises:
            httpx.HTTPStatusError: If downgrade fails.
        """  # noqa: DOC502
        path = _validate_provider_name(provider_name)
        data: dict = {"target_version": target_version}

        response = await self._request("POST", f"/providers/installed/{path}/downgrade", json_data=data)
        return InstalledProvider.model_validate(response)

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
