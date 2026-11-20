"""Uploading a declared provider wheel straight to its signed storage address."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pragma_sdk.blocking import run_in_worker_thread
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
    import httpx

    from pragma_sdk.models import UploadAddress, UploadOutcome


def upload_wheel(
    transport: httpx.BaseTransport,
    wheel_upload: UploadAddress,
    wheel_path: Path | str,
) -> UploadOutcome:
    """Upload a declared wheel to its address through a transport.

    Args:
        transport: Transport the upload is sent through.
        wheel_upload: The declared version's ``wheel_upload``.
        wheel_path: Path to the declared ``.whl``, streamed from disk; its
            size when opened is the size sent.

    Returns:
        Whether this upload stored the wheel or found it already stored.

    Raises:
        OSError: If the wheel cannot be read.
        EOFError: If the wheel shrinks while it is sent.
        UploadExpiredError: As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` describes.
        UploadError: As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` describes.
        httpx.TransportError: As :func:`~pragma_sdk.storage_upload.send_upload` describes.
    """  # noqa: DOC502
    with Path(wheel_path).open("rb") as wheel_file:
        wheel_size = os.fstat(wheel_file.fileno()).st_size
        request = build_upload_request(wheel_upload, read_file_chunks(wheel_file, wheel_size), wheel_size)
        response = send_upload(transport, request)

    return parse_upload_outcome(response)


async def upload_wheel_async(
    transport: httpx.AsyncBaseTransport,
    wheel_upload: UploadAddress,
    wheel_path: Path | str,
) -> UploadOutcome:
    """Async mirror of :func:`upload_wheel`, the wheel opened, read and closed off the event loop.

    The wheel is closed even when the caller is cancelled while it opens.

    Args:
        transport: Transport the upload is sent through.
        wheel_upload: The declared version's ``wheel_upload``.
        wheel_path: Path to the declared ``.whl``, streamed from disk; its
            size when opened is the size sent.

    Returns:
        Whether this upload stored the wheel or found it already stored.

    Raises:
        OSError: If the wheel cannot be read.
        EOFError: If the wheel shrinks while it is sent.
        UploadExpiredError: As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` describes.
        UploadError: As :func:`~pragma_sdk.storage_upload.parse_upload_outcome` describes.
        httpx.TransportError: As :func:`~pragma_sdk.storage_upload.send_upload` describes.
    """  # noqa: DOC502
    opened_files: list[io.FileIO] = []

    try:
        wheel_file = await run_in_worker_thread(open_recorded_file, Path(wheel_path), opened_files)
        wheel_size = (await run_in_worker_thread(os.fstat, wheel_file.fileno())).st_size

        async with contextlib.aclosing(read_file_chunks_async(wheel_file, wheel_size)) as chunks:
            request = build_upload_request(wheel_upload, chunks, wheel_size)
            response = await send_upload_async(transport, request)
    finally:
        await close_recorded_files(opened_files)

    return parse_upload_outcome(response)
