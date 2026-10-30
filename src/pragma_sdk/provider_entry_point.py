"""The ``pragma.provider`` entry point a provider distribution declares itself with.

A provider wheel declares itself with one entry point in the
``pragma.provider`` group, named after the provider and pointing at its
importable package::

    [project.entry-points."pragma.provider"]
    postgres = "postgres_provider"
"""

from __future__ import annotations

import importlib.metadata
import keyword
import re


PROVIDER_ENTRY_POINT_GROUP = "pragma.provider"
"""Entry point group a provider distribution declares its provider in."""

ENTRY_POINT_DECLARATION = '[project.entry-points."pragma.provider"] <name> = "<package>"'
"""How a ``pyproject.toml`` declares a provider's entry point, for messages telling an author what to write."""


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
