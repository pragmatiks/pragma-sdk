"""Descriptions of pydantic validation errors that never carry the rejected values, which can be secrets."""

from __future__ import annotations

import typing
from typing import Any

from pydantic import ValidationError
from pydantic_core.core_schema import ErrorType


VALUE_FREE_ERROR_TYPES = frozenset(typing.get_args(ErrorType)) - {
    "value_error",
    "assertion_error",
    "union_tag_invalid",
}

REDACTED_FAILURE_MESSAGE = "Value rejected"


def build_validation_error_details(error: ValidationError) -> list[dict[str, Any]]:
    """List a validation error's failures without their inputs, contexts or URLs.

    A failure's message is kept only for pydantic's own error types, whose
    messages describe the expected shape. Messages a validator writes itself
    (``value_error``, ``assertion_error``, custom error types) and the
    ``union_tag_invalid`` message, which repeats the rejected tag, are
    replaced by ``"Value rejected"``.

    Args:
        error: The validation error to describe.

    Returns:
        One JSON-compatible entry per failure, carrying its ``type``, ``loc``
        and ``msg``.
    """
    return [
        {
            "type": failure["type"],
            "loc": list(failure["loc"]),
            "msg": build_failure_message(failure["type"], failure["msg"]),
        }
        for failure in error.errors(include_input=False, include_url=False, include_context=False)
    ]


def build_failure_message(error_type: str, message: str) -> str:
    """Choose the message of one validation failure that cannot repeat a rejected value.

    Args:
        error_type: The failure's pydantic error type.
        message: The failure's own message.

    Returns:
        ``message`` for pydantic's value-free error types, otherwise
        ``"Value rejected"``.
    """
    if error_type in VALUE_FREE_ERROR_TYPES:
        return message

    return REDACTED_FAILURE_MESSAGE


def build_validation_error_message(error: ValidationError) -> str:
    """Describe a validation error in one line, naming each failing field but no value.

    Args:
        error: The validation error to describe.

    Returns:
        The model title followed by each failing location and its message.
    """
    failures = "; ".join(
        f"{format_location(failure['loc'])}: {failure['msg']}" for failure in build_validation_error_details(error)
    )

    return f"Invalid {error.title}: {failures}"


def format_location(location: list[Any]) -> str:
    """Format a validation failure's location as a dotted path.

    Args:
        location: Field names and indexes from the model root to the failure.

    Returns:
        The parts joined with ``.``, or ``(root)`` for a failure of the whole input.
    """
    return ".".join(str(part) for part in location) or "(root)"
