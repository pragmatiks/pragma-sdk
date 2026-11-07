"""Reading what a built provider wheel declares, within bounded decompression."""

from __future__ import annotations

import base64
import collections
import contextlib
import errno
import hashlib
import importlib.metadata
import io
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, NoReturn

from pragma_sdk.exceptions import (
    WheelCentralDirectoryRefusedError,
    WheelExpansionLimitError,
    build_wheel_too_large_message,
)
from pragma_sdk.models.provider import ProviderVersionDeclaration
from pragma_sdk.provider_entry_point import PROVIDER_ENTRY_POINT_GROUP, parse_provider_package


if TYPE_CHECKING:
    import os
    from collections.abc import Generator


MAXIMUM_WHEEL_BYTES = 256 * 1024 * 1024
"""Largest size, in bytes, a wheel file or its decompressed members may reach on the platform."""

WHEEL_TOO_LARGE_MESSAGE = build_wheel_too_large_message(MAXIMUM_WHEEL_BYTES)
"""Message refusing a wheel that expands past :data:`MAXIMUM_WHEEL_BYTES`, worded as the platform words it."""

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

READ_CHUNK_SIZE = 65536
"""Largest number of decompressed bytes an archive member yields at once."""

WHEEL_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
"""Compression methods a wheel's members may use: the only ones whose decompression a read bounds per chunk."""

type EntryPointGroups = dict[str, dict[str, str]]
"""Entry point group names mapped to their entry names and object references."""


@dataclass(frozen=True)
class DistInfoFiles:
    """The ``.dist-info`` files a provider wheel is declared by.

    Attributes:
        directory: ``.dist-info`` directory name, such as ``postgresql-1.0.0.dist-info``.
        metadata: Contents of ``METADATA``.
        entry_points: Contents of ``entry_points.txt``, or ``None`` when the wheel has none.
    """

    directory: str
    metadata: bytes
    entry_points: bytes | None


@dataclass(frozen=True)
class ProviderEntryPoint:
    """The provider a wheel declares under ``pragma.provider``.

    Attributes:
        name: Provider short name, the entry point's key.
        package: Importable package holding the provider, the entry point's
            whole module path.
    """

    name: str
    package: str


class ExpansionBudget:
    """Running count of decompressed bytes against a limit, refusing bytes past it.

    One budget is shared by every read it is passed to, so the limit bounds
    their total.

    Attributes:
        limit_bytes: Decompressed bytes the budget allows.
        consumed_bytes: Decompressed bytes counted so far.
    """

    def __init__(self, limit_bytes: int) -> None:
        """Start the count at zero.

        Args:
            limit_bytes: Decompressed bytes the budget allows.
        """
        self.limit_bytes = limit_bytes
        self.consumed_bytes = 0

    def consume(self, byte_count: int, wheel_name: str, member_name: str) -> None:
        """Count a member's decompressed bytes against the budget, refusing them past its limit.

        Args:
            byte_count: Bytes just decompressed.
            wheel_name: File name of the wheel being read, for the error.
            member_name: Archive member the bytes belong to, for the error.

        Raises:
            WheelExpansionLimitError: When the bytes counted so far pass the
                limit, naming this wheel and member.
        """
        self.consumed_bytes += byte_count

        if self.consumed_bytes > self.limit_bytes:
            raise WheelExpansionLimitError(self.limit_bytes, wheel_name, member_name)


class EntryPointsDistribution(importlib.metadata.Distribution):
    """A distribution holding nothing but an ``entry_points.txt``, read the way an installed one is."""

    def __init__(self, entry_points_text: str) -> None:
        """Hold the ``entry_points.txt`` text.

        Args:
            entry_points_text: Decoded contents of ``entry_points.txt``.
        """
        self.entry_points_text = entry_points_text

    def read_text(self, filename: str) -> str | None:
        """Read one metadata file of the distribution.

        Args:
            filename: Metadata file name, such as ``entry_points.txt``.

        Returns:
            The ``entry_points.txt`` text, or ``None`` for any other file.
        """
        return self.entry_points_text if filename == "entry_points.txt" else None

    def locate_file(self, path: str | os.PathLike[str]) -> NoReturn:
        """Refuse to locate a file: the distribution has no installed files.

        Args:
            path: Path of a file relative to the distribution.

        Raises:
            FileNotFoundError: Always.
        """
        raise FileNotFoundError(path)


