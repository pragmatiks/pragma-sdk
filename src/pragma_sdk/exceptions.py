"""Exceptions for the Pragmatiks SDK.

The messages of the wheel publishing errors say what failed, not what to do
next, which is the caller's to tell its user; the one exception is the
``wheel_too_large`` message, which the SDK words the way the platform does.
"""

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


class WheelExpansionLimitError(Exception):
    """Raised when a wheel's members decompress past an expansion budget.

    Not a ``ValueError``, so it is never mistaken for a malformed wheel. The
    platform fails such a version with the ``wheel_too_large`` category and
    the same message, which ``str()`` gives; ``repr()`` and the attributes
    carry the wheel and the member for logs.

    Attributes:
        limit_bytes: Decompressed bytes the budget allowed.
        wheel_filename: File name of the wheel being read.
        member_name: Archive member whose read passed the budget.
    """

    def __init__(self, limit_bytes: int, wheel_filename: str, member_name: str) -> None:
        """Record the budget and where it ran out.

        Args:
            limit_bytes: Decompressed bytes the budget allowed.
            wheel_filename: File name of the wheel being read.
            member_name: Archive member whose read passed the budget.
        """
        self.limit_bytes = limit_bytes
        self.wheel_filename = wheel_filename
        self.member_name = member_name
        super().__init__(limit_bytes, wheel_filename, member_name)

    def __str__(self) -> str:
        """Describe the refusal the way the platform does.

        Returns:
            The message :func:`build_wheel_too_large_message` builds for the budget.
        """
        return build_wheel_too_large_message(self.limit_bytes)


class RenamedWheelWriteError(Exception):
    """Raised when the renamed copy of a wheel, or the temporary directory it is written to, cannot be written.

    Not an ``OSError``, so it is never mistaken for the source wheel failing
    to open. The ``OSError`` raised is its ``__cause__``; the message leaves
    out the path and leaves what to do next to the caller.

    Attributes:
        reason: What the operating system reported, such as
            ``No space left on device``, or the error's type when it
            reported nothing.
    """

    def __init__(self, error: OSError) -> None:
        """Record what the operating system reported, without the path it names.

        Args:
            error: The ``OSError`` that creating the directory or writing
                the copy raised, to be raised as the ``__cause__``.
        """
        self.reason = error.strerror or type(error).__name__
        super().__init__(error)

    def __str__(self) -> str:
        """Name the failed write and its reason.

        Returns:
            A sentence such as ``Could not write the renamed copy of the
            wheel: No space left on device.``
        """
        return f"Could not write the renamed copy of the wheel: {self.reason}."


class WheelUploadError(Exception):
    """Raised when the storage behind a wheel upload address refuses the upload.

    Carries what storage answered, never the address: the address is a
    bearer capability for writing the wheel.

    Attributes:
        status_code: HTTP status storage answered.
        storage_error_code: Error code from storage's answer, such as
            ``EntityTooLarge``, or ``None`` when it carried none.
        storage_request_id: Storage's identifier of the upload request, for
            its support, or ``None`` when it answered without one; ``str()``
            names it too, so a logged error carries it.
    """

    def __init__(
        self,
        status_code: int,
        storage_error_code: str | None = None,
        storage_request_id: str | None = None,
    ) -> None:
        """Record what storage answered.

        Args:
            status_code: HTTP status storage answered.
            storage_error_code: Error code from storage's answer, or ``None``.
            storage_request_id: Storage's identifier of the upload request, or ``None``.
        """
        self.status_code = status_code
        self.storage_error_code = storage_error_code
        self.storage_request_id = storage_request_id
        super().__init__(status_code, storage_error_code, storage_request_id)

    def __str__(self) -> str:
        """Name the refusal with what storage answered.

        Returns:
            A sentence such as ``Storage refused the wheel upload (HTTP 400
            EntityTooLarge, storage request ABC123).``, as
            :meth:`build_storage_answer` describes the answer.
        """
        return f"Storage refused the wheel upload ({self.build_storage_answer()})."

    def build_storage_answer(self) -> str:
        """Build the description of what storage answered.

        Returns:
            Text such as ``HTTP 400 EntityTooLarge, storage request ABC123``,
            without the code or the request id when storage sent none.
        """
        status_label = f"HTTP {self.status_code}"

        if self.storage_error_code:
            status_label += f" {self.storage_error_code}"

        details = [status_label]

        if self.storage_request_id:
            details.append(f"storage request {self.storage_request_id}")

        return ", ".join(details)


class WheelUploadExpiredError(WheelUploadError):
    """Raised when a wheel upload address expired before storage received the whole wheel.

    Storage checks the expiry once the whole body has arrived, so an upload
    that started in time but finished late is refused too.
    """

    def __str__(self) -> str:
        """Name the expiry with what storage answered.

        Returns:
            A sentence such as ``The wheel upload address expired before
            storage received the whole wheel (HTTP 400 ExpiredToken, storage
            request ABC123).``
        """
        return (
            f"The wheel upload address expired before storage received the whole wheel ({self.build_storage_answer()})."
        )


def build_wheel_too_large_message(limit_bytes: int) -> str:
    """Build the message refusing a wheel that expands past a limit.

    The platform refuses such a wheel with the same message, built from
    :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES`.

    Args:
        limit_bytes: Decompressed bytes the wheel may expand to.

    Returns:
        A sentence naming the limit in MiB, followed by what to do.
    """
    return f"The wheel expands to more than {limit_bytes // (1024 * 1024)} MiB. Reduce its size and publish it again."
