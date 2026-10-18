"""Uploading a declared provider wheel straight to its signed storage address.

The upload contract assumes the platform's storage answers a refusal with an
XML error body carrying a ``<Code>`` element, names an expired address with
the ``ExpiredToken`` code, and identifies the upload request in the
``x-guploader-uploadid`` header; only :func:`parse_wheel_upload_outcome`
reads those.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from typing import IO, TYPE_CHECKING

import httpx

from pragma_sdk.blocking import run_in_worker_thread
from pragma_sdk.exceptions import WheelUploadError, WheelUploadExpiredError
from pragma_sdk.models import WheelUploadAddress, WheelUploadOutcome


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


WHEEL_UPLOAD_TIMEOUT_SECONDS = 60.0
"""Longest a wheel upload waits on any one connect, write or read, in seconds."""

UPLOAD_CHUNK_SIZE = 64 * 1024
"""Bytes of the wheel each write of the async upload sends; the write timeout bounds each such write.

The sync upload does not read it.
"""

EXPIRED_UPLOAD_ERROR_CODE = "ExpiredToken"
"""Error code storage answers with, in a 400, to an upload whose address expired."""

STORAGE_ERROR_CODE_PATTERN = re.compile(r"<Code>([^<]*)</Code>")
"""The ``<Code>`` element of storage's XML error answer, capturing the error code."""

STORAGE_REQUEST_ID_HEADER = "x-guploader-uploadid"
"""Response header carrying storage's identifier of an upload request."""


def upload_wheel(
    transport: httpx.BaseTransport,
    wheel_upload: WheelUploadAddress,
    wheel_path: Path | str,
) -> WheelUploadOutcome:
    """Upload a declared wheel to its address through a transport.

    Args:
        transport: Transport the upload is sent through.
        wheel_upload: The declared version's ``wheel_upload``.
        wheel_path: Path to the declared ``.whl``, streamed from disk.

    Returns:
        Whether this upload stored the wheel or found it already stored.

    Raises:
        OSError: If the wheel cannot be read.
        WheelUploadExpiredError: As :func:`parse_wheel_upload_outcome` describes.
        WheelUploadError: As :func:`parse_wheel_upload_outcome` describes.
        httpx.TransportError: As :func:`send_wheel_upload` describes.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)
    wheel_size = wheel_path.stat().st_size

    with wheel_path.open("rb") as wheel_file:
        request = build_wheel_upload_request(wheel_upload, wheel_file, wheel_size)
        response = send_wheel_upload(transport, request)

    return parse_wheel_upload_outcome(response)


async def upload_wheel_async(
    transport: httpx.AsyncBaseTransport,
    wheel_upload: WheelUploadAddress,
    wheel_path: Path | str,
) -> WheelUploadOutcome:
    """Async mirror of :func:`upload_wheel`.

    Args:
        transport: Transport the upload is sent through.
        wheel_upload: The declared version's ``wheel_upload``.
        wheel_path: Path to the declared ``.whl``, streamed from disk.

    Returns:
        Whether this upload stored the wheel or found it already stored.

    Raises:
        OSError: If the wheel cannot be read.
        WheelUploadExpiredError: As :func:`parse_wheel_upload_outcome` describes.
        WheelUploadError: As :func:`parse_wheel_upload_outcome` describes.
        httpx.TransportError: As :func:`send_wheel_upload` describes.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)
    wheel_size = (await run_in_worker_thread(wheel_path.stat)).st_size

    async with contextlib.aclosing(read_file_chunks(wheel_path)) as chunks:
        request = build_wheel_upload_request(wheel_upload, chunks, wheel_size)
        response = await send_wheel_upload_async(transport, request)

    return parse_wheel_upload_outcome(response)


