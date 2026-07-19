"""Exceptions for the Pragma SDK."""

from __future__ import annotations

from typing import Any


class ProjectMismatchError(ValueError):
    """Raised when a resource's project_id does not match a scoped project handle.

    Attributes:
        expected_project_id: Project ID of the scoped handle.
        actual_project_id: Project ID carried on the resource.
    """

    def __init__(self, expected_project_id: str, actual_project_id: str) -> None:
        """Build a descriptive mismatch message."""
        self.expected_project_id = expected_project_id
        self.actual_project_id = actual_project_id
        super().__init__(
            f"Resource belongs to project {actual_project_id!r} but was submitted "
            f"through a handle scoped to project {expected_project_id!r}."
        )


class ResourceFailedError(Exception):
    """Raised when a resource transitions to FAILED state during wait operations.

    Attributes:
        resource_id: The ID of the resource that failed.
        error: Error message from the failed resource.
        resource_data: Full resource data from the state notification.
    """

    def __init__(
        self,
        resource_id: str,
        error: str | None = None,
        resource_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize ResourceFailedError with resource details."""
        self.resource_id = resource_id
        self.error = error
        self.resource_data = resource_data
        message = f"Resource {resource_id} failed"

        if error:
            message += f": {error}"

        super().__init__(message)


class ProviderVersionConflictError(Exception):
    """Raised when publishing a provider version that already exists.

    Published versions are immutable. The server rejects a re-publish of an
    existing ``name``/``version`` pair with HTTP 409. Bump the version rather
    than retrying the same one — the same bytes will never publish twice.

    Attributes:
        name: Namespaced provider name that was targeted.
        version: Version that already exists in the catalog.
    """

    def __init__(self, name: str, version: str) -> None:
        """Initialize ProviderVersionConflictError with the rejected identity."""
        self.name = name
        self.version = version
        super().__init__(f"Provider version {name!r} v{version} already exists and is immutable; bump the version.")


class ProjectHasResourcesError(Exception):
    """Raised when deleting a non-empty project without orphan_resources=True.

    The server rejects the delete with HTTP 409 when the target project
    still contains resources and the caller did not opt into orphaning.
    Pass ``orphan_resources=True`` on :class:`DeleteProjectRequest` to
    bypass this safety check, or delete the resources first.

    Attributes:
        project_id: ID of the project whose deletion was rejected.
        resource_count: Authoritative count of resources still in the
            project at the moment the server refused the delete.
        resources: Bounded sample of resource IDs (up to 20). Not the
            complete list — use the count for totals.
    """

    def __init__(self, project_id: str, resource_count: int, resources: list[str]) -> None:
        """Initialize ProjectHasResourcesError with rejected project details."""
        self.project_id = project_id
        self.resource_count = resource_count
        self.resources = resources
        super().__init__(
            f"Project {project_id!r} still contains {resource_count} resource(s); "
            "delete them first or pass orphan_resources=True."
        )
