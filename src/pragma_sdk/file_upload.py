"""Uploading a project's file straight to storage: its source, its MD5, the streamed ``PUT`` and the wait until stored.

The file is read twice: once to hash it, then once to send exactly the
hashed number of bytes. Storage refuses bytes whose MD5 differs from the
declared one, so content that changed between the two reads is never
stored.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import os
import stat
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import httpx

from pragma_sdk.blocking import run_in_worker_thread
from pragma_sdk.exceptions import (
    FileUploadNotStoredError,
    FileUploadNotStoredReason,
    FileUploadPendingError,
    UploadError,
    UploadExpiredError,
)
from pragma_sdk.models import (
    DeclaredFileUpload,
    FileMetadata,
    FileUploadOutcome,
    FileUploadProgress,
    UploadAddress,
    UploadOutcome,
)
from pragma_sdk.storage_upload import (
    build_upload_request,
    close_recorded_files,
    open_recorded_file,
    parse_upload_outcome,
    read_file_chunks,
    read_file_chunks_async,
    send_upload,
    send_upload_async,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Generator


FILE_UPLOAD_STALL_SECONDS = 180.0
"""Longest the wait for a stored file goes without visible progress, in seconds.

Three of storage's copy-call timeouts: the longest stretch in which storing a
large file shows nothing.
"""

FILE_UPLOAD_POLL_SECONDS = 1.0
"""Seconds between two reads of an upload's progress."""

FILE_UPLOAD_BUSY_SECONDS = 180.0
"""Longest a declaration waits out an upload of other content being stored under the name, in seconds."""

FILE_UPLOAD_BUSY_RETRY_SECONDS = 5.0
"""Seconds to wait before declaring again when a busy answer carries no usable ``Retry-After``."""

FILE_UPLOAD_IN_PROGRESS_ERROR = "file_upload_in_progress"
"""Error code of the 409 answering a declaration while an upload of other content is being stored."""

BAD_DIGEST_ERROR_CODE = "BadDigest"
"""Error code storage answers with, in a 400, to bytes whose MD5 differs from the declared one."""


@dataclass(frozen=True)
class UploadSource:
    """The bytes of a file to upload: an open binary file and the number of bytes from its start.

    Attributes:
        file: Binary file read from its start, twice: to hash it and to send it.
        size: Bytes to hash and send.
    """

    file: BinaryIO
    size: int


class FileUploadWaitStep(StrEnum):
    """Where a wait for a stored file stands after one read of a pending upload's progress.

    Attributes:
        ADVANCED: The upload showed progress since the last read.
        UNCHANGED: The upload showed no progress since the last read.
    """

    ADVANCED = "advanced"
    UNCHANGED = "unchanged"


def build_memory_source(content: bytes) -> UploadSource:
    """Build the source of content held in memory.

    Args:
        content: The file's bytes.

    Returns:
        A source reading ``content`` without copying it.
    """
    return UploadSource(io.BytesIO(content), len(content))


def read_file_source(file: BinaryIO) -> UploadSource:
    """Read the size of an open file to upload, which must be a regular file opened for binary reading.

    Args:
        file: The file; it is read from its start, whatever its position.

    Returns:
        A source of the file's size when this reads it.

    Raises:
        TypeError: If the file is opened in text mode.
        ValueError: If the file is not a regular file, such as a pipe or a
            directory.
        OSError: If the file cannot be inspected.
    """  # noqa: DOC502
    if isinstance(file, io.TextIOBase):
        raise TypeError("The file to upload must be opened for binary reading.")

    file_status = os.fstat(file.fileno())

    if not stat.S_ISREG(file_status.st_mode):
        raise ValueError("The file to upload must be a regular file.")

    return UploadSource(file, file_status.st_size)


@contextlib.contextmanager
def open_upload_source(source: bytes | Path | BinaryIO) -> Generator[UploadSource]:
    """Open the source of a file upload, closing a file it opened on exit.

    Args:
        source: Bytes held in memory, the path of a file opened here, or a
            regular file opened for binary reading, which stays open.

    Yields:
        The source to hash and send.

    Raises:
        TypeError: As :func:`read_file_source` describes.
        ValueError: As :func:`read_file_source` describes.
        OSError: If the file cannot be opened or inspected.
    """  # noqa: DOC502
    if isinstance(source, bytes):
        yield build_memory_source(source)
        return

    if not isinstance(source, Path):
        yield read_file_source(source)
        return

    with source.open("rb") as file:
        yield read_file_source(file)


