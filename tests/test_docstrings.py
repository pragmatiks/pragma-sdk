"""Tests for Google-style docstring parsing."""

from __future__ import annotations

from pragma_sdk.docstrings import extract_short_description, parse_attributes_section


def test_parse_basic_attributes_section() -> None:
    docstring = """Configuration for a Cloud SQL database.

    Attributes:
        instance: The Cloud SQL instance to create the database in.
        name: Name of the database.
        charset: Character set for the database.
    """

    result = parse_attributes_section(docstring)

    assert result == {
        "instance": "The Cloud SQL instance to create the database in.",
        "name": "Name of the database.",
        "charset": "Character set for the database.",
    }


def test_parse_multiline_field_descriptions() -> None:
    docstring = """Configuration for a Cloud SQL database.

    Attributes:
        instance: The Cloud SQL instance to create the database in.
        charset: Character set for the database.
            Defaults to utf8mb4 for broad Unicode support.
        name: Name of the database.
    """

    result = parse_attributes_section(docstring)

    assert result == {
        "instance": "The Cloud SQL instance to create the database in.",
        "charset": "Character set for the database. Defaults to utf8mb4 for broad Unicode support.",
        "name": "Name of the database.",
    }


def test_parse_no_attributes_section() -> None:
    docstring = """Configuration for a Cloud SQL database.

    This config has no Attributes section.
    """

    result = parse_attributes_section(docstring)

    assert result == {}


def test_parse_none_docstring() -> None:
    result = parse_attributes_section(None)

    assert result == {}


def test_parse_attributes_followed_by_another_section() -> None:
    docstring = """Configuration for a Cloud SQL database.

    Attributes:
        instance: The Cloud SQL instance.
        name: Name of the database.

    Returns:
        Something unrelated.
    """

    result = parse_attributes_section(docstring)

    assert result == {
        "instance": "The Cloud SQL instance.",
        "name": "Name of the database.",
    }


def test_parse_empty_attributes_section() -> None:
    docstring = """Configuration for a Cloud SQL database.

    Attributes:

    Returns:
        Something.
    """

    result = parse_attributes_section(docstring)

    assert result == {}


def test_parse_attributes_at_end_of_docstring() -> None:
    docstring = """Configuration for a Cloud SQL database.

    Attributes:
        name: Name of the database.
    """

    result = parse_attributes_section(docstring)

    assert result == {"name": "Name of the database."}


def test_parse_attributes_with_multiple_continuation_lines() -> None:
    docstring = """Config.

    Attributes:
        instance: The Cloud SQL instance to create
            the database in. Must be in the same
            region as the application.
    """

    result = parse_attributes_section(docstring)

    assert result == {
        "instance": "The Cloud SQL instance to create the database in. Must be in the same region as the application.",
    }


def test_parse_attributes_field_with_no_description() -> None:
    docstring = """Config.

    Attributes:
        name:
        size: The size in GB.
    """

    result = parse_attributes_section(docstring)

    assert result == {
        "name": "",
        "size": "The size in GB.",
    }


def test_extract_short_description_single_line() -> None:
    docstring = """Manages Cloud SQL databases."""

    result = extract_short_description(docstring)

    assert result == "Manages Cloud SQL databases."


def test_extract_short_description_multi_paragraph() -> None:
    docstring = """Manages Cloud SQL databases.

    This is a longer description that should not be included.
    """

    result = extract_short_description(docstring)

    assert result == "Manages Cloud SQL databases."


def test_extract_short_description_none() -> None:
    result = extract_short_description(None)

    assert result is None


def test_extract_short_description_empty_string() -> None:
    result = extract_short_description("")

    assert result is None


def test_extract_short_description_multiline_first_paragraph() -> None:
    docstring = """Manages Cloud SQL databases
    with multi-line first paragraph.

    Second paragraph here.
    """

    result = extract_short_description(docstring)

    assert result == "Manages Cloud SQL databases with multi-line first paragraph."


def test_extract_short_description_whitespace_only() -> None:
    result = extract_short_description("   \n  \n  ")

    assert result is None
