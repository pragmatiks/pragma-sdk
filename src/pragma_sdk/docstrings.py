"""Google-style docstring parsing for resource metadata extraction."""

from __future__ import annotations

import re


_SECTION_HEADER_RE = re.compile(r"^(\w[\w\s]*):\s*$")
_ATTRIBUTE_LINE_RE = re.compile(r"^(\s+)(\w+)(?:\s*\([^)]*\))?:\s*(.*)$")


def extract_short_description(docstring: str | None) -> str | None:
    """Return the first paragraph of a docstring, stripped.

    Everything before the first blank line is considered the short description.

    Args:
        docstring: Raw docstring text, or None.

    Returns:
        Stripped first paragraph, or None if docstring is None or empty.
    """
    if docstring is None:
        return None

    lines = docstring.strip().splitlines()

    if not lines:
        return None

    paragraph_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            break

        paragraph_lines.append(stripped)

    if not paragraph_lines:
        return None

    return " ".join(paragraph_lines)


def parse_attributes_section(docstring: str | None) -> dict[str, str]:
    """Parse field descriptions from a Google-style Attributes section.

    Extracts field names and their descriptions from the ``Attributes:`` section
    of a docstring. Handles multi-line descriptions where continuation lines are
    indented further than the field name line.

    Args:
        docstring: Raw docstring text, or None.

    Returns:
        Mapping of field names to their description strings.
        Empty dict if no Attributes section found or docstring is None.
    """
    if docstring is None:
        return {}

    lines = docstring.splitlines()

    attributes_start = None
    section_indent = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "Attributes:":
            attributes_start = i + 1
            section_indent = len(line) - len(line.lstrip())
            break

    if attributes_start is None:
        return {}

    result: dict[str, str] = {}
    current_field: str | None = None
    current_desc_parts: list[str] = []
    field_indent: int = 0

    for line in lines[attributes_start:]:
        if not line.strip():
            continue

        line_indent = len(line) - len(line.lstrip())

        if line_indent <= section_indent and _SECTION_HEADER_RE.match(line.strip()):
            break

        attr_match = _ATTRIBUTE_LINE_RE.match(line)

        if attr_match:
            if current_field is not None:
                result[current_field] = " ".join(current_desc_parts)

            indent_str = attr_match.group(1)
            field_indent = len(indent_str.expandtabs())
            current_field = attr_match.group(2)
            desc = attr_match.group(3).strip()
            current_desc_parts = [desc] if desc else []
        elif current_field is not None:
            stripped = line.strip()
            line_indent = len(line) - len(line.lstrip())

            if line_indent > field_indent and stripped:
                current_desc_parts.append(stripped)
            else:
                break

    if current_field is not None:
        result[current_field] = " ".join(current_desc_parts)

    return result
