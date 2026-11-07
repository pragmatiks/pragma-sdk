"""Bounded reading of a provider wheel archive, and the declaration read of a built wheel."""

from __future__ import annotations

import base64
import collections
import contextlib
import errno
import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from pragma_sdk.exceptions import (
    WheelExpansionLimitError,
    build_wheel_too_large_message,
)
from pragma_sdk.models.provider import ProviderVersionDeclaration
from pragma_sdk.wheel_central_directory import require_bounded_central_directory


if TYPE_CHECKING:
    from collections.abc import Generator


MAXIMUM_WHEEL_BYTES = 256 * 1024 * 1024
"""Largest size, in bytes, a wheel file or its decompressed members may reach on the platform."""

WHEEL_TOO_LARGE_MESSAGE = build_wheel_too_large_message(MAXIMUM_WHEEL_BYTES)
"""Message refusing a wheel that expands past :data:`MAXIMUM_WHEEL_BYTES`, worded as the platform words it."""

READ_CHUNK_SIZE = 65536
"""Largest number of decompressed bytes an archive member yields at once."""

WHEEL_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
"""Compression methods a wheel's members may use: the only ones whose decompression a read bounds per chunk."""


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
    :data:`~pragma_sdk.wheel_central_directory.MAXIMUM_CENTRAL_DIRECTORY_BYTES`
    never takes memory in proportion to it.

    Args:
        wheel_file: The wheel file, opened in binary mode and seekable. The
            archive does not close it. When the wheel is refused the file is
            left at its start.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The archive, positioned to list and read its members.

    Raises:
        WheelCentralDirectoryRefusedError: When the central directory passes
            :data:`~pragma_sdk.wheel_central_directory.MAXIMUM_CENTRAL_DIRECTORY_BYTES`,
            the archive spans several disks, or it has a zip64 layout that
            releases of :mod:`zipfile` could read differently. A :class:`zipfile.BadZipFile`.
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
