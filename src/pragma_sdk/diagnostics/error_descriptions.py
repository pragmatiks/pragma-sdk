"""Descriptions of exceptions that keep revealed secret values out of logs and host replies.

Handlers run with secret configuration revealed, so an exception's text, or
the text of any exception chained to it, can repeat a secret: a pydantic
validation error repeats its input, and an HTTP client error repeats the
signed URL it requested.
"""

from __future__ import annotations

import re
import sys
import traceback
from types import TracebackType
from typing import Any

from pydantic import ValidationError
from structlog.typing import EventDict, WrappedLogger

from pragma_sdk.diagnostics.validation_errors import build_validation_error_message


URL_QUERY_PATTERN = re.compile(r"(?P<address>[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>?#]+)\?[^\s'\"<>#]*")

URL_CREDENTIALS_PATTERN = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^\s'\"<>/@]+@")

INPUT_VALUE_PATTERN = re.compile(r"input_value=.*?(?=, input_type=)", re.DOTALL)

REDACTED = "<redacted>"


def build_error_message(error: BaseException) -> str:
    """Describe an exception in one message that repeats no rejected input or URL secret.

    A pydantic validation error is described by its failing fields alone.
    Any other exception keeps its own text, with URL query strings, URL
    credentials and pydantic ``input_value`` fragments redacted. Never raises,
    even when the exception's ``__str__`` does.

    Args:
        error: The exception to describe.

    Returns:
        The redacted message, or the exception's type name when it has no
        text or its text cannot be read.
    """
    if isinstance(error, ValidationError):
        return build_validation_error_message(error)

    try:
        text = str(error)
    except Exception:
        return type(error).__name__

    return build_redacted_text(text) or type(error).__name__


def build_redacted_text(text: str) -> str:
    """Redact URL query strings, URL credentials and pydantic ``input_value`` fragments in a text.

    Args:
        text: The text to redact.

    Returns:
        The text with each of those parts replaced by ``<redacted>``.
    """
    without_queries = URL_QUERY_PATTERN.sub(rf"\g<address>?{REDACTED}", text)
    without_credentials = URL_CREDENTIALS_PATTERN.sub(rf"\g<scheme>{REDACTED}@", without_queries)

    return INPUT_VALUE_PATTERN.sub(f"input_value={REDACTED}", without_credentials)


def build_error_trace(error: BaseException) -> list[dict[str, Any]]:
    """Describe an exception and every exception chained to it, without revealing secret values.

    The chain follows ``__cause__``, then ``__context__`` unless the exception
    suppressed it, the way Python prints a traceback. Each member of an
    exception group is described with its own chain.

    Args:
        error: The exception to describe.

    Returns:
        One entry per exception, the given one first, each carrying its
        ``type``, its redacted ``message``, its traceback ``locations`` as
        ``"file:line in function"``, and for an exception group the
        ``exceptions`` it holds.
    """
    trace: list[dict[str, Any]] = []
    described: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in described:
        described.add(id(current))
        trace.append(build_exception_entry(current))
        current = find_chained_exception(current)

    return trace


def build_exception_entry(error: BaseException) -> dict[str, Any]:
    """Describe one exception without the exceptions chained to it.

    Args:
        error: The exception to describe.

    Returns:
        Its ``type``, redacted ``message`` and traceback ``locations``, and for an
        exception group the ``exceptions`` it holds, each with its own chain.
    """
    entry: dict[str, Any] = {
        "type": type(error).__name__,
        "message": build_error_message(error),
        "locations": build_traceback_locations(error.__traceback__),
    }

    if isinstance(error, BaseExceptionGroup):
        entry["exceptions"] = [build_error_trace(member) for member in error.exceptions]

    return entry


def find_chained_exception(error: BaseException) -> BaseException | None:
    """Find the exception Python prints before this one in a traceback.

    Args:
        error: The exception whose chain to follow.

    Returns:
        Its explicit cause, else its implicit context unless suppressed, else ``None``.
    """
    if error.__cause__ is not None:
        return error.__cause__

    if error.__suppress_context__:
        return None

    return error.__context__


def build_traceback_locations(trace: TracebackType | None) -> list[str]:
    """List the code locations of a traceback, without source lines or local values.

    Args:
        trace: The traceback to list, or ``None``.

    Returns:
        One ``"file:line in function"`` entry per traceback entry, outermost first.
    """
    return [
        f"{stack_frame.f_code.co_filename}:{line} in {stack_frame.f_code.co_name}"
        for stack_frame, line in traceback.walk_tb(trace)
    ]


def format_exception_trace(logger: WrappedLogger, method_name: str, event: EventDict) -> EventDict:
    """Replace a log event's ``exc_info`` with an ``exception`` trace that reveals no secret value.

    A structlog processor taking the place of ``format_exc_info``.

    Args:
        logger: The wrapped logger, unused.
        method_name: The log method called, unused.
        event: The event being processed.

    Returns:
        The event, carrying the :func:`build_error_trace` of its exception
        under ``exception`` when ``exc_info`` named one.
    """
    error = find_logged_exception(event.pop("exc_info", None))

    if error is not None:
        event["exception"] = build_error_trace(error)

    return event


def find_logged_exception(exception_info: Any) -> BaseException | None:
    """Find the exception a log call's ``exc_info`` names.

    Args:
        exception_info: ``True`` for the exception being handled, an
            exception, an ``exc_info`` tuple, or a false value for none.

    Returns:
        The exception, or ``None`` when there is none.
    """
    if exception_info is True:
        return sys.exception()

    if isinstance(exception_info, BaseException):
        return exception_info

    if isinstance(exception_info, tuple):
        return exception_info[1]

    return None
