"""Exceptions for the Pragmatiks SDK.

The messages of the wheel publishing errors say what failed, not what to do
next, which is the caller's to tell its user. The exceptions are the
``wheel_too_large`` message, which the SDK words the way the platform does,
and ``FileUploadNotStoredError`` and ``FileUploadPendingError``, whose
messages say what to do and name the upload operation.
"""

from __future__ import annotations

import zipfile
from enum import StrEnum
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


class AlreadyExistsError(Exception):
    """Raised when the object at a resource's identity is not one this resource created.

    Raise it from ``on_create`` on a vendor HTTP 409 or ``AlreadyExists``
    error instead of adopting an object Pragmatiks did not create, or from
    ``on_observe`` for an object at the resource's identity this resource
    did not create: one that the create's conflict path would refuse, such
    as an object without this resource's marker. The platform then never
    writes or deletes that object: a create or update fails, and a delete
    leaves it in place. Raised from any other lifecycle method, it is
    treated as any other exception.
    """


class CallbackRefusedError(RuntimeError):
    """Raised from a platform callback, such as ``Resource.apply`` or ``Resource.read``, that the platform refused.

    Its text is the platform's reason alone, worded for the resource's user,
    for example that the resource to apply is already owned by another
    resource. Let it propagate: the operation then fails with that text as
    its reason.
    """


class UnresolvedReferenceError(RuntimeError):
    """Raised by ``Resource.resolved`` for a config value that is still a field reference.

    The platform resolves every field reference before ``on_create``,
    ``on_update`` or ``on_delete`` runs, so an unresolved one was read
    elsewhere, such as in a child's config from ``Resource.read``. The
    message names the reference's resource identity and field, never a
    value.
    """


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


class LogFollowInterruptedError(Exception):
    """Raised when a followed log stream breaks before an ``end`` or ``error`` event.

    The connection to the API was lost or went silent mid-stream, so entries
    written after the last one received were not delivered. Follow again with
    ``since`` set to the last entry's timestamp to continue: ``since`` is
    inclusive, so entries carrying that exact timestamp arrive again, and
    ``tail`` caps how many entries the new stream sends first.
    """

    def __init__(self) -> None:
        """Build the message naming the interruption."""
        super().__init__("The log stream closed before it ended. Follow again to continue.")


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


class WheelCentralDirectoryRefusedError(zipfile.BadZipFile):
    """Raised when a wheel's zip central directory is refused before any member is listed.

    A :class:`zipfile.BadZipFile`, so callers that treat an unreadable wheel
    as ``wheel_unreadable`` refuse it the same way; the type lets logs name
    the reason. Raised when the central directory passes
    :data:`pragma_sdk.wheel_central_directory.MAXIMUM_CENTRAL_DIRECTORY_BYTES`,
    the archive spans several disks, or its zip64 layout could be read
    differently by releases of :mod:`zipfile`.
    """


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


class UploadError(Exception):
    """Raised when the storage behind an upload address refuses an upload of a wheel or a file.

    Carries what storage answered, never the address: the address is a
    bearer capability for writing the declared bytes.

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
            A sentence such as ``Storage refused the upload (HTTP 400
            EntityTooLarge, storage request ABC123).``, as
            :meth:`build_storage_answer` describes the answer.
        """
        return f"Storage refused the upload ({self.build_storage_answer()})."

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


class UploadExpiredError(UploadError):
    """Raised when an upload address expired before storage received the whole upload.

    Storage checks the expiry once the whole body has arrived, so an upload
    that started in time but finished late is refused too.
    """

    def __str__(self) -> str:
        """Name the expiry with what storage answered.

        Returns:
            A sentence such as ``The upload address expired before storage
            received the whole upload (HTTP 400 ExpiredToken, storage request
            ABC123).``
        """
        return f"The upload address expired before storage received the whole upload ({self.build_storage_answer()})."


