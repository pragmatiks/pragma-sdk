"""Reading the declaration of a built provider wheel from its archive."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

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
        ValueError: When the wheel has no single top-level ``.dist-info``
            directory or no ``METADATA`` file in it.
        zipfile.BadZipFile: When the file is not a zip archive.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)

    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        dist_info_directory = derive_dist_info_directory(names)
        metadata_name = f"{dist_info_directory}/METADATA"

        if metadata_name not in names:
            raise ValueError(f"Wheel {wheel_path.name} has no {metadata_name}")

        metadata = archive.read(metadata_name)
        entry_points_name = f"{dist_info_directory}/entry_points.txt"
        entry_points = archive.read(entry_points_name) if entry_points_name in names else None

    with wheel_path.open("rb") as wheel_file:
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


def derive_dist_info_directory(names: list[str]) -> str:
    """Derive the single top-level ``.dist-info`` directory of a wheel.

    Args:
        names: Every member name in the wheel archive.

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
        raise ValueError(f"Expected exactly one .dist-info directory in the wheel, found {directories}")

    return directories[0]
