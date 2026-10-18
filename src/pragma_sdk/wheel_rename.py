"""Renaming a provider wheel to its organization's distribution name before it is published."""

from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import itertools
import logging
import re
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from pragma_sdk.exceptions import RenamedWheelWriteError
from pragma_sdk.wheel import (
    MAXIMUM_WHEEL_BYTES,
    DistInfoFiles,
    ExpansionBudget,
    find_provider_entry_point,
    open_wheel_archive,
    parse_entry_points,
    read_dist_info_files,
    stream_archive_member_within_budget,
)
from pragma_sdk.wheel_names import (
    WheelNames,
    build_distribution_name,
    build_wheel_names,
    parse_wheel_filename_fields,
    require_versions_agree,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


logger = logging.getLogger(__name__)


METADATA_LINE_PATTERN = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
"""One ``METADATA`` line with its line ending, which is CRLF, CR or LF, as the email parser splits lines."""

METADATA_HEADER_LINE_PATTERN = re.compile(r"From |[!-9;-~]*:|[ \t]")
"""The start of a ``METADATA`` header block line, as the email parser reads one.

The ``From `` of a Unix envelope line, a header name of printable ASCII
other than ``:`` followed by ``:``, or the space or tab a continuation
starts with.
"""

BYTE_ORDER_MARK = "\ufeff"
"""The byte order mark, which the email parser reads as part of a first header's name."""

REQUIREMENT_NAME_PATTERN = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
"""The distribution name a ``Requires-Dist`` value starts with, after any leading whitespace."""

REQUIRES_DIST_PREFIX = "requires-dist:"
"""Lowercase start of a ``Requires-Dist:`` header line."""

UNIX_CREATE_SYSTEM = 3
"""Zip ``create_system`` value of a member made on Unix, which ``external_attr`` permissions assume."""


@dataclass(frozen=True)
class MetadataHeaders:
    """``METADATA`` text split around its header block, with its single ``Name:`` and ``Version:`` located.

    Attributes:
        lines: The header lines, each keeping its line ending.
        body: The rest of the text, starting with the line that ends the
            header block, such as the empty line.
        name_index: Index in ``lines`` of the ``Name:`` header.
        version_index: Index in ``lines`` of the ``Version:`` header.
        name: The ``Name:`` header's value, as :func:`parse_header_value` parses it.
        version: The ``Version:`` header's value, as :func:`parse_header_value` parses it.
    """

    lines: list[str]
    body: str
    name_index: int
    version_index: int
    name: str
    version: str


@dataclass(frozen=True)
class WheelRename:
    """How one wheel is renamed.

    Attributes:
        source_dist_info_directory: ``.dist-info`` directory name of the wheel being renamed.
        names: Names the renamed wheel carries.
        metadata: ``METADATA`` contents of the renamed wheel.
    """

    source_dist_info_directory: str
    names: WheelNames
    metadata: bytes


def rename_provider_wheel(wheel_path: Path | str, organization_slug: str, destination_directory: Path | str) -> Path:
    """Rename a built provider wheel to its organization's distribution name.

    The distribution name joins the organization slug and the provider short
    name of the wheel's single ``pragma.provider`` entry point; the version
    is the normalized ``METADATA`` version, which the filename and the
    ``.dist-info`` directory must name too. Only distribution metadata
    changes: the ``.dist-info`` and ``.data`` directory names, the
    ``METADATA`` ``Name:`` and ``Version:`` headers, the name of each
    ``Requires-Dist`` naming the wheel's own distribution, such as an extra
    that gathers others, ``RECORD`` and the filename; directory entries and
    ``RECORD`` signatures are dropped. Every other member keeps its content,
    permissions, compression method and timestamp. The same wheel renamed by
    the same Python and zlib always gives the same bytes. The renamed
    wheel's members together may decompress to at most
    :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES`, as the platform measures
    them. The renamed wheel appears under its filename only once written
    whole, through a working file of its own, so renames into one directory
    never share one; it gets the permissions a new file gets under the
    process's umask, and replaces any file already under that filename. A
    failed rename writes no file under the renamed filename, leaves any file
    already there untouched, and removes its working file as far as it can.

    A wheel without exactly one ``pragma.provider`` entry point, or already
    carrying its renamed names in its filename, ``.dist-info`` directory and
    ``METADATA`` ``Name:`` and its normalized version in ``METADATA``
    ``Version:``, comes back unchanged, for the API to judge.

    Args:
        wheel_path: Path to the built ``.whl``.
        organization_slug: Slug of the organization publishing the wheel.
        destination_directory: Existing directory the renamed wheel is
            written to; not the wheel's own directory when the renamed
            filename equals the wheel's.

    Returns:
        Path to the renamed wheel in ``destination_directory``, or
        ``wheel_path`` when the wheel comes back unchanged.

    Raises:
        OSError: When the wheel cannot be opened, such as when it does not
            exist or is not readable, or its file cannot be compared with
            the renamed wheel's path; a read failing once it is open is a
            :class:`zipfile.BadZipFile`.
        RenamedWheelWriteError: When the renamed wheel cannot be written; the
            ``OSError`` writing raised is its ``__cause__``.
        ValueError: When the wheel cannot be renamed, as
            :func:`build_wheel_rename` describes, two of its members share a
            name, it has no single top-level ``.dist-info`` directory or no
            ``METADATA`` in it, the renamed wheel's path names the wheel's
            own file, or two of its members would carry the same name once
            renamed.
        WheelExpansionLimitError: When the renamed wheel's members decompress
            past :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES`.
        zipfile.BadZipFile: When the file is not a readable zip archive or a
            member cannot be read, a compression method other than stored or
            deflate included. When reading the open wheel failed with an
            ``OSError``, that ``OSError`` is its ``__cause__``.
    """  # noqa: DOC502
    wheel_path = Path(wheel_path)
    wheel_name = wheel_path.name

    with wheel_path.open("rb") as wheel_file, open_wheel_archive(wheel_file, wheel_name) as source:
        dist_info_files = read_dist_info_files(source, wheel_name, ExpansionBudget(MAXIMUM_WHEEL_BYTES))
        rename = build_wheel_rename(dist_info_files, wheel_name, organization_slug)

        if rename is None:
            return wheel_path

        renamed_path = Path(destination_directory) / rename.names.filename
        require_distinct_renamed_path(wheel_path, renamed_path)
        write_renamed_wheel_atomically(source, wheel_name, rename, renamed_path)

    return renamed_path


def build_wheel_rename(dist_info_files: DistInfoFiles, wheel_name: str, organization_slug: str) -> WheelRename | None:
    """Build how a wheel is renamed to its organization's distribution name.

    Args:
        dist_info_files: The wheel's ``.dist-info`` files.
        wheel_name: The wheel's file name.
        organization_slug: Slug of the organization publishing the wheel.

    Returns:
        The rename, or ``None`` when the wheel has not exactly one
        ``pragma.provider`` entry point or already carries its renamed names
        and normalized ``METADATA`` version.

    Raises:
        ValueError: When the wheel's ``entry_points.txt`` is invalid or its
            ``pragma.provider`` entry point does not name a package, its
            filename is not a wheel filename, its ``METADATA`` is not UTF-8,
            starts with a byte order mark, has not exactly one ``Name:`` and
            one ``Version:`` header in its header block, has either one empty
            or folded over several lines, or carries no valid version, or its
            filename or ``.dist-info`` directory names another version than
            its ``METADATA``.
    """  # noqa: DOC502
    entry_points = parse_entry_points(dist_info_files.entry_points, wheel_name)
    entry_point = find_provider_entry_point(entry_points, wheel_name)

    if entry_point is None:
        return None

    headers = parse_metadata_headers(dist_info_files.metadata, wheel_name)
    version = parse_metadata_version(headers.version, wheel_name)
    normalized_version = str(version)
    filename_fields = parse_wheel_filename_fields(wheel_name)
    require_versions_agree(filename_fields.version, dist_info_files.directory, version, wheel_name)

    distribution_name = build_distribution_name(organization_slug, entry_point.name)
    renamed_names = build_wheel_names(distribution_name, normalized_version, filename_fields.tags)
    current_names = WheelNames(
        filename=wheel_name,
        dist_info_directory=dist_info_files.directory,
        metadata_name=headers.name,
    )

    if current_names == renamed_names and headers.version == normalized_version:
        return None

    return WheelRename(
        source_dist_info_directory=dist_info_files.directory,
        names=renamed_names,
        metadata=build_renamed_metadata(headers, distribution_name, normalized_version),
    )


def parse_metadata_headers(metadata: bytes, wheel_name: str) -> MetadataHeaders:
    """Parse a wheel's ``METADATA`` into its header lines and body, locating its ``Name:`` and ``Version:``.

    The header block is read as the email parser the platform and installers
    use reads it: lines end with CRLF, CR or LF, and the block ends at the
    first line :data:`METADATA_HEADER_LINE_PATTERN` does not match, such as
    the empty line. Header names are matched case-insensitively.

    Args:
        metadata: ``METADATA`` contents of the wheel.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The header lines and body, which joined give the text back, where
        the single ``Name:`` and ``Version:`` headers are, and their values.

    Raises:
        ValueError: When ``METADATA`` is not UTF-8 or starts with a byte
            order mark, its header block does not contain exactly one
            ``Name:`` and one ``Version:`` header, or either one is empty or
            folded over several lines; every message names the wheel, and
            for ``METADATA`` that is not UTF-8 the
            :class:`UnicodeDecodeError` is its ``__cause__``.
    """  # noqa: DOC502
    try:
        text = metadata.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Wheel {wheel_name} has a METADATA that is not UTF-8; write it as UTF-8") from error

    if text.startswith(BYTE_ORDER_MARK):
        raise ValueError(f"Wheel {wheel_name} has a METADATA starting with a byte order mark; write it without one")

    lines = split_metadata_header_lines(text)
    boundary = sum(len(line) for line in lines)

    name_index = find_single_header_index(lines, "Name", wheel_name)
    version_index = find_single_header_index(lines, "Version", wheel_name)
    require_single_line_header(lines, name_index, "Name", wheel_name)
    require_single_line_header(lines, version_index, "Version", wheel_name)

    return MetadataHeaders(
        lines=lines,
        body=text[boundary:],
        name_index=name_index,
        version_index=version_index,
        name=parse_header_value(lines[name_index]),
        version=parse_header_value(lines[version_index]),
    )


def split_metadata_header_lines(text: str) -> list[str]:
    """Split the header block off the start of ``METADATA`` text, as :func:`parse_metadata_headers` reads it.

    Args:
        text: ``METADATA`` contents.

    Returns:
        The header block's lines, each keeping its line ending.
    """
    lines: list[str] = []

    for line_match in METADATA_LINE_PATTERN.finditer(text):
        line = line_match.group()

        if METADATA_HEADER_LINE_PATTERN.match(line) is None:
            break

        lines.append(line)

    return lines


def find_single_header_index(header_lines: list[str], header: str, wheel_name: str) -> int:
    """Find the line of a ``METADATA`` header that must appear exactly once.

    Args:
        header_lines: Header block lines.
        header: Header name, such as ``Name``, matched case-insensitively.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The index of the header's line.

    Raises:
        ValueError: When the header block does not contain exactly one such header.
    """
    prefix = f"{header.lower()}:"
    indices = [index for index, line in enumerate(header_lines) if line.lower().startswith(prefix)]

    if len(indices) != 1:
        raise ValueError(f"Wheel {wheel_name} has {len(indices)} {header} headers in METADATA; expected exactly one")

    return indices[0]


def require_single_line_header(header_lines: list[str], header_index: int, header: str, wheel_name: str) -> None:
    """Refuse a ``METADATA`` header that is empty or folded over several lines.

    A folded header continues on the lines after it that start with a space
    or a tab; renaming rewrites only the header's own line, so it refuses
    such a header instead of leaving part of the old value behind.

    Args:
        header_lines: Header block lines.
        header_index: Index of the header's line in ``header_lines``.
        header: Header name, such as ``Name``, for error messages.
        wheel_name: The wheel's file name, for error messages.

    Raises:
        ValueError: When the header's value is empty or the next line
            continues it.
    """
    next_line = header_lines[header_index + 1] if header_index + 1 < len(header_lines) else ""

    if not parse_header_value(header_lines[header_index]) or next_line.startswith((" ", "\t")):
        raise ValueError(f"Wheel {wheel_name} has an empty or folded {header} header in METADATA; expected one line")


def parse_header_value(line: str) -> str:
    """Parse the value of a ``METADATA`` header line.

    Args:
        line: One header line, such as ``Name: postgresql``.

    Returns:
        Everything after the first ``:``, without the spaces and tabs before
        it or the line ending, as the email parser the platform uses yields
        it; whitespace before the line ending is kept, so a value carrying it
        is not the canonical one.
    """
    _, _, value = line.partition(":")
    return value.rstrip("\r\n").lstrip(" \t")


def parse_metadata_version(version_text: str, wheel_name: str) -> Version:
    """Parse the ``Version:`` a wheel's ``METADATA`` carries.

    Args:
        version_text: The ``Version:`` header value.
        wheel_name: The wheel's file name, for error messages.

    Returns:
        The version; ``str()`` gives its normalized form, such as ``1.0.0``.

    Raises:
        ValueError: When the version is invalid.
    """
    try:
        return Version(version_text)
    except InvalidVersion as error:
        raise ValueError(f"Wheel {wheel_name} has an invalid METADATA version: {error}") from error


def build_renamed_metadata(headers: MetadataHeaders, distribution_name: str, version: str) -> bytes:
    """Build ``METADATA`` contents carrying a distribution name and normalized version.

    Only the header block changes: its ``Name:`` and ``Version:`` headers
    are set, and each ``Requires-Dist`` naming the wheel's own ``Name:``
    names ``distribution_name`` instead; every changed line keeps its line
    ending, and the description body is left untouched.

    Args:
        headers: ``METADATA`` of the wheel being renamed.
        distribution_name: Distribution name to write.
        version: Normalized version to write.

    Returns:
        The renamed ``METADATA`` contents.
    """
    source_name = headers.name.strip()
    renamed_lines = [build_renamed_requirement_line(line, source_name, distribution_name) for line in headers.lines]
    renamed_lines[headers.name_index] = build_replaced_header_line(
        headers.lines[headers.name_index], "Name", distribution_name
    )
    renamed_lines[headers.version_index] = build_replaced_header_line(
        headers.lines[headers.version_index], "Version", version
    )
    return ("".join(renamed_lines) + headers.body).encode("utf-8")


def build_renamed_requirement_line(line: str, source_name: str, distribution_name: str) -> str:
    """Build a ``METADATA`` header line with a ``Requires-Dist`` on the source distribution renamed.

    Args:
        line: One header line.
        source_name: The distribution name being renamed.
        distribution_name: Distribution name the requirement names instead.

    Returns:
        The line with the requirement's name replaced when it is a
        ``Requires-Dist`` whose name canonicalizes to ``source_name``;
        otherwise the line unchanged.
    """
    if not line.lower().startswith(REQUIRES_DIST_PREFIX):
        return line

    name_match = REQUIREMENT_NAME_PATTERN.match(line, len(REQUIRES_DIST_PREFIX))

    if name_match is None or canonicalize_name(name_match.group(1)) != canonicalize_name(source_name):
        return line

    return f"{line[: name_match.start(1)]}{distribution_name}{line[name_match.end(1) :]}"


def build_replaced_header_line(line: str, header: str, value: str) -> str:
    """Build a ``METADATA`` header line set to a new value, keeping its line ending.

    Args:
        line: The header's current line.
        header: Header name to write, such as ``Name``.
        value: Value to write.

    Returns:
        ``header: value`` followed by the line's own line ending.
    """
    content = line.rstrip("\r\n")
    return f"{header}: {value}{line.removeprefix(content)}"


def require_distinct_renamed_path(wheel_path: Path, renamed_path: Path) -> None:
    """Refuse a renamed wheel's path that names the wheel's own file.

    The files themselves are compared, so a path that differs only in case
    on a case-insensitive filesystem, or a hard link to the wheel, is
    refused too.

    Args:
        wheel_path: Path to the wheel being renamed.
        renamed_path: Path the renamed wheel would be written to.

    Raises:
        ValueError: When both paths name the same file.
    """
    if renamed_path.exists() and renamed_path.samefile(wheel_path):
        raise ValueError(f"Wheel {wheel_path.name} would be overwritten by its renamed wheel; rename it elsewhere")


def write_renamed_wheel_atomically(
    source: zipfile.ZipFile,
    wheel_name: str,
    rename: WheelRename,
    renamed_path: Path,
) -> None:
    """Write the renamed wheel of an open source wheel to its path, whole or not at all.

    The wheel is written to a ``.partial`` working file of its own next to
    ``renamed_path``, created with the permissions a new file gets under the
    process's umask, which replaces ``renamed_path`` once complete and is
    removed, as far as it can be, when the write fails. A failure to remove
    it never hides the failure that ended the write.

    Args:
        source: The open source wheel.
        wheel_name: The source wheel's file name, for error messages.
        rename: How the wheel is renamed.
        renamed_path: Path the renamed wheel is written to.

    Raises:
        RenamedWheelWriteError: When creating, writing or placing the
            renamed wheel fails with an ``OSError``, its ``__cause__``.
        ValueError: As :func:`write_renamed_wheel` describes.
        WheelExpansionLimitError: As :func:`write_renamed_wheel` describes.
        zipfile.BadZipFile: As :func:`write_renamed_wheel` describes.
    """  # noqa: DOC502
    partial_path = renamed_path.parent / f".rename-{secrets.token_hex(8)}.partial"

    try:
        partial_file = partial_path.open("xb")
    except OSError as error:
        raise RenamedWheelWriteError(error) from error

    try:
        with partial_file:
            write_renamed_wheel(source, wheel_name, rename, partial_file)

        partial_path.replace(renamed_path)
    except OSError as error:
        remove_partial_file(partial_path)
        raise RenamedWheelWriteError(error) from error
    except BaseException:
        remove_partial_file(partial_path)
        raise


def write_renamed_wheel(
    source: zipfile.ZipFile,
    wheel_name: str,
    rename: WheelRename,
    renamed_file: IO[bytes],
) -> None:
    """Write the renamed wheel of an open source wheel into a file.

    Directory entries and the source's ``RECORD``, ``RECORD.jws`` and
    ``RECORD.p7s`` are left out; a fresh ``RECORD`` is written last, with a
    fixed timestamp. Every member of the renamed wheel, its ``METADATA`` and
    ``RECORD`` included, shares one
    :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES` expansion budget.

    Args:
        source: The open source wheel.
        wheel_name: The source wheel's file name, for error messages.
        rename: How the wheel is renamed.
        renamed_file: Seekable, empty binary file the renamed wheel is written to.

    Raises:
        ValueError: When two members would carry the same name in the
            renamed wheel.
        WheelExpansionLimitError: When the renamed wheel's members
            decompress past :data:`~pragma_sdk.wheel.MAXIMUM_WHEEL_BYTES`.
        zipfile.BadZipFile: When a member cannot be read, as
            :func:`~pragma_sdk.wheel.stream_archive_member` describes, its
            compression method included.
    """  # noqa: DOC502
    source_dist_info_directory = rename.source_dist_info_directory
    source_data_directory = f"{source_dist_info_directory.removesuffix('.dist-info')}.data"
    source_metadata_path = f"{source_dist_info_directory}/METADATA"
    skipped_paths = {
        f"{source_dist_info_directory}/RECORD",
        f"{source_dist_info_directory}/RECORD.jws",
        f"{source_dist_info_directory}/RECORD.p7s",
    }

    renamed_dist_info_directory = rename.names.dist_info_directory
    renamed_data_directory = f"{renamed_dist_info_directory.removesuffix('.dist-info')}.data"
    renamed_record_path = f"{renamed_dist_info_directory}/RECORD"
    renamed_directories = {
        source_dist_info_directory: renamed_dist_info_directory,
        source_data_directory: renamed_data_directory,
    }

    budget = ExpansionBudget(MAXIMUM_WHEEL_BYTES)
    record_rows: list[tuple[str, str, str]] = []
    written_names: set[str] = set()

    with zipfile.ZipFile(renamed_file, "w", zipfile.ZIP_DEFLATED) as renamed_archive:
        for source_info in source.infolist():
            if source_info.is_dir() or source_info.filename in skipped_paths:
                continue

            member_name = build_renamed_member_name(source_info.filename, renamed_directories)

            if member_name in written_names:
                raise ValueError(f"Wheel {wheel_name} has two members named {member_name} once renamed")

            written_names.add(member_name)
            renamed_info = build_renamed_member_info(source_info, member_name)

            if source_info.filename == source_metadata_path:
                budget.consume(len(rename.metadata), wheel_name, member_name)
                digest, size = write_member_content(renamed_archive, renamed_info, rename.metadata)
            else:
                member_chunks = stream_archive_member_within_budget(source, wheel_name, source_info, budget)

                with contextlib.closing(member_chunks) as chunks:
                    digest, size = write_streamed_member(renamed_archive, renamed_info, chunks)

            record_rows.append((renamed_info.filename, digest, size))

        record_rows.append((renamed_record_path, "", ""))
        record = build_record(record_rows)
        budget.consume(len(record), wheel_name, renamed_record_path)
        renamed_archive.writestr(build_record_info(renamed_record_path), record)


def build_renamed_member_name(member_name: str, renamed_directories: dict[str, str]) -> str:
    """Build a member's name in the renamed wheel.

    Args:
        member_name: Member name in the source wheel.
        renamed_directories: Source top-level directory names mapped to their new names.

    Returns:
        The name under its renamed top-level directory, or unchanged when
        its directory is not renamed.
    """
    directory, separator, remainder = member_name.partition("/")

    if not separator or directory not in renamed_directories:
        return member_name

    return f"{renamed_directories[directory]}/{remainder}"


def build_renamed_member_info(source_info: zipfile.ZipInfo, member_name: str) -> zipfile.ZipInfo:
    """Build the renamed wheel's member for a source member, under a possibly new name.

    Args:
        source_info: Source member.
        member_name: Member name in the renamed wheel.

    Returns:
        A member keeping the source's timestamp, compression, permissions
        and creating system, and its declared size, so a member declared past
        the zip64 limit is written with zip64 extensions.
    """
    renamed_info = zipfile.ZipInfo(member_name, date_time=source_info.date_time)
    renamed_info.compress_type = source_info.compress_type
    renamed_info.external_attr = source_info.external_attr
    renamed_info.create_system = source_info.create_system
    renamed_info.file_size = source_info.file_size
    return renamed_info


def write_member_content(
    renamed_archive: zipfile.ZipFile,
    renamed_info: zipfile.ZipInfo,
    content: bytes,
) -> tuple[str, str]:
    """Write one member from in-memory content into the renamed wheel.

    Args:
        renamed_archive: Renamed wheel being written.
        renamed_info: Name and attributes of the member.
        content: The member's content.

    Returns:
        The member's ``RECORD`` digest and size.
    """
    renamed_archive.writestr(renamed_info, content)
    return build_record_digest(hashlib.sha256(content).digest()), str(len(content))


def write_streamed_member(
    renamed_archive: zipfile.ZipFile,
    renamed_info: zipfile.ZipInfo,
    chunks: Iterator[bytes],
) -> tuple[str, str]:
    """Write one member streamed in chunks into the renamed wheel.

    A member whose first chunk cannot be read raises before anything is
    written for it.

    Args:
        renamed_archive: Renamed wheel being written.
        renamed_info: Name and attributes of the member.
        chunks: The member's content, in order.

    Returns:
        The member's ``RECORD`` digest and size.
    """
    first_chunk = next(chunks, b"")
    digest = hashlib.sha256()
    size = 0

    with renamed_archive.open(renamed_info, "w") as writer:
        for chunk in itertools.chain((first_chunk,), chunks):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)

    return build_record_digest(digest.digest()), str(size)


