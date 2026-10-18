"""The platform's naming contract for a provider distribution and the wheels published under it."""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class WheelNames:
    """The names that say which distribution and version a wheel is.

    Attributes:
        filename: The wheel's filename, such as
            ``fubar_postgresql-1.0.0-py3-none-any.whl``.
        dist_info_directory: Its ``.dist-info`` directory name, such as
            ``fubar_postgresql-1.0.0.dist-info``.
        metadata_name: The ``Name:`` its ``METADATA`` carries, such as
            ``fubar-postgresql``.
    """

    filename: str
    dist_info_directory: str
    metadata_name: str


@dataclass(frozen=True)
class WheelFilenameFields:
    """What a wheel filename says about its wheel.

    Attributes:
        version: The version the filename names.
        tags: The optional build tag and the compatibility tags, as written,
            such as ``py3-none-any``.
    """

    version: Version
    tags: str


def build_distribution_name(prefix: str, name: str) -> str:
    """Build the distribution name a provider is published under.

    Args:
        prefix: Provider prefix, the publishing organization's slug.
        name: Provider short name, the key of its ``pragma.provider`` entry point.

    Returns:
        ``prefix-name`` canonicalized per PEP 503, such as ``fubar-postgresql``.
    """
    return str(canonicalize_name(f"{prefix}-{name}"))


def build_wheel_names(distribution_name: str, version: str, tags: str) -> WheelNames:
    """Build the names a wheel published under a distribution name carries.

    Args:
        distribution_name: PEP 503 canonical distribution name, such as ``fubar-postgresql``.
        version: Normalized version of the wheel.
        tags: Optional build tag and the compatibility tags, such as ``py3-none-any``.

    Returns:
        The filename, such as ``fubar_postgresql-1.0.0-py3-none-any.whl``,
        the ``.dist-info`` directory name, such as
        ``fubar_postgresql-1.0.0.dist-info``, and ``distribution_name`` as the
        ``METADATA`` ``Name:``.
    """
    stem = f"{escape_distribution_name(distribution_name)}-{version}"
    return WheelNames(
        filename=f"{stem}-{tags}.whl",
        dist_info_directory=f"{stem}.dist-info",
        metadata_name=distribution_name,
    )


def escape_distribution_name(distribution_name: str) -> str:
    """Escape a canonical distribution name for a wheel filename or ``.dist-info`` directory.

    Args:
        distribution_name: PEP 503 canonical distribution name, such as ``fubar-postgresql``.

    Returns:
        The name with every run of non-alphanumeric characters replaced by
        ``_``, such as ``fubar_postgresql``.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", distribution_name)


def parse_wheel_filename_fields(wheel_name: str) -> WheelFilenameFields:
    """Parse the version and the tags out of a wheel filename.

    Args:
        wheel_name: The wheel's file name.

    Returns:
        The version, and the tags as written, without the ``.whl``
        extension.

    Raises:
        ValueError: When the name is not a valid wheel filename.
    """  # noqa: DOC502
    _, version, _, _ = parse_wheel_filename(wheel_name)
    _, _, tags = wheel_name.removesuffix(".whl").split("-", 2)
    return WheelFilenameFields(version=version, tags=tags)


def require_versions_agree(
    filename_version: Version,
    dist_info_directory: str,
    metadata_version: Version,
    wheel_name: str,
) -> None:
    """Refuse a wheel whose filename or ``.dist-info`` directory names another version than its ``METADATA``.

    Args:
        filename_version: The version the wheel's filename names.
        dist_info_directory: The wheel's ``.dist-info`` directory name.
        metadata_version: The version its ``METADATA`` carries.
        wheel_name: The wheel's file name, for error messages.

    Raises:
        ValueError: When the directory name is not a ``.dist-info`` name with
            a valid version, or either version differs from
            ``metadata_version``.
    """
    directory_version = parse_dist_info_version(dist_info_directory, wheel_name)

    if filename_version != metadata_version or directory_version != metadata_version:
        raise ValueError(
            f"Wheel {wheel_name} names version {filename_version} in its filename and {directory_version} in "
            f"{dist_info_directory}, but its METADATA carries {metadata_version}"
        )


def parse_dist_info_version(dist_info_directory: str, wheel_name: str) -> Version:
    """Parse the version a wheel's ``.dist-info`` directory name carries.

    Args:
        dist_info_directory: The wheel's ``.dist-info`` directory name, such
            as ``fubar_postgresql-1.0.0.dist-info``.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The version after the directory name's last ``-``.

    Raises:
        ValueError: When the name does not end in ``.dist-info`` or carries
            no valid version.
    """
    if not dist_info_directory.endswith(".dist-info"):
        raise ValueError(f"Wheel {wheel_name} has {dist_info_directory}, which is not a .dist-info directory")

    _, _, version_text = dist_info_directory.removesuffix(".dist-info").rpartition("-")

    try:
        return Version(version_text)
    except InvalidVersion as error:
        raise ValueError(f"Wheel {wheel_name} has a .dist-info directory without a valid version: {error}") from error
