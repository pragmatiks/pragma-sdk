"""Uploading declared bytes, a wheel or a file, straight to their signed storage address.

The upload contract assumes the platform's storage answers a refusal with an
XML error body carrying a ``<Code>`` element, names an expired address with
the ``ExpiredToken`` code, and identifies the upload request in the
``x-guploader-uploadid`` header; only :func:`parse_upload_outcome` and its
callers read those.
"""

from __future__ import annotations

import contextlib
import io
import re
from typing import TYPE_CHECKING, BinaryIO

import httpx

from pragma_sdk.blocking import run_in_worker_thread
from pragma_sdk.exceptions import UploadError, UploadExpiredError
from pragma_sdk.models import UploadAddress, UploadOutcome


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
    from pathlib import Path


UPLOAD_TIMEOUT_SECONDS = 60.0
"""Longest an upload waits on any one connect, write or read, in seconds.

No total timeout applies: the address's expiry bounds the whole transfer.
"""

UPLOAD_CHUNK_SIZE = 64 * 1024
"""Bytes each read of an uploaded file takes, and each write of the upload sends; the write timeout bounds each."""

EXPIRED_UPLOAD_ERROR_CODE = "ExpiredToken"
"""Error code storage answers with, in a 400, to an upload whose address expired."""

STORAGE_ERROR_CODE_PATTERN = re.compile(r"<Code>([^<]*)</Code>")
"""The ``<Code>`` element of storage's XML error answer, capturing the error code."""

STORAGE_REQUEST_ID_HEADER = "x-guploader-uploadid"
"""Response header carrying storage's identifier of an upload request."""


def read_next_chunk(file: BinaryIO, remaining: int) -> bytes:
    """Read the next chunk of a file that must still hold a number of bytes.

    Args:
        file: File opened for binary reading.
        remaining: Bytes the file must still hold from its position, more than ``0``.

    Returns:
        At most :data:`UPLOAD_CHUNK_SIZE` bytes, and never more than ``remaining``.

    Raises:
        EOFError: If the file ends before ``remaining`` bytes.
        OSError: If the file cannot be read.
    """  # noqa: DOC502
    chunk = file.read(min(UPLOAD_CHUNK_SIZE, remaining))

    if not chunk:
        raise EOFError(f"The file ended {remaining} bytes before its declared size.")

    return chunk


def read_file_chunks(file: BinaryIO, size: int) -> Generator[bytes]:
    """Stream exactly a number of bytes of an open file from its position, in chunks.

    Bytes past ``size``, such as those of a file that grew, are never read.

    Args:
        file: File opened for binary reading.
        size: Bytes to read.

    Yields:
        Consecutive chunks of at most :data:`UPLOAD_CHUNK_SIZE` bytes, ``size`` bytes in all.

    Raises:
        EOFError: If the file ends before ``size`` bytes.
        OSError: If the file cannot be read.
    """  # noqa: DOC502
    remaining = size

    while remaining:
        chunk = read_next_chunk(file, remaining)
        remaining -= len(chunk)
        yield chunk


async def read_file_chunks_async(file: BinaryIO, size: int) -> AsyncGenerator[bytes]:
    """Async mirror of :func:`read_file_chunks`, each read off the event loop.

    Args:
        file: File opened for binary reading.
        size: Bytes to read.

    Yields:
        Consecutive chunks of at most :data:`UPLOAD_CHUNK_SIZE` bytes, ``size`` bytes in all.

    Raises:
        EOFError: If the file ends before ``size`` bytes.
        OSError: If the file cannot be read.
    """  # noqa: DOC502
    remaining = size

    while remaining:
        chunk = await run_in_worker_thread(read_next_chunk, file, remaining)
        remaining -= len(chunk)
        yield chunk


def open_recorded_file(path: Path, opened_files: list[io.FileIO]) -> io.FileIO:
    """Open a file for reading and record it in a list the caller holds.

    The record lets a caller cancelled while the file opens still close it.

    Args:
        path: File to open.
        opened_files: List the opened file is appended to.

    Returns:
        The file, opened for reading in binary mode.

    Raises:
        OSError: If the file cannot be opened.
    """  # noqa: DOC502
    file = io.FileIO(path)
    opened_files.append(file)
    return file


async def close_recorded_files(opened_files: list[io.FileIO]) -> None:
    """Close the files :func:`open_recorded_file` recorded, off the event loop.

    Args:
        opened_files: Files to close.
    """
    for opened_file in opened_files:
        await run_in_worker_thread(opened_file.close)