def build_record_digest(digest: bytes) -> str:
    """Encode a SHA-256 digest the way ``RECORD`` stores it.

    Args:
        digest: Raw SHA-256 digest of a member.

    Returns:
        ``sha256=`` followed by the unpadded URL-safe base64 digest.
    """
    return f"sha256={base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')}"


def build_record(rows: list[tuple[str, str, str]]) -> bytes:
    """Serialize ``RECORD`` rows into the wheel ``RECORD`` CSV format.

    Args:
        rows: One ``(path, hash, size)`` triple per wheel entry. The ``RECORD``
            file's own row carries an empty hash and size per the wheel spec.

    Returns:
        The encoded ``RECORD`` file contents.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def build_record_info(record_path: str) -> zipfile.ZipInfo:
    """Build the member the renamed wheel's ``RECORD`` is written as.

    Its timestamp, permissions and creating system are fixed, so the same
    rename gives the same bytes on every operating system.

    Args:
        record_path: ``RECORD`` path in the renamed wheel.

    Returns:
        A deflated member with ``0o644`` permissions made on Unix.
    """
    record_info = zipfile.ZipInfo(record_path)
    record_info.compress_type = zipfile.ZIP_DEFLATED
    record_info.external_attr = 0o644 << 16
    record_info.create_system = UNIX_CREATE_SYSTEM
    return record_info


def remove_partial_file(partial_path: Path) -> None:
    """Remove a rename's working file, as far as it can be removed.

    A working file that cannot be removed is left as it is and logged as
    :func:`log_path_not_removed` logs it, the
    ``rename_partial_file_not_removed`` event; this never raises.

    Args:
        partial_path: The working file; a missing file is left alone.
    """
    try:
        partial_path.unlink(missing_ok=True)
    except OSError as error:
        log_path_not_removed(logger, "rename_partial_file_not_removed", str(partial_path), error)


def log_path_not_removed(event_logger: logging.Logger, event: str, path: str, error: BaseException) -> None:
    """Log a temporary path that could not be removed as a warning naming it.

    The message is the event followed by ``path=``, ``error_type=`` and
    ``error=``, so a handler printing only the message names the path left
    on disk and why; the same three fields are also the record's extras.

    Args:
        event_logger: Logger the warning is written to.
        event: The event name, such as ``rename_partial_file_not_removed``.
        path: The path left in place.
        error: What removing it raised.
    """
    error_type = type(error).__name__
    event_logger.warning(
        "%s path=%s error_type=%s error=%s",
        event,
        path,
        error_type,
        error,
        extra={"path": path, "error_type": error_type, "error": str(error)},
    )
