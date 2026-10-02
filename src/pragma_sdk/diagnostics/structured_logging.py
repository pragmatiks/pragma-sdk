"""Log output of the worker and introspection processes."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import EventDict, WrappedLogger

from pragma_sdk.diagnostics.error_descriptions import build_redacted_text, format_exception_trace


def configure_logging() -> None:
    """Write log events, standard-library records and warnings at INFO or above to stderr as JSON lines.

    Each line carries its level, an ISO-8601 UTC timestamp, every value bound
    with :func:`structlog.contextvars.bind_contextvars`, and for a logged
    exception an ``exception`` trace whose messages repeat no secret value.
    URL query strings, URL credentials and pydantic ``input_value`` fragments
    in the event text are redacted.
    Standard-library records, Python warnings included, also carry their
    logger name.
    """
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_event_text,
        format_exception_trace,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared_processors],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)
    logging.captureWarnings(True)


def redact_event_text(logger: WrappedLogger, method_name: str, event: EventDict) -> EventDict:
    """Redact URL query strings, URL credentials and pydantic ``input_value`` fragments in a log event's text.

    A structlog processor, so a library logging a signed URL, such as an
    HTTP client logging the request it sends, repeats no secret value.

    Args:
        logger: The wrapped logger, unused.
        method_name: The log method called, unused.
        event: The event being processed.

    Returns:
        The event, its ``event`` text redacted when it is a string.
    """
    text = event.get("event")

    if isinstance(text, str):
        event["event"] = build_redacted_text(text)

    return event
