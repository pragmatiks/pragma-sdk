"""Reading the declaration of a built provider wheel from its archive."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path
from typing import BinaryIO

from pragma_sdk.models.provider import ProviderVersionDeclaration


def read_wheel_declaration(wheel_path: Path | str, changelog: str | None = None) -> ProviderVersionDeclaration:
    """Read the declaration of a built provider wheel from disk.

    Only gathers what the wheel claims; the API judges whether the claims
    are acceptable.

    Args:
        wheel_path: Path to the built ``.whl``.
        changelog: Release notes to declare with the version.

    Returns:
        The wheel's filename, digest, size, ``.dist-info`` directory name,
        ``METADATA`` and ``entry_points.txt``.

    Raises:
        OSError: When the file cannot be opened, such as when it does not
            exist or is not readable.
        ValueError: When the wheel has no single top-level ``.dist-info``
            directory or no ``METADATA`` file in it.
        zipfile.BadZipFile: When the file is not a readable zip archive, or
            reading the archive or a member the declaration needs fails,
            whatever the cause: a damaged directory or header, a CRC mismatch,
            a corrupt or truncated compressed stream, an encrypted member, or
            a compression method this Python cannot decompress.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)

    with wheel_path.open("rb") as wheel_file:
        with open_wheel_archive(wheel_file, wheel_path.name) as archive:
            names = archive.namelist()
            dist_info_directory = derive_dist_info_directory(names, wheel_path.name)
            metadata_name = f"{dist_info_directory}/METADATA"

            if metadata_name not in names:
                raise ValueError(f"Wheel {wheel_path.name} has no {metadata_name}")

            metadata = read_archive_member(archive, wheel_path.name, metadata_name)
            entry_points_name = f"{dist_info_directory}/entry_points.txt"
            entry_points = (
                read_archive_member(archive, wheel_path.name, entry_points_name) if entry_points_name in names else None
            )

        wheel_file.seek(0)
        digest = hashlib.file_digest(wheel_file, "sha256")

    return ProviderVersionDeclaration.model_validate({
        "wheel_filename": wheel_path.name,
        "wheel_sha256": digest.hexdigest(),
        "wheel_size": wheel_path.stat().st_size,
        "dist_info_directory": dist_info_directory,
        "metadata": base64.b64encode(metadata).decode("ascii"),
        "entry_points": base64.b64encode(entry_points).decode("ascii") if entry_points is not None else None,
        "changelog": changelog,
    })


def open_wheel_archive(wheel_file: BinaryIO, wheel_name: str) -> zipfile.ZipFile:
    """Open an already opened wheel file as a zip archive for reading.

    Args:
        wheel_file: The wheel file, opened in binary mode and seekable. The
            archive does not close it.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The archive, positioned to list and read its members.

    Raises:
        zipfile.BadZipFile: When the file is not a readable zip archive.
    """
    try:
        return zipfile.ZipFile(wheel_file)
    except Exception as error:
        raise zipfile.BadZipFile(f"Wheel {wheel_name} is not a readable zip archive: {error}") from error


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


def read_archive_member(archive: zipfile.ZipFile, wheel_name: str, name: str) -> bytes:
    """Read the uncompressed content of one member of a wheel archive.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        name: The member's name, which must be in the archive.

    Returns:
        The member's content.

    Raises:
        zipfile.BadZipFile: When the member cannot be read, such as for a
            damaged header, a CRC mismatch, a corrupt or truncated compressed
            stream, encryption, or a compression method this Python cannot
            decompress.
    """
    try:
        return archive.read(name)
    except Exception as error:
        raise zipfile.BadZipFile(f"Wheel {wheel_name} has an unreadable {name}: {error}") from error