def build_upload_request(
    address: UploadAddress,
    content: Iterator[bytes] | AsyncIterator[bytes],
    size: int,
) -> httpx.Request:
    """Build the ``PUT`` that uploads declared bytes to their address.

    Args:
        address: The address the bytes are uploaded to.
        content: The bytes, streamed as the body.
        size: The declared size in bytes.

    Returns:
        A request carrying the address's signed headers unchanged plus
        ``Content-Length``, timed out after :data:`UPLOAD_TIMEOUT_SECONDS`
        on any one connect, write or read.
    """
    return httpx.Request(
        "PUT",
        address.url.get_secret_value(),
        content=content,
        headers=build_upload_headers(address, size),
        extensions={"timeout": httpx.Timeout(UPLOAD_TIMEOUT_SECONDS).as_dict()},
    )


def build_upload_headers(address: UploadAddress, size: int) -> httpx.Headers:
    """Build the headers an upload sends.

    Args:
        address: The address the bytes are uploaded to.
        size: The declared size in bytes.

    Returns:
        The address's signed headers unchanged, with a single
        ``Content-Length`` of ``size`` replacing one they carry in any case.
    """
    headers = httpx.Headers(address.headers)
    headers["Content-Length"] = str(size)
    return headers


def send_upload(transport: httpx.BaseTransport, request: httpx.Request) -> httpx.Response:
    """Send an upload through a transport and read storage's whole answer.

    Sending through the transport, not a client, keeps the signed address
    out of the client's request log.

    Args:
        transport: Transport the upload is sent through.
        request: The upload, as :func:`build_upload_request` builds it.

    Returns:
        Storage's answer, read and closed.

    Raises:
        httpx.TransportError: If storage cannot be reached, a connect, write
            or read times out, or the upload or answer breaks off; its
            request is the upload without the address's signed query.
    """  # noqa: DOC502
    try:
        response = transport.handle_request(request)

        with contextlib.closing(response):
            response.read()
    except httpx.TransportError as error:
        error.request = build_unsigned_request(request)
        raise

    return response


async def send_upload_async(transport: httpx.AsyncBaseTransport, request: httpx.Request) -> httpx.Response:
    """Async mirror of :func:`send_upload`.

    Args:
        transport: Transport the upload is sent through.
        request: The upload, as :func:`build_upload_request` builds it.

    Returns:
        Storage's answer, read and closed.

    Raises:
        httpx.TransportError: If storage cannot be reached, a connect, write
            or read times out, or the upload or answer breaks off; its
            request is the upload without the address's signed query.
    """  # noqa: DOC502
    try:
        response = await transport.handle_async_request(request)

        async with contextlib.aclosing(response):
            await response.aread()
    except httpx.TransportError as error:
        error.request = build_unsigned_request(request)
        raise

    return response


def build_unsigned_request(request: httpx.Request) -> httpx.Request:
    """Build a copy of an upload request that carries no signature.

    Args:
        request: The upload request.

    Returns:
        A bodiless request with the same method and the URL without its
        query, where a signed address carries its signature.
    """
    return httpx.Request(request.method, request.url.copy_with(query=None))


def parse_upload_outcome(response: httpx.Response) -> UploadOutcome:
    """Parse how storage took an upload from its answer, raising when the bytes are left unstored.

    A 2xx means this upload stored the bytes; a 412 means they were already
    stored under the address, which counts as stored too.

    Args:
        response: Storage's answer to the upload, read.

    Returns:
        ``already_stored`` for a 412, ``stored`` for a 2xx.

    Raises:
        UploadExpiredError: If storage answers 400 with the ``ExpiredToken``
            error code.
        UploadError: For every other status; it carries the status,
            storage's error code and request id, never the address.
    """
    if response.status_code == httpx.codes.PRECONDITION_FAILED:
        return UploadOutcome.ALREADY_STORED

    if response.is_success:
        return UploadOutcome.STORED

    storage_error_code = parse_storage_error_code(response.text)
    storage_request_id = response.headers.get(STORAGE_REQUEST_ID_HEADER)

    if response.status_code == httpx.codes.BAD_REQUEST and storage_error_code == EXPIRED_UPLOAD_ERROR_CODE:
        raise UploadExpiredError(response.status_code, storage_error_code, storage_request_id)

    raise UploadError(response.status_code, storage_error_code, storage_request_id)


def parse_storage_error_code(body: str) -> str | None:
    """Parse the error code out of storage's XML error answer.

    Args:
        body: Storage's answer body.

    Returns:
        The ``<Code>`` element's text, such as ``EntityTooLarge``, or
        ``None`` when the body has none.
    """
    match = STORAGE_ERROR_CODE_PATTERN.search(body)
    return match.group(1) if match is not None else None