@contextlib.asynccontextmanager
async def open_upload_source_async(source: bytes | Path | BinaryIO) -> AsyncGenerator[UploadSource]:
    """Async mirror of :func:`open_upload_source`, the file opened, inspected and closed off the event loop.

    A file opened here is closed even when the caller is cancelled while it opens.

    Args:
        source: Bytes held in memory, the path of a file opened here, or a
            regular file opened for binary reading, which stays open.

    Yields:
        The source to hash and send.

    Raises:
        TypeError: As :func:`read_file_source` describes.
        ValueError: As :func:`read_file_source` describes.
        OSError: If the file cannot be opened or inspected.
    """  # noqa: DOC502
    if isinstance(source, bytes):
        yield build_memory_source(source)
        return

    if not isinstance(source, Path):
        yield await run_in_worker_thread(read_file_source, source)
        return

    opened_files: list[io.FileIO] = []

    try:
        file = await run_in_worker_thread(open_recorded_file, source, opened_files)
        yield await run_in_worker_thread(read_file_source, file)
    finally:
        await close_recorded_files(opened_files)


def read_content_md5(file: BinaryIO, size: int) -> str:
    """Read a number of bytes of a file from its start, in chunks, and hash them.

    Args:
        file: File opened for binary reading.
        size: Bytes to hash.

    Returns:
        Base64 of the bytes' 16-byte MD5 digest, as ``Content-MD5`` carries it.

    Raises:
        EOFError: If the file ends before ``size`` bytes.
        OSError: If the file cannot be read.
    """  # noqa: DOC502
    file.seek(0)
    digest = hashlib.md5(usedforsecurity=False)

    for chunk in read_file_chunks(file, size):
        digest.update(chunk)

    return base64.b64encode(digest.digest()).decode("ascii")


@contextlib.contextmanager
def reporting_changed_source(file_name: str, operation_id: str | None) -> Generator[None]:
    """Report a file that ended before its size, while it is hashed or sent, as changed.

    Args:
        file_name: Name of the file in its project.
        operation_id: Upload operation the bytes are sent for, or ``None``
            while the file is hashed before its declaration.

    Yields:
        Nothing; the body hashes or sends the file.

    Raises:
        FileUploadNotStoredError: ``changed``, if the body raises
            ``EOFError``, its ``__cause__``.
    """
    try:
        yield
    except EOFError as error:
        raise FileUploadNotStoredError(file_name, FileUploadNotStoredReason.CHANGED, operation_id) from error


def parse_file_upload_outcome(response: httpx.Response, file_name: str, operation_id: str) -> UploadOutcome:
    """Parse how storage took a file's upload, reporting refused bytes and an expired address as an unstored file.

    Args:
        response: Storage's answer to the upload, read.
        file_name: Name of the file in its project.
        operation_id: Upload operation the bytes were sent for.

    Returns:
        As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` returns.

    Raises:
        FileUploadNotStoredError: ``changed`` if storage refuses the bytes
            with ``BadDigest``, ``expired`` if the address expired before
            storage received the whole file; storage's refusal is its
            ``__cause__``.
        UploadError: As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` describes.
    """  # noqa: DOC502
    try:
        return parse_upload_outcome(response)
    except UploadExpiredError as error:
        raise FileUploadNotStoredError(file_name, FileUploadNotStoredReason.EXPIRED, operation_id) from error
    except UploadError as error:
        if error.storage_error_code != BAD_DIGEST_ERROR_CODE:
            raise

        raise FileUploadNotStoredError(file_name, FileUploadNotStoredReason.CHANGED, operation_id) from error


def send_file_upload(
    transport: httpx.BaseTransport,
    address: UploadAddress,
    source: UploadSource,
    file_name: str,
    operation_id: str,
) -> UploadOutcome:
    """Stream exactly a source's size from the start of its file to the upload address, in one ``PUT``.

    Args:
        transport: Transport the upload is sent through, with no API credentials.
        address: The declaration's upload address.
        source: The file and the size its MD5 was declared over.
        file_name: Name of the file in its project.
        operation_id: Upload operation the declaration answered.

    Returns:
        Whether this upload stored the bytes or found them already stored.

    Raises:
        FileUploadNotStoredError: ``changed`` if the file ends before its
            size or storage refuses the bytes as not matching the declared
            MD5; ``expired`` if the address expired before storage received
            the whole file.
        UploadError: If storage refuses the upload otherwise.
        OSError: If the file cannot be read.
        httpx.TransportError: As :func:`~pragma_sdk.storage_upload.send_upload` describes.
    """  # noqa: DOC502
    with reporting_changed_source(file_name, operation_id):
        source.file.seek(0)
        request = build_upload_request(address, read_file_chunks(source.file, source.size), source.size)
        response = send_upload(transport, request)

    return parse_file_upload_outcome(response, file_name, operation_id)


