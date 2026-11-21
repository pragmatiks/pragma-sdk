"""Events of a followed resource log stream and the parser reading them from the API's server-sent events.

The API sends ``entry`` events, each one log entry, then exactly one terminal
event: ``end`` when the stream finished, or ``error`` when it broke. Lines
starting with ``:`` are keep-alive comments and carry no event; the ``id``
and ``retry`` fields are ignored, since a follow resumes through ``since``.
An event name this SDK does not know is skipped, so the API can add events
without breaking older clients.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Literal

from pydantic import BaseModel

from pragma_sdk.exceptions import LogFollowInterruptedError
from pragma_sdk.types import LogEntry


class LogFollowEntry(BaseModel):
    """An ``entry`` event: one log entry of the followed resource.

    Attributes:
        kind: Always ``entry``.
        entry: The log entry.
    """

    kind: Literal["entry"] = "entry"
    entry: LogEntry


class LogFollowEnd(BaseModel):
    """The ``end`` event: the stream finished and nothing more follows.

    Attributes:
        kind: Always ``end``.
        reason: Why it finished, an open-ended string such as ``completed``
            when the provider had nothing left to follow, or
            ``provider_restarted`` when the provider restarted, or
            ``host_draining`` when the provider's host restarted; after
            either, following again with ``since`` continues.
    """

    kind: Literal["end"] = "end"
    reason: str


class LogFollowError(BaseModel):
    """The ``error`` event: the stream broke and nothing more follows.

    Attributes:
        kind: Always ``error``.
        message: What went wrong, readable by a user.
        reason: Machine-readable cause, an open-ended string such as
            ``overflow``, ``provider_failed`` or
            ``provider_unavailable``.
    """

    kind: Literal["error"] = "error"
    message: str
    reason: str


type LogFollowEvent = LogFollowEntry | LogFollowEnd | LogFollowError
"""One event of a followed log stream, told apart by ``kind``."""


class ServerSentEventAssembler:
    """Collects server-sent event lines into complete ``(event, data)`` pairs.

    Attributes:
        event_name: Name of the event in progress, ``message`` until an ``event`` field names it.
        data_lines: Data lines of the event in progress.
    """

    def __init__(self) -> None:
        """Start with no event in progress."""
        self.event_name = "message"
        self.data_lines: list[str] = []

    def feed(self, line: str) -> tuple[str, str] | None:
        """Take one line of the stream.

        Args:
            line: One line, without its line ending.

        Returns:
            The event name and joined data once a blank line completes an
            event that carried data, otherwise ``None``.
        """
        if not line:
            return self.take_completed_event()

        if line.startswith(":"):
            return None

        field, _, value = line.partition(":")
        value = value.removeprefix(" ")

        if field == "event":
            self.event_name = value or "message"
        elif field == "data":
            self.data_lines.append(value)

        return None

    def take_completed_event(self) -> tuple[str, str] | None:
        """Take the event in progress and start a new one.

        Returns:
            The event name and joined data, or ``None`` when no data arrived.
        """
        event_name, data_lines = self.event_name, self.data_lines
        self.event_name, self.data_lines = "message", []

        if not data_lines:
            return None

        return event_name, "\n".join(data_lines)


def parse_log_follow_event(event_name: str, data: str) -> LogFollowEvent | None:
    """Build the log stream event one server-sent event carries.

    Args:
        event_name: The server-sent event's name.
        data: Its JSON data.

    Returns:
        The event, or ``None`` for an event name this SDK does not know.

    Raises:
        ValidationError: If the data is not JSON matching the event's shape.
    """  # noqa: DOC502
    match event_name:
        case "entry":
            return LogFollowEntry(entry=LogEntry.model_validate_json(data))
        case "end":
            return LogFollowEnd.model_validate_json(data)
        case "error":
            return LogFollowError.model_validate_json(data)
        case _:
            return None


def parse_completed_event(completed: tuple[str, str] | None) -> LogFollowEvent | None:
    """Build the log stream event of an assembled server-sent event.

    Args:
        completed: The event name and data an assembler completed, or ``None``.

    Returns:
        The event, or ``None`` when nothing completed or the name is unknown.

    Raises:
        ValidationError: If the data is not JSON matching the event's shape.
    """  # noqa: DOC502
    if completed is None:
        return None

    return parse_log_follow_event(*completed)


def is_terminal_event(event: LogFollowEvent) -> bool:
    """Tell whether an event ends the stream.

    Args:
        event: A followed log stream event.

    Returns:
        ``True`` for an ``end`` or ``error`` event.
    """
    return not isinstance(event, LogFollowEntry)


def parse_log_follow_stream(lines: Iterable[str]) -> Iterator[LogFollowEvent]:
    """Read the events of a followed log stream, stopping after its terminal event.

    A terminal event the lines end on without its closing blank line still counts.

    Args:
        lines: The stream's lines, without line endings.

    Yields:
        Each ``entry`` event, then the ``end`` or ``error`` event.

    Raises:
        ValidationError: If an event's data is not JSON matching its shape.
        LogFollowInterruptedError: If the lines run out before a terminal event.
    """  # noqa: DOC502
    assembler = ServerSentEventAssembler()

    for line in lines:
        event = parse_completed_event(assembler.feed(line))

        if event is None:
            continue

        yield event

        if is_terminal_event(event):
            return

    final_event = parse_completed_event(assembler.take_completed_event())

    if final_event is None or not is_terminal_event(final_event):
        raise LogFollowInterruptedError

    yield final_event


async def parse_log_follow_stream_async(lines: AsyncIterable[str]) -> AsyncIterator[LogFollowEvent]:
    """Read the events of a followed log stream from async lines, stopping after its terminal event.

    A terminal event the lines end on without its closing blank line still counts.

    Args:
        lines: The stream's lines, without line endings.

    Yields:
        Each ``entry`` event, then the ``end`` or ``error`` event.

    Raises:
        ValidationError: If an event's data is not JSON matching its shape.
        LogFollowInterruptedError: If the lines run out before a terminal event.
    """  # noqa: DOC502
    assembler = ServerSentEventAssembler()

    async for line in lines:
        event = parse_completed_event(assembler.feed(line))

        if event is None:
            continue

        yield event

        if is_terminal_event(event):
            return

    final_event = parse_completed_event(assembler.take_completed_event())

    if final_event is None or not is_terminal_event(final_event):
        raise LogFollowInterruptedError

    yield final_event