async def read_file_chunks(path: Path) -> AsyncGenerator[bytes]:
    """Stream a file from disk in chunks, opening, reading and closing it off the event loop.

    The file is closed even when the caller is cancelled while it opens.

    Args:
        path: File to read.

    Yields:
        Consecutive chunks of at most :data:`UPLOAD_CHUNK_SIZE` bytes until
        the file's end.

    Raises:
        OSError: If the file cannot be opened or read.
    """  # noqa: DOC502
    opened_files: list[io.FileIO] = []

    try:
        file = await run_in_worker_thread(open_recorded_file, path, opened_files)

        while chunk := await run_in_worker_thread(file.read, UPLOAD_CHUNK_SIZE):
            yield chunk
    finally:
        for opened_file in opened_files:
            await run_in_worker_thread(opened_file.close)


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


def build_wheel_upload_request(
    wheel_upload: WheelUploadAddress,
    content: IO[bytes] | AsyncGenerator[bytes],
    wheel_size: int,
) -> httpx.Request:
    """Build the ``PUT`` that uploads a wheel to its address.

    Args:
        wheel_upload: The address the wheel is uploaded to.
        content: The wheel's bytes, streamed as the body.
        wheel_size: The wheel's size in bytes, the declared size.

    Returns:
        A request carrying the address's signed headers unchanged plus
        ``Content-Length``, timed out after :data:`WHEEL_UPLOAD_TIMEOUT_SECONDS`
        on any one connect, write or read.
    """
    return httpx.Request(
        "PUT",
        wheel_upload.url.get_secret_value(),
        content=content,
        headers=build_wheel_upload_headers(wheel_upload, wheel_size),
        extensions={"timeout": httpx.Timeout(WHEEL_UPLOAD_TIMEOUT_SECONDS).as_dict()},
    )


def build_wheel_upload_headers(wheel_upload: WheelUploadAddress, wheel_size: int) -> httpx.Headers:
    """Build the headers a wheel upload sends.

    Args:
        wheel_upload: The address the wheel is uploaded to.
        wheel_size: The wheel's size in bytes, the declared size.

    Returns:
        The address's signed headers unchanged, with a single
        ``Content-Length`` of ``wheel_size`` replacing one they carry in
        any case.
    """
    headers = httpx.Headers(wheel_upload.headers)
    headers["Content-Length"] = str(wheel_size)
    return headers


def send_wheel_upload(transport: httpx.BaseTransport, request: httpx.Request) -> httpx.Response:
    """Send a wheel upload through a transport and read storage's whole answer.

    Sending through the transport, not a client, keeps the signed address
    out of the client's request log.

    Args:
        transport: Transport the upload is sent through.
        request: The upload, as :func:`build_wheel_upload_request` builds it.

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


async def send_wheel_upload_async(transport: httpx.AsyncBaseTransport, request: httpx.Request) -> httpx.Response:
    """Async mirror of :func:`send_wheel_upload`.

    Args:
        transport: Transport the upload is sent through.
        request: The upload, as :func:`build_wheel_upload_request` builds it.

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


def parse_wheel_upload_outcome(response: httpx.Response) -> WheelUploadOutcome:
    """Parse how storage took a wheel upload from its answer, raising when the wheel is left unstored.

    A 2xx means this upload stored the wheel; a 412 means the wheel was
    already stored under the address, which counts as stored too.

    Args:
        response: Storage's answer to the upload, read.

    Returns:
        ``already_stored`` for a 412, ``stored`` for a 2xx.

    Raises:
        WheelUploadExpiredError: If storage answers 400 with the
            ``ExpiredToken`` error code.
        WheelUploadError: For every other status; it carries the status,
            storage's error code and request id, never the address.
    """
    if response.status_code == httpx.codes.PRECONDITION_FAILED:
        return WheelUploadOutcome.ALREADY_STORED

    if response.is_success:
        return WheelUploadOutcome.STORED

    storage_error_code = parse_storage_error_code(response.text)
    storage_request_id = response.headers.get(STORAGE_REQUEST_ID_HEADER)

    if response.status_code == httpx.codes.BAD_REQUEST and storage_error_code == EXPIRED_UPLOAD_ERROR_CODE:
        raise WheelUploadExpiredError(response.status_code, storage_error_code, storage_request_id)

    raise WheelUploadError(response.status_code, storage_error_code, storage_request_id)


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
