"""Bounding a wheel's zip central directory from its end records, before any member is listed."""

from __future__ import annotations

import io
import struct
import zipfile
from typing import BinaryIO

from pragma_sdk.exceptions import WheelCentralDirectoryRefusedError


MAXIMUM_CENTRAL_DIRECTORY_BYTES = 1024 * 1024
"""Largest zip central directory a wheel may have; bounds the members listed and the memory they take.

Listing reads members until the central directory's bytes run out,
whatever member count the end record states, and each member takes at
least 46 of those bytes, so one listing holds at most about 22,800 members.
"""

END_RECORD = struct.Struct("<4s4H2LH")
"""Layout of a zip end of central directory record, before its comment."""

END_RECORD_SIGNATURE = b"PK\x05\x06"
"""Signature opening a zip end of central directory record."""

END_RECORD_CENTRAL_DIRECTORY_SIZE_FIELD = 5
"""Position of the central directory's size among the fields of :data:`END_RECORD`."""

MAXIMUM_COMMENT_BYTES = 0xFFFF
"""Longest comment that may follow a zip end of central directory record."""

ZIP64_LOCATOR = struct.Struct("<4sLQL")
"""Layout of a zip64 end of central directory locator, which sits right before the end record."""

ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
"""Signature opening a zip64 end of central directory locator."""

ZIP64_END_RECORD = struct.Struct("<4sQ2H2L4Q")
"""Layout of a zip64 end of central directory record, without extensible data."""

ZIP64_END_RECORD_SIGNATURE = b"PK\x06\x06"
"""Signature opening a zip64 end of central directory record."""


def require_bounded_central_directory(file: BinaryIO, wheel_name: str) -> None:
    """Refuse a wheel archive whose central directory is too large to list, before any member is listed.

    Args:
        file: Seekable binary file holding the wheel.
        wheel_name: Filename of the wheel, for error messages.

    Raises:
        WheelCentralDirectoryRefusedError: When the file spans several disks,
            has a zip64 layout that releases of :mod:`zipfile` could read
            differently, or its central directory passes
            :data:`MAXIMUM_CENTRAL_DIRECTORY_BYTES`.
        zipfile.BadZipFile: When the file has no zip end record.
    """  # noqa: DOC502
    central_directory_bytes = read_central_directory_size(file, wheel_name)

    if central_directory_bytes > MAXIMUM_CENTRAL_DIRECTORY_BYTES:
        raise WheelCentralDirectoryRefusedError(
            f"Wheel {wheel_name} has a {central_directory_bytes}-byte central directory, "
            f"past {MAXIMUM_CENTRAL_DIRECTORY_BYTES} bytes"
        )


def read_central_directory_size(file: BinaryIO, wheel_name: str) -> int:
    """Read the central directory size a zip archive states, as :class:`zipfile.ZipFile` reads it when listing.

    The zip64 end record's size, when the archive has one right before its
    zip64 locator, takes the place of the end record's.

    Args:
        file: Seekable binary file holding the archive.
        wheel_name: Filename of the wheel, for error messages.

    Returns:
        The central directory's size in bytes.

    Raises:
        WheelCentralDirectoryRefusedError: When the file spans several
            disks, or has a zip64 layout that releases of :mod:`zipfile`
            could read differently.
        zipfile.BadZipFile: When the file has no zip end record.
    """  # noqa: DOC502
    end_record_offset, end_record_central_directory_size = read_end_record(file, wheel_name)
    zip64_central_directory_size = read_zip64_central_directory_size(file, end_record_offset, wheel_name)

    if zip64_central_directory_size is None:
        return end_record_central_directory_size

    return zip64_central_directory_size


