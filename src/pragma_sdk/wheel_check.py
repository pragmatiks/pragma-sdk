"""Checking a wheel's ``.dist-info`` against the declaration it was published with."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from enum import StrEnum

from pragma_sdk.exceptions import WheelExpansionLimitError
from pragma_sdk.wheel import MAXIMUM_WHEEL_BYTES, ExpansionBudget, read_dist_info_files


class DistInfoRefusalReason(StrEnum):
    """Why a wheel's ``.dist-info`` is refused against the one it was declared with."""

    DIRECTORY_MISMATCH = "dist_info_directory_mismatch"
    SHA256_MISMATCH = "dist_info_sha256_mismatch"
    WHEEL_TOO_LARGE = "wheel_too_large"
    WHEEL_UNREADABLE = "wheel_unreadable"


@dataclass(frozen=True)
class DistInfoRefusal:
    """A refused wheel's ``.dist-info``: the reason, and what the wheel itself carried.

    Attributes:
        reason: Why it is refused.
        dist_info_directory: The wheel's own ``.dist-info`` directory name,
            or ``None`` when its files could not be read.
        dist_info_sha256: The digest of the wheel's own ``.dist-info``
            files, or ``None`` unless the reason is
            ``dist_info_sha256_mismatch``.
        error: What reading the files raised, or ``None`` when they were read.
    """

    reason: DistInfoRefusalReason
    dist_info_directory: str | None = None
    dist_info_sha256: str | None = None
    error: Exception | None = None


def read_dist_info_refusal(
    archive: zipfile.ZipFile,
    wheel_name: str,
    declared_dist_info_directory: str,
    declared_dist_info_sha256: str,
) -> DistInfoRefusal | None:
    """Read why a wheel's ``.dist-info`` is refused against the directory and digest it was declared with.

    The ``.dist-info`` files are read under a budget of their own of
    :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES`. Files that cannot be read
    are reported as a refusal, not raised, so every refusal carries one
    :class:`DistInfoRefusalReason`: ``wheel_unreadable`` for a damaged
    archive or member, a member compressed with a method other than stored
    or deflate, two members sharing a name, or a wheel without a single
    ``.dist-info`` directory holding ``METADATA``; ``wheel_too_large`` for
    files that decompress past the budget; ``dist_info_directory_mismatch``
    and ``dist_info_sha256_mismatch`` for a wheel read whole that differs,
    the directory compared first.

    Args:
        archive: The open wheel archive.
        wheel_name: The wheel's file name, for error messages.
        declared_dist_info_directory: Declared ``.dist-info`` directory name.
        declared_dist_info_sha256: Declared digest, as
            :func:`compute_dist_info_sha256` computes it.

    Returns:
        Why the wheel is refused, or ``None`` when its ``.dist-info`` matches
        the declaration.
    """
    try:
        dist_info_files = read_dist_info_files(archive, wheel_name, ExpansionBudget(MAXIMUM_WHEEL_BYTES))
    except WheelExpansionLimitError as error:
        return DistInfoRefusal(reason=DistInfoRefusalReason.WHEEL_TOO_LARGE, error=error)
    except (zipfile.BadZipFile, ValueError) as error:
        return DistInfoRefusal(reason=DistInfoRefusalReason.WHEEL_UNREADABLE, error=error)

    if dist_info_files.directory != declared_dist_info_directory:
        return DistInfoRefusal(
            reason=DistInfoRefusalReason.DIRECTORY_MISMATCH,
            dist_info_directory=dist_info_files.directory,
        )

    wheel_dist_info_sha256 = compute_dist_info_sha256(dist_info_files.metadata, dist_info_files.entry_points)

    if wheel_dist_info_sha256 != declared_dist_info_sha256:
        return DistInfoRefusal(
            reason=DistInfoRefusalReason.SHA256_MISMATCH,
            dist_info_directory=dist_info_files.directory,
            dist_info_sha256=wheel_dist_info_sha256,
        )

    return None


def compute_dist_info_sha256(metadata: bytes, entry_points: bytes | None) -> str:
    """Compute the digest a wheel's ``METADATA`` and ``entry_points.txt`` are compared by.

    The SHA-256 runs over ``METADATA`` and then ``entry_points.txt``, each
    preceded by its length as 8 big-endian bytes. A wheel without
    ``entry_points.txt`` contributes nothing for it, so its digest differs
    from that of a wheel with an empty one. The ``.dist-info`` directory name
    is not part of the digest.

    Args:
        metadata: Contents of ``METADATA``.
        entry_points: Contents of ``entry_points.txt``, or ``None`` when the
            wheel has none.

    Returns:
        Lowercase hex SHA-256 digest.
    """
    digest = hashlib.sha256()

    for content in (metadata, entry_points):
        if content is None:
            continue

        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    return digest.hexdigest()