class FileUploadNotStoredReason(StrEnum):
    """Why an upload of a file ended without storing it.

    Attributes:
        CHANGED: The file ended before its size while it was hashed or
            sent, or storage refused its bytes as not matching the declared
            MD5: its content changed between the two reads.
        SUPERSEDED: A declaration of other content for the name replaced the upload.
        FAILED: Storing the upload failed.
        EXPIRED: The bytes did not reach storage before the upload address expired.
        DELETED: The upload was stored, then the file was deleted or replaced.
        BUSY: An upload of other content for the name stayed in storing
            past the wait, so this upload could not be declared.
    """

    CHANGED = "changed"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"
    BUSY = "busy"


class FileUploadNotStoredError(Exception):
    """Raised when an upload of a file ended without the file holding its content.

    Attributes:
        file_name: Name of the file in its project.
        reason: Why the upload ended.
        operation_id: Upload operation that ended, matching the platform's
            record of it, or ``None`` when the upload ended before it was
            declared: ``busy``, or ``changed`` while it was hashed.
        error_message: Pragmatiks' explanation, for ``failed`` and ``expired``
            when the platform recorded one, else ``None``.
    """

    def __init__(
        self,
        file_name: str,
        reason: FileUploadNotStoredReason,
        operation_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record the file, why its upload ended and which operation ended.

        Args:
            file_name: Name of the file in its project.
            reason: Why the upload ended.
            operation_id: Upload operation that ended, or ``None``.
            error_message: Pragmatiks' explanation, or ``None``.
        """
        self.file_name = file_name
        self.reason = reason
        self.operation_id = operation_id
        self.error_message = error_message
        super().__init__(file_name, reason, operation_id, error_message)

    def __str__(self) -> str:
        """Name why the upload ended, what to do and the upload operation.

        Returns:
            Pragmatiks' explanation when it gave one, otherwise a sentence
            for the reason, each ending with what to do, then ``Upload
            operation <id>.`` when the upload was declared.
        """
        explanation = self.error_message or self.build_reason_message()

        if self.operation_id is None:
            return explanation

        return f"{explanation} Upload operation {self.operation_id}."

    def build_reason_message(self) -> str:
        """Build the sentence naming why the upload ended and what to do.

        Returns:
            A sentence such as ``Pragmatiks could not store report.pdf.
            Upload it again.``
        """
        match self.reason:
            case FileUploadNotStoredReason.CHANGED:
                return f"{self.file_name} changed while it was being uploaded. Upload it again."
            case FileUploadNotStoredReason.SUPERSEDED:
                return f"An upload of other content for {self.file_name} replaced this one. Upload it again."
            case FileUploadNotStoredReason.DELETED:
                return f"{self.file_name} was deleted or replaced after it was stored. Upload it again."
            case FileUploadNotStoredReason.EXPIRED:
                return (
                    f"The upload of {self.file_name} did not reach storage before its address expired. Upload it again."
                )
            case FileUploadNotStoredReason.BUSY:
                return (
                    f"An upload of other content for {self.file_name} is still being stored. "
                    "Upload it again once it is stored."
                )
            case FileUploadNotStoredReason.FAILED:
                return f"Pragmatiks could not store {self.file_name}. Upload it again."


class FileUploadPendingError(Exception):
    """Raised when an upload of a file reached Pragmatiks and made no visible progress within the wait.

    The upload may still complete: uploading the same content again keeps
    waiting for it, and its bytes are not sent again.

    Attributes:
        file_name: Name of the file in its project.
        operation_id: Upload operation still pending.
    """

    def __init__(self, file_name: str, operation_id: str) -> None:
        """Record the file and its pending operation.

        Args:
            file_name: Name of the file in its project.
            operation_id: Upload operation still pending.
        """
        self.file_name = file_name
        self.operation_id = operation_id
        super().__init__(file_name, operation_id)

    def __str__(self) -> str:
        """Name the pending upload, what to do and the upload operation.

        Returns:
            A sentence saying the upload may still complete, that uploading
            it again keeps waiting without sending the bytes again, then
            ``Upload operation <id>.``
        """
        return (
            f"{self.file_name} reached Pragmatiks and is not stored yet; it may still complete. "
            "Upload it again to keep waiting; the bytes are not sent again. "
            f"Upload operation {self.operation_id}."
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