def read_end_record(file: BinaryIO, wheel_name: str) -> tuple[int, int]:
    """Read a zip archive's end of central directory record, found as :class:`zipfile.ZipFile` finds it.

    A record closing the file without a comment is taken first; otherwise
    the last record signature within the file's final
    :data:`MAXIMUM_COMMENT_BYTES` plus one record is.

    Args:
        file: Seekable binary file holding the archive.
        wheel_name: Filename of the wheel, for error messages.

    Returns:
        The record's offset in the file, and the central directory size it states.

    Raises:
        zipfile.BadZipFile: When the file holds no whole end record.
    """  # noqa: DOC502
    file_size = file.seek(0, io.SEEK_END)

    if file_size < END_RECORD.size:
        raise zipfile.BadZipFile(f"Wheel {wheel_name} is not a readable zip archive")

    file.seek(file_size - END_RECORD.size)
    final_record = file.read(END_RECORD.size)

    if final_record.startswith(END_RECORD_SIGNATURE) and final_record.endswith(b"\x00\x00"):
        return file_size - END_RECORD.size, parse_end_record_central_directory_size(final_record)

    search_start = max(file_size - MAXIMUM_COMMENT_BYTES - END_RECORD.size, 0)
    file.seek(search_start)
    tail = file.read(MAXIMUM_COMMENT_BYTES + END_RECORD.size)
    record_start = tail.rfind(END_RECORD_SIGNATURE)
    record = tail[record_start : record_start + END_RECORD.size]

    if record_start < 0 or len(record) != END_RECORD.size:
        raise zipfile.BadZipFile(f"Wheel {wheel_name} is not a readable zip archive")

    return search_start + record_start, parse_end_record_central_directory_size(record)


def parse_end_record_central_directory_size(record: bytes) -> int:
    """Parse the central directory size a zip end of central directory record states.

    Args:
        record: The record's :data:`END_RECORD` bytes, without its comment.

    Returns:
        The central directory's size in bytes.
    """
    return END_RECORD.unpack(record)[END_RECORD_CENTRAL_DIRECTORY_SIZE_FIELD]


def read_zip64_central_directory_size(file: BinaryIO, end_record_offset: int, wheel_name: str) -> int | None:
    """Read the central directory size a zip archive's zip64 end record states, refusing an ambiguous zip64 layout.

    The zip64 locator is read right before the end record, and the zip64 end record right before the locator.

    Args:
        file: Seekable binary file holding the archive.
        end_record_offset: Offset of the archive's end of central directory record.
        wheel_name: Filename of the wheel, for error messages.

    Returns:
        The size, or ``None`` when the archive has no zip64 locator or no zip64 end record before it.

    Raises:
        WheelCentralDirectoryRefusedError: When the zip64 locator names a disk other than the first or more than
            one disk, or does not point at the zip64 end record right before it, or when the central directory does
            not fit between its offset and the end of the file.

    Note:
        Older releases of :mod:`zipfile` read the zip64 end record right before the locator, newer ones at the
        offset the locator states, with any extensible data; the refused layouts are those they could read
        differently.
    """
    locator_offset = end_record_offset - ZIP64_LOCATOR.size

    if locator_offset < 0:
        return None

    file.seek(locator_offset)
    locator = file.read(ZIP64_LOCATOR.size)
    signature, disk_number, stated_record_offset, disk_count = ZIP64_LOCATOR.unpack(locator)

    if signature != ZIP64_LOCATOR_SIGNATURE:
        return None

    if disk_number != 0 or disk_count > 1:
        raise WheelCentralDirectoryRefusedError(f"Wheel {wheel_name} spans several disks")

    record_offset = locator_offset - ZIP64_END_RECORD.size

    if stated_record_offset != record_offset:
        raise WheelCentralDirectoryRefusedError(
            f"Wheel {wheel_name} has a zip64 locator that does not point at the zip64 end record before it"
        )

    file.seek(record_offset)
    zip64_end_record = file.read(ZIP64_END_RECORD.size)
    signature, *_, central_directory_size, central_directory_offset = ZIP64_END_RECORD.unpack(zip64_end_record)

    if signature != ZIP64_END_RECORD_SIGNATURE:
        return None

    file_size = file.seek(0, io.SEEK_END)

    if central_directory_offset + central_directory_size > file_size:
        raise WheelCentralDirectoryRefusedError(
            f"Wheel {wheel_name} has a zip64 central directory past the end of the file"
        )

    return central_directory_size