async def send_file_upload_async(
    transport: httpx.AsyncBaseTransport,
    address: UploadAddress,
    source: UploadSource,
    file_name: str,
    operation_id: str,
) -> UploadOutcome:
    """Async mirror of :func:`send_file_upload`, the file read off the event loop.

    Args:
        transport: Transport the upload is sent through, with no API credentials.
        address: The declaration's upload address.
        source: The file and the size its MD5 was declared over.
        file_name: Name of the file in its project.
        operation_id: Upload operation the declaration answered.

    Returns:
        Whether this upload stored the bytes or found them already stored.

    Raises:
        FileUploadNotStoredError: As :func:`send_file_upload` describes.
        UploadError: If storage refuses the upload otherwise.
        OSError: If the file cannot be read.
        httpx.TransportError: As :func:`~pragma_sdk.storage_upload.send_upload` describes.
    """  # noqa: DOC502
    with reporting_changed_source(file_name, operation_id):
        await run_in_worker_thread(source.file.seek, 0)

        async with contextlib.aclosing(read_file_chunks_async(source.file, source.size)) as chunks:
            request = build_upload_request(address, chunks, source.size)
            response = await send_upload_async(transport, request)

    return parse_file_upload_outcome(response, file_name, operation_id)


def parse_busy_retry_delay(error: httpx.HTTPStatusError) -> float | None:
    """Parse how long to wait before declaring again from a refused declaration.

    Args:
        error: The refused declaration.

    Returns:
        The ``Retry-After`` seconds of a ``409 file_upload_in_progress``, at
        least :data:`FILE_UPLOAD_POLL_SECONDS`, or
        :data:`FILE_UPLOAD_BUSY_RETRY_SECONDS` when it carries no usable
        one; ``None`` for any other refusal.
    """
    response = error.response

    if response.status_code != httpx.codes.CONFLICT:
        return None

    try:
        detail = response.json().get("detail")
    except ValueError:
        return None

    if not isinstance(detail, dict) or detail.get("error") != FILE_UPLOAD_IN_PROGRESS_ERROR:
        return None

    retry_after = response.headers.get("Retry-After", "")

    if not retry_after.isdigit():
        return FILE_UPLOAD_BUSY_RETRY_SECONDS

    return max(float(retry_after), FILE_UPLOAD_POLL_SECONDS)


def decide_file_upload_progress(
    progress: FileUploadProgress, operation_id: str, last_seen: FileUploadProgress | None
) -> FileMetadata | FileUploadWaitStep | FileUploadNotStoredReason:
    """Decide where a wait for a stored file stands from one read of its upload's progress.

    Progress is a first read, or a rise in ``stored_bytes``.

    Args:
        progress: The latest read.
        operation_id: Upload operation the wait is for.
        last_seen: The read before it, or ``None`` for the first.

    Returns:
        The stored file's metadata once the file holds this operation's
        content; ``advanced`` or ``unchanged`` while it is pending;
        otherwise why it ended unstored: ``superseded`` when another
        operation replaced it, ``deleted`` when the file no longer holds
        it, ``failed`` or ``expired``.
    """
    if progress.operation_id != operation_id:
        return FileUploadNotStoredReason.SUPERSEDED

    match progress.outcome:
        case FileUploadOutcome.STORED if progress.file is not None:
            return progress.file
        case FileUploadOutcome.STORED | FileUploadOutcome.DELETED:
            return FileUploadNotStoredReason.DELETED
        case FileUploadOutcome.FAILED:
            return FileUploadNotStoredReason.FAILED
        case FileUploadOutcome.EXPIRED:
            return FileUploadNotStoredReason.EXPIRED
        case FileUploadOutcome.PENDING:
            if last_seen is None or (progress.stored_bytes or 0) > (last_seen.stored_bytes or 0):
                return FileUploadWaitStep.ADVANCED

            return FileUploadWaitStep.UNCHANGED


def declare_file_upload_when_free(declare: Callable[[], DeclaredFileUpload], file_name: str) -> DeclaredFileUpload:
    """Declare an upload, declaring again while an upload of other content for the name is being stored.

    A ``409 file_upload_in_progress`` is waited out for its ``Retry-After``
    and declared again, for up to :data:`FILE_UPLOAD_BUSY_SECONDS`.

    Args:
        declare: Sends the declaration once and returns the answer.
        file_name: Name of the file in its project.

    Returns:
        The first declaration answered.

    Raises:
        FileUploadNotStoredError: ``busy``, if the other upload is still
            being stored when the wait ends.
        httpx.HTTPStatusError: Every other refusal of the declaration.
    """  # noqa: DOC502
    deadline = time.monotonic() + FILE_UPLOAD_BUSY_SECONDS

    while True:
        try:
            return declare()
        except httpx.HTTPStatusError as error:
            delay = parse_busy_retry_delay(error)

            if delay is None:
                raise

            if time.monotonic() + delay > deadline:
                raise FileUploadNotStoredError(file_name, FileUploadNotStoredReason.BUSY) from error

            time.sleep(delay)


