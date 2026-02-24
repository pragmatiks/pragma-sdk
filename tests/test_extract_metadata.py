"""Tests for provider metadata extraction from pyproject.toml."""

from __future__ import annotations

import os
from pathlib import Path

from pragma_sdk.provider.extract_schemas import extract_metadata


def _write_pyproject(tmp_path: Path, content: str) -> None:
    (tmp_path / "pyproject.toml").write_text(content)


def test_all_metadata_fields_present(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[tool.pragma]
provider = "postgres"
package = "postgres_provider"
display_name = "PostgreSQL"
description = "Managed PostgreSQL databases"
author = "Pragmatiks"
tags = ["database", "sql"]
icon = "postgres.svg"
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result == {
        "display_name": "PostgreSQL",
        "description": "Managed PostgreSQL databases",
        "author": "Pragmatiks",
        "tags": ["database", "sql"],
        "icon": "postgres.svg",
    }


def test_some_metadata_fields_present(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[tool.pragma]
provider = "gcp"
package = "gcp_provider"
display_name = "Google Cloud"
description = "GCP infrastructure provider"
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result == {
        "display_name": "Google Cloud",
        "description": "GCP infrastructure provider",
    }


def test_returns_none_when_no_metadata_fields(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[tool.pragma]
provider = "gcp"
package = "gcp_provider"
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result is None


def test_returns_none_when_no_pragma_section(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[project]
name = "some-project"
version = "0.1.0"
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result is None


def test_returns_none_when_no_pyproject(tmp_path: Path) -> None:
    os.chdir(tmp_path)
    result = extract_metadata()

    assert result is None


def test_ignores_unknown_keys(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[tool.pragma]
provider = "test"
package = "test_provider"
display_name = "Test"
unknown_field = "should be ignored"
another_unknown = 42
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result == {"display_name": "Test"}


def test_tags_as_empty_list(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """\
[tool.pragma]
provider = "test"
package = "test_provider"
display_name = "Test"
tags = []
""",
    )

    os.chdir(tmp_path)
    result = extract_metadata()

    assert result == {"display_name": "Test", "tags": []}