def read_wheel_declaration(wheel_path: Path | str, changelog: str | None = None) -> ProviderVersionDeclaration:
    """Read the declaration of a built provider wheel from disk.

    Only gathers what the wheel claims; the API judges whether the claims
    are acceptable. The ``.dist-info`` reads share one
    :data:`MAXIMUM_WHEEL_BYTES` budget.

    Args:
        wheel_path: Path to the built ``.whl``.
        changelog: Release notes to declare with the version.

    Returns:
        The wheel's filename, digest, size, ``.dist-info`` directory name,
        ``METADATA`` and ``entry_points.txt``.

    Raises:
        OSError: When the file cannot be opened, such as when it does not
            exist or is not readable, or reading or measuring it fails while
            hashing it.
        ValueError: When two of the wheel's members share a name, or it has
            no single top-level ``.dist-info`` directory or no ``METADATA``
            file in it.
        WheelExpansionLimitError: When ``METADATA`` and ``entry_points.txt``
            decompress past :data:`MAXIMUM_WHEEL_BYTES`.
        zipfile.BadZipFile: When the file is not a readable zip archive, its
            central directory is refused as :func:`open_wheel_archive`
            describes, or reading the archive or a member the declaration needs fails,
            whatever the cause: a damaged directory or header, a CRC mismatch,
            a corrupt or truncated compressed stream, an encrypted member, or
            a compression method other than stored or deflate. When reading
            the file failed with an ``OSError``, that ``OSError`` is its
            ``__cause__``; a read failure is never raised otherwise.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)

    with wheel_path.open("rb") as wheel_file:
        with open_wheel_archive(wheel_file, wheel_path.name) as archive:
            dist_info_files = read_dist_info_files(archive, wheel_path.name, ExpansionBudget(MAXIMUM_WHEEL_BYTES))

        wheel_file.seek(0)
        digest = hashlib.file_digest(wheel_file, "sha256")

    return ProviderVersionDeclaration.model_validate({
        "wheel_filename": wheel_path.name,
        "wheel_sha256": digest.hexdigest(),
        "wheel_size": wheel_path.stat().st_size,
        "dist_info_directory": dist_info_files.directory,
        "metadata": encode_base64(dist_info_files.metadata),
        "entry_points": encode_base64(dist_info_files.entry_points),
        "changelog": changelog,
    })


def encode_base64(content: bytes | None) -> str | None:
    """Encode file contents as base64 text, the way a declaration carries them.

    Args:
        content: The contents, or ``None`` when the file is absent.

    Returns:
        The standard base64 text, or ``None`` when ``content`` is ``None``.
    """
    if content is None:
        return None

    return base64.b64encode(content).decode("ascii")


def open_wheel_archive(wheel_file: BinaryIO, wheel_name: str) -> zipfile.ZipFile:
    """Open an already opened wheel file as a zip archive for reading, its central directory bounded.

    The central directory is measured from the end records before any
    member is listed, so a wheel whose central directory passes
    :data:`MAXIMUM_CENTRAL_DIRECTORY_BYTES` never takes memory in proportion
    to it.

    Args:
        wheel_file: The wheel file, opened in binary mode and seekable. The
            archive does not close it. When the wheel is refused the file is
            left at its start.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The archive, positioned to list and read its members.

    Raises:
        WheelCentralDirectoryRefusedError: When the central directory passes
            :data:`MAXIMUM_CENTRAL_DIRECTORY_BYTES`, the archive spans several
            disks, or it has a zip64 layout that releases of :mod:`zipfile`
            could read differently. A :class:`zipfile.BadZipFile`.
        zipfile.BadZipFile: When the file is not a readable zip archive; when
            reading the file failed with an ``OSError``, that ``OSError`` is
            its ``__cause__`` (see ``find_unreadable_archive_cause``).
    """  # noqa: DOC502
    unreadable_message = f"Wheel {wheel_name} is not a readable zip archive"

    try:
        require_bounded_central_directory(wheel_file, wheel_name)
    except OSError as error:
        raise zipfile.BadZipFile(f"{unreadable_message}: {error}") from find_unreadable_archive_cause(error)
    finally:
        wheel_file.seek(0)

    try:
        return zipfile.ZipFile(wheel_file)
    except Exception as error:
        raise zipfile.BadZipFile(f"{unreadable_message}: {error}") from find_unreadable_archive_cause(error)


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
    unreadable = zipfile.BadZipFile(f"Wheel {wheel_name} is not a readable zip archive")

    if file_size < END_RECORD.size:
        raise unreadable

    file.seek(file_size - END_RECORD.size)
    final_record = file.read(END_RECORD.size)

    if final_record.startswith(END_RECORD_SIGNATURE) and final_record.endswith(b"\x00\x00"):
        central_directory_size = END_RECORD.unpack(final_record)[END_RECORD_CENTRAL_DIRECTORY_SIZE_FIELD]
        return file_size - END_RECORD.size, central_directory_size

    search_start = max(file_size - MAXIMUM_COMMENT_BYTES - END_RECORD.size, 0)
    file.seek(search_start)
    tail = file.read(MAXIMUM_COMMENT_BYTES + END_RECORD.size)
    record_start = tail.rfind(END_RECORD_SIGNATURE)
    record = tail[record_start : record_start + END_RECORD.size]

    if record_start < 0 or len(record) != END_RECORD.size:
        raise unreadable

    central_directory_size = END_RECORD.unpack(record)[END_RECORD_CENTRAL_DIRECTORY_SIZE_FIELD]
    return search_start + record_start, central_directory_size


def read_zip64_central_directory_size(file: BinaryIO, end_record_offset: int, wheel_name: str) -> int | None:
    """Read the central directory size a zip archive's zip64 end record states, refusing a layout read two ways.

    The zip64 locator is read right before the end record, and the zip64
    end record right before the locator. Releases of :mod:`zipfile` differ
    in where they read the zip64 end record: older ones right before the
    locator, newer ones at the offset the locator states, with any extensible
    data. A zip64 layout on which they could disagree is refused: a locator
    whose offset is not that of the record right before it, or a central
    directory that does not fit between its stated offset and the end of the
    file.

    Args:
        file: Seekable binary file holding the archive.
        end_record_offset: Offset of the archive's end of central directory record.
        wheel_name: Filename of the wheel, for error messages.

    Returns:
        The size, or ``None`` when the archive has no zip64 locator or no
        zip64 end record before it.

    Raises:
        WheelCentralDirectoryRefusedError: When the zip64 locator names a disk other than
            the first or more than one disk, or does not point at the zip64
            end record right before it, or when the central directory does not
            fit between its offset and the end of the file.
    """
    locator_offset = end_record_offset - ZIP64_LOCATOR.size

    if locator_offset < 0:
        return None

    file.seek(locator_offset)
    signature, disk_number, stated_record_offset, disk_count = ZIP64_LOCATOR.unpack(file.read(ZIP64_LOCATOR.size))

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
    signature, *_, central_directory_size, central_directory_offset = ZIP64_END_RECORD.unpack(
        file.read(ZIP64_END_RECORD.size)
    )

    if signature != ZIP64_END_RECORD_SIGNATURE:
        return None

    file_size = file.seek(0, io.SEEK_END)

    if central_directory_offset + central_directory_size > file_size:
        raise WheelCentralDirectoryRefusedError(
            f"Wheel {wheel_name} has a zip64 central directory past the end of the file"
        )

    return central_directory_size


def find_unreadable_archive_cause(error: Exception) -> BaseException | None:
    """Find the cause to raise a wheel archive's unreadable error from.

    An ``OSError`` cause always means reading the file failed. An ``OSError``
    with ``errno.EINVAL`` is not a read failure: zipfile seeks before the
    file's start when a damaged archive's records point there.

    Args:
        error: The error reading the archive failed with, followed through
            its ``__cause__``, else its ``__context__``.

    Returns:
        The first ``OSError`` on that chain other than ``errno.EINVAL``; when
        it holds none, ``error`` itself, or ``None`` when ``error`` is an
        ``OSError``.
    """
    chained_error: BaseException | None = error

    while chained_error is not None:
        if isinstance(chained_error, OSError) and chained_error.errno != errno.EINVAL:
            return chained_error

        chained_error = chained_error.__cause__ or chained_error.__context__

    return None if isinstance(error, OSError) else error


def read_dist_info_files(archive: zipfile.ZipFile, wheel_name: str, budget: ExpansionBudget) -> DistInfoFiles:
    """Read the ``.dist-info`` files a provider wheel is declared by, against an expansion budget.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        budget: Budget the decompressed bytes count against.

    Returns:
        The ``.dist-info`` directory name with its ``METADATA`` and
        ``entry_points.txt`` contents.

    Raises:
        ValueError: When two of the wheel's members share a name, or it has
            no single top-level ``.dist-info`` directory or no ``METADATA``
            file in it.
        WheelExpansionLimitError: When the reads decompress past the budget.
        zipfile.BadZipFile: When a file cannot be read, as
            :func:`stream_archive_member` describes.
    """  # noqa: DOC502
    names = archive.namelist()
    require_unique_member_names(names, wheel_name)
    directory = derive_dist_info_directory(names, wheel_name)
    metadata_name = f"{directory}/METADATA"

    if metadata_name not in names:
        raise ValueError(f"Wheel {wheel_name} has no {metadata_name}")

    metadata = read_archive_member_within_budget(archive, wheel_name, metadata_name, budget)
    entry_points_name = f"{directory}/entry_points.txt"

    if entry_points_name not in names:
        return DistInfoFiles(directory=directory, metadata=metadata, entry_points=None)

    entry_points = read_archive_member_within_budget(archive, wheel_name, entry_points_name, budget)

    return DistInfoFiles(directory=directory, metadata=metadata, entry_points=entry_points)


def require_unique_member_names(names: list[str], wheel_name: str) -> None:
    """Refuse a wheel archive in which two members share a name.

    A zip archive may hold several members under one name, and readers
    disagree on which one they take, so such a wheel has no single content.

    Args:
        names: Every member name in the wheel archive.
        wheel_name: The wheel's file name, for error messages.

    Raises:
        ValueError: When a name appears more than once; the message names
            the first such name in sorted order.
    """
    repeated_names = sorted(name for name, count in collections.Counter(names).items() if count > 1)

    if repeated_names:
        raise ValueError(f"Wheel {wheel_name} has more than one member named {repeated_names[0]}")


def derive_dist_info_directory(names: list[str], wheel_name: str) -> str:
    """Derive the single top-level ``.dist-info`` directory of a wheel.

    Args:
        names: Every member name in the wheel archive.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The directory name without a trailing slash, such as
        ``postgresql-1.0.0.dist-info``.

    Raises:
        ValueError: When the wheel has no top-level ``.dist-info`` directory
            or more than one.
    """
    top_level_directories = {name.split("/", 1)[0] for name in names if "/" in name}
    directories = sorted(directory for directory in top_level_directories if directory.endswith(".dist-info"))

    if len(directories) != 1:
        raise ValueError(f"Wheel {wheel_name} has no single top-level .dist-info directory, found {directories}")

    return directories[0]


def read_archive_member_within_budget(
    archive: zipfile.ZipFile,
    wheel_name: str,
    member: zipfile.ZipInfo | str,
    budget: ExpansionBudget,
) -> bytes:
    """Read the whole decompressed content of one wheel archive member within a budget.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        member: The member, or its name, which must be in the archive.
        budget: Budget the decompressed bytes count against.

    Returns:
        The member's content.

    Raises:
        WheelExpansionLimitError: When the budget's total passes its limit.
        zipfile.BadZipFile: When the member cannot be opened or read, as
            :func:`stream_archive_member` describes.
    """  # noqa: DOC502
    return b"".join(stream_archive_member_within_budget(archive, wheel_name, member, budget))


def stream_archive_member_within_budget(
    archive: zipfile.ZipFile,
    wheel_name: str,
    member: zipfile.ZipInfo | str,
    budget: ExpansionBudget,
) -> Generator[bytes]:
    """Stream one member of a wheel archive, counting every chunk against a budget.

    A member that decompresses past the budget is refused before more than
    one chunk past the limit is decompressed.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        member: The member, or its name, which must be in the archive.
        budget: Budget the decompressed bytes count against.

    Yields:
        Consecutive chunks of the member until its end.

    Raises:
        WheelExpansionLimitError: When the budget's total passes its limit,
            naming this wheel and member.
        zipfile.BadZipFile: When the member cannot be opened or read, as
            :func:`stream_archive_member` describes.
    """  # noqa: DOC502
    member_name = derive_member_name(member)

    with contextlib.closing(stream_archive_member(archive, wheel_name, member)) as chunks:
        for chunk in chunks:
            budget.consume(len(chunk), wheel_name, member_name)
            yield chunk


def stream_archive_member(
    archive: zipfile.ZipFile,
    wheel_name: str,
    member: zipfile.ZipInfo | str,
) -> Generator[bytes]:
    """Stream the decompressed content of one member of a wheel archive.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        member: The member, or its name, which must be in the archive.

    Yields:
        Consecutive chunks of at most :data:`READ_CHUNK_SIZE` bytes until the
        member's end.

    Raises:
        zipfile.BadZipFile: When the member cannot be opened or read, such as
            for a damaged header, a CRC mismatch, a corrupt or truncated
            compressed stream, or encryption, or uses a compression method
            other than stored or deflate, which is refused before anything
            is decompressed; when reading the file failed with an
            ``OSError``, that ``OSError`` is its ``__cause__``.
    """
    unreadable_message = f"Wheel {wheel_name} has an unreadable {derive_member_name(member)}"

    try:
        member_info = member if isinstance(member, zipfile.ZipInfo) else archive.getinfo(member)
    except KeyError as error:
        raise zipfile.BadZipFile(f"{unreadable_message}: {error}") from error

    if member_info.compress_type not in WHEEL_COMPRESSION_METHODS:
        raise zipfile.BadZipFile(
            f"{unreadable_message}: compression method {member_info.compress_type} is not stored or deflate"
        )

    try:
        stream = archive.open(member_info)
    except Exception as error:
        raise zipfile.BadZipFile(f"{unreadable_message}: {error}") from find_unreadable_archive_cause(error)

    with stream:
        while True:
            try:
                chunk = stream.read(READ_CHUNK_SIZE)
            except Exception as error:
                raise zipfile.BadZipFile(f"{unreadable_message}: {error}") from find_unreadable_archive_cause(error)

            if not chunk:
                return

            yield chunk


def derive_member_name(member: zipfile.ZipInfo | str) -> str:
    """Derive the name of a wheel archive member.

    Args:
        member: The member, or its name.

    Returns:
        The member's name in the archive.
    """
    return member.filename if isinstance(member, zipfile.ZipInfo) else member


def parse_entry_points(entry_points: bytes | None, wheel_name: str) -> EntryPointGroups:
    """Parse a wheel's ``entry_points.txt`` the way :mod:`importlib.metadata` reads it once installed.

    Entry names keep their case, a group may be split over several sections,
    and lines before the first section are ignored. An entry name declared
    twice in one group is refused.

    Args:
        entry_points: Contents of ``entry_points.txt``, or ``None`` when the
            wheel has none.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        Every declared group with its entries; empty when the wheel has no file.

    Raises:
        ValueError: When the file is not UTF-8, has an entry line without
            ``=`` or an entry whose object reference is not a module, optionally
            followed by a colon and an object, or declares an entry name twice
            in one group;
            the message names the wheel, the same on every Python.
    """
    if entry_points is None:
        return {}

    try:
        entry_points_text = entry_points.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Wheel {wheel_name} has an entry_points.txt that is not UTF-8: {error}") from error

    try:
        declared = tuple(EntryPointsDistribution(entry_points_text).entry_points)
    except TypeError as error:
        raise ValueError(
            f"Wheel {wheel_name} has an entry_points.txt entry line without '='; write each entry as 'name = reference'"
        ) from error
    except ValueError as error:
        raise ValueError(build_invalid_entry_point_reference_message(wheel_name)) from error

    groups: EntryPointGroups = {}

    for entry_point in declared:
        if entry_point.pattern.match(entry_point.value) is None:
            raise ValueError(build_invalid_entry_point_reference_message(wheel_name))

        group = groups.setdefault(entry_point.group, {})

        if entry_point.name in group:
            raise ValueError(
                f"Wheel {wheel_name} has an entry_points.txt declaring {entry_point.name!r} "
                f"twice in {entry_point.group!r}"
            )

        group[entry_point.name] = entry_point.value

    return groups


def build_invalid_entry_point_reference_message(wheel_name: str) -> str:
    """Build the message refusing an ``entry_points.txt`` entry whose object reference is invalid.

    Args:
        wheel_name: The wheel's file name.

    Returns:
        A sentence naming the wheel, the same on every Python.
    """
    return f"Wheel {wheel_name} has an entry_points.txt entry whose reference is not 'module' or 'module:object'"


def find_provider_entry_point(entry_points: EntryPointGroups, wheel_name: str) -> ProviderEntryPoint | None:
    """Find the provider a wheel declares under ``pragma.provider``.

    Args:
        entry_points: Parsed entry point groups of the wheel.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The provider short name and its importable package, as
        :func:`~pragma_sdk.provider_entry_point.parse_provider_package`
        parses it, or ``None`` unless the group holds exactly one entry point.

    Raises:
        ValueError: When the entry point does not name a package, as
            :func:`parse_wheel_provider_package` describes.
    """  # noqa: DOC502
    provider_entry_points = entry_points.get(PROVIDER_ENTRY_POINT_GROUP, {})

    if len(provider_entry_points) != 1:
        return None

    name, reference = next(iter(provider_entry_points.items()))
    package = parse_wheel_provider_package(name, reference, wheel_name)

    return ProviderEntryPoint(name=name, package=package)


def parse_wheel_provider_package(name: str, reference: str, wheel_name: str) -> str:
    """Parse the package a wheel's ``pragma.provider`` entry point names.

    Args:
        name: The entry point's name.
        reference: The entry point's object reference.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The package, as
        :func:`~pragma_sdk.provider_entry_point.parse_provider_package`
        parses it.

    Raises:
        ValueError: When the entry point does not name a package; the
            message names the wheel.
    """
    try:
        return parse_provider_package(name, reference)
    except ValueError as error:
        raise ValueError(
            f"Wheel {wheel_name} has an invalid {PROVIDER_ENTRY_POINT_GROUP!r} entry point. {error}"
        ) from error
