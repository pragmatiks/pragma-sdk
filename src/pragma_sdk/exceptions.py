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