async def declare_file_upload_when_free_async(
    declare: Callable[[], Awaitable[DeclaredFileUpload]], file_name: str
) -> DeclaredFileUpload:
    """Async mirror of :func:`declare_file_upload_when_free`.

    Args:
        declare: Sends the declaration once and returns the answer.
        file_name: Name of the file in its project.

    Returns:
        The first declaration answered.

    Raises:
        FileUploadNotStoredError: ``busy``, if the other upload is still
            being stored when the wait ends.
        httpx.HTTPStatusError: Every other refusal of the declaration.
    """  # noqa: DOC502
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILE_UPLOAD_BUSY_SECONDS

    while True:
        try:
            return await declare()
        except httpx.HTTPStatusError as error:
            delay = parse_busy_retry_delay(error)

            if delay is None:
                raise

            if loop.time() + delay > deadline:
                raise FileUploadNotStoredError(file_name, FileUploadNotStoredReason.BUSY) from error

            await asyncio.sleep(delay)


def wait_file_stored(
    read_progress: Callable[[], FileUploadProgress], file_name: str, operation_id: str
) -> FileMetadata:
    """Read an upload's progress every :data:`FILE_UPLOAD_POLL_SECONDS` until the file holds its content.

    The wait lasts as long as the upload shows progress, and ends after
    :data:`FILE_UPLOAD_STALL_SECONDS` without any.

    Args:
        read_progress: Reads the upload's progress once.
        file_name: Name of the file in its project.
        operation_id: Upload operation the declaration answered.

    Returns:
        The stored file's metadata.

    Raises:
        FileUploadNotStoredError: If the upload ended without storing the file.
        FileUploadPendingError: If the upload showed no progress within the wait.
        httpx.HTTPStatusError: If a read of the progress is refused.
    """  # noqa: DOC502
    last_seen: FileUploadProgress | None = None
    last_advanced_at = time.monotonic()

    while True:
        progress = read_progress()
        step = decide_file_upload_progress(progress, operation_id, last_seen)

        match step:
            case FileMetadata():
                return step
            case FileUploadNotStoredReason():
                raise FileUploadNotStoredError(file_name, step, operation_id, progress.error_message)
            case FileUploadWaitStep.ADVANCED:
                last_advanced_at = time.monotonic()
            case FileUploadWaitStep.UNCHANGED if time.monotonic() - last_advanced_at >= FILE_UPLOAD_STALL_SECONDS:
                raise FileUploadPendingError(file_name, operation_id)

        last_seen = progress
        time.sleep(FILE_UPLOAD_POLL_SECONDS)


async def wait_file_stored_async(
    read_progress: Callable[[], Awaitable[FileUploadProgress]], file_name: str, operation_id: str
) -> FileMetadata:
    """Async mirror of :func:`wait_file_stored`.

    Args:
        read_progress: Reads the upload's progress once.
        file_name: Name of the file in its project.
        operation_id: Upload operation the declaration answered.

    Returns:
        The stored file's metadata.

    Raises:
        FileUploadNotStoredError: If the upload ended without storing the file.
        FileUploadPendingError: If the upload showed no progress within the wait.
        httpx.HTTPStatusError: If a read of the progress is refused.
    """  # noqa: DOC502
    loop = asyncio.get_running_loop()
    last_seen: FileUploadProgress | None = None
    last_advanced_at = loop.time()

    while True:
        progress = await read_progress()
        step = decide_file_upload_progress(progress, operation_id, last_seen)

        match step:
            case FileMetadata():
                return step
            case FileUploadNotStoredReason():
                raise FileUploadNotStoredError(file_name, step, operation_id, progress.error_message)
            case FileUploadWaitStep.ADVANCED:
                last_advanced_at = loop.time()
            case FileUploadWaitStep.UNCHANGED if loop.time() - last_advanced_at >= FILE_UPLOAD_STALL_SECONDS:
                raise FileUploadPendingError(file_name, operation_id)

        last_seen = progress
        await asyncio.sleep(FILE_UPLOAD_POLL_SECONDS)
