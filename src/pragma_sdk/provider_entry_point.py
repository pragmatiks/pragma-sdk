"""The ``pragma.provider`` entry point a provider distribution declares itself with.

A provider wheel declares itself with one entry point in the
``pragma.provider`` group, named after the provider and pointing at its
importable package::

    [project.entry-points."pragma.provider"]
    postgres = "postgres_provider"

It also parses a wheel's ``entry_points.txt`` and finds that entry point in it.
"""

from __future__ import annotations

import importlib.metadata
import keyword
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn


if TYPE_CHECKING:
    import os


PROVIDER_ENTRY_POINT_GROUP = "pragma.provider"
"""Entry point group a provider distribution declares its provider in."""

ENTRY_POINT_DECLARATION = '[project.entry-points."pragma.provider"] <name> = "<package>"'
"""How a ``pyproject.toml`` declares a provider's entry point, for messages telling an author what to write."""

type EntryPointGroups = dict[str, dict[str, str]]
"""Entry point group names mapped to their entry names and object references."""


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


def parse_provider_package(name: str, reference: str) -> str:
    """Parse the importable package a ``pragma.provider`` entry point names.

    Args:
        name: The entry point's name, the provider short name.
        reference: The entry point's object reference, its value.

    Returns:
        The entry point's whole module path, such as ``postgres_provider`` or
        ``acme.provider``.

    Raises:
        ValueError: When the entry point's reference is not an importable
            module path, carries extras, or names an object after a colon
            instead of a package.
    """
    reference_match = importlib.metadata.EntryPoint.pattern.match(reference)

    if reference_match is None or not is_module_path_reference(reference_match):
        raise ValueError(
            f"Entry point {name!r} must name a package, not {reference!r}. Declare it as {ENTRY_POINT_DECLARATION}."
        )

    return reference_match.group("module")


def is_module_path_reference(reference_match: re.Match[str]) -> bool:
    """Tell whether a parsed entry point reference is an importable module path and nothing more.

    Args:
        reference_match: The entry point's value matched against
            :attr:`importlib.metadata.EntryPoint.pattern`.

    Returns:
        ``True`` when the reference has no object and no extras and every
        dot-separated part of its module is a Python identifier and not a
        keyword.
    """
    module = reference_match.group("module")

    return (
        reference_match.group("attr") is None
        and reference_match.group("extras") is None
        and all(part.isidentifier() and not keyword.iskeyword(part) for part in module.split("."))
    )


def is_module_in_package(module_name: str, package_name: str) -> bool:
    """Check whether a module is a package or one of its submodules.

    Args:
        module_name: Fully qualified module name.
        package_name: Importable package name (e.g., "postgres_provider").

    Returns:
        True if the module is the package or lies below it.
    """
    return module_name == package_name or module_name.startswith(f"{package_name}.")


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
        ValueError: When the file is not UTF-8, has an entry line without ``=`` or an entry whose object reference
            is not a module, optionally followed by a colon and an object, or declares an entry name twice in one
            group; the message names the wheel, the same on every Python.
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
        The provider short name and its importable package, as :func:`parse_provider_package` parses it, or
        ``None`` unless the group holds exactly one entry point.

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
        The package, as :func:`parse_provider_package` parses it.

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
