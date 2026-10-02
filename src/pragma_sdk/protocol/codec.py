"""Length-prefixed JSON-RPC 2.0 framing for the worker protocol.

Both ends build frames with :class:`JsonRpcCodec` and exchange them with
:meth:`~JsonRpcCodec.write` and :meth:`~JsonRpcCodec.read`. Payloads cross as
JSON with every secret value revealed; bytes, secret or not, cross as UTF-8
text and must decode as UTF-8. Numbers must be finite.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from pydantic import BaseModel, Secret, SecretBytes, SecretStr, ValidationError
from pydantic_core import to_jsonable_python

from pragma_sdk.protocol.constants import (
    JSONRPC_VERSION,
    LENGTH_PREFIX_BYTES,
    LENGTH_PREFIX_FORMAT,
    MAXIMUM_FRAME_BYTES,
)
from pragma_sdk.protocol.frames import (
    Envelope,
    ErrorObject,
    FailureFrame,
    NotificationFrame,
    RequestFrame,
    SuccessFrame,
)


type OutgoingFrame = RequestFrame | NotificationFrame | SuccessFrame | FailureFrame


class FrameError(Exception):
    """Raised when a frame cannot be read or written: the peer closed, or the frame is oversized or malformed."""


class ConnectionClosedError(FrameError):
    """Raised when the peer closes the connection cleanly between two frames."""


class MalformedFrameError(FrameError):
    """Raised when a frame body is not a JSON-RPC message; the message never repeats the body."""


class FrameTooLargeError(FrameError):
    """Raised when a frame body exceeds ``MAXIMUM_FRAME_BYTES``.

    Attributes:
        length: Length of the refused frame body in bytes.
    """

    def __init__(self, length: int) -> None:
        """Initialize the error.

        Args:
            length: Length of the refused frame body in bytes.
        """
        self.length = length
        super().__init__(f"Frame length {length} exceeds maximum {MAXIMUM_FRAME_BYTES}")


def build_revealed_payload(value: Any) -> Any:
    """Turn a payload into plain data with every secret value revealed.

    Args:
        value: Payload to convert: a model, a mapping, a sequence or a scalar.

    Returns:
        The payload as dicts, lists and scalars, with every ``Secret``,
        ``SecretStr`` and ``SecretBytes`` value replaced by the value it holds.
    """
    if isinstance(value, BaseModel):
        return build_revealed_payload(value.model_dump())

    if isinstance(value, Secret | SecretStr | SecretBytes):
        return build_revealed_payload(value.get_secret_value())

    if isinstance(value, dict):
        return {key: build_revealed_payload(item) for key, item in value.items()}

    if isinstance(value, list | tuple | set | frozenset):
        return [build_revealed_payload(item) for item in value]

    return value


def build_json_payload(value: Any) -> Any:
    """Convert a payload into JSON-compatible data, revealing secret values.

    Args:
        value: Payload to convert.

    Returns:
        JSON-compatible data: datetimes as ISO text, enums as their values,
        bytes as UTF-8 text.

    Raises:
        UnicodeDecodeError: If a bytes value, secret or not, is not UTF-8.
    """  # noqa: DOC502
    return to_jsonable_python(build_revealed_payload(value))


class JsonRpcCodec:
    """Builds, writes and reads the frames of the worker protocol."""

    def build_request(self, correlation_id: int, method: str, params: Any) -> RequestFrame:
        """Build a request frame.

        Args:
            correlation_id: Correlation id unique among the sender's open requests.
            method: Method name.
            params: Method parameters, usually a payload model.

        Returns:
            The request frame.

        Raises:
            UnicodeDecodeError: If a bytes value in ``params`` is not UTF-8.
        """  # noqa: DOC502
        payload = build_json_payload(params)

        return RequestFrame(jsonrpc=JSONRPC_VERSION, id=correlation_id, method=method, params=payload)

    def build_notification(self, method: str) -> NotificationFrame:
        """Build a notification frame.

        Args:
            method: Method name.

        Returns:
            The notification frame.
        """
        return NotificationFrame(jsonrpc=JSONRPC_VERSION, method=method)

    def build_success(self, correlation_id: int, result: Any) -> SuccessFrame:
        """Build a response frame reporting success.

        Args:
            correlation_id: Correlation id of the request being answered.
            result: Method result, a payload model or plain data.

        Returns:
            The success frame.

        Raises:
            UnicodeDecodeError: If a bytes value in ``result`` is not UTF-8.
        """  # noqa: DOC502
        payload = build_json_payload(result)

        return SuccessFrame(jsonrpc=JSONRPC_VERSION, id=correlation_id, result=payload)

    def build_failure(self, correlation_id: int, code: int, message: str, data: Any = None) -> FailureFrame:
        """Build a response frame reporting failure.

        Args:
            correlation_id: Correlation id of the request being answered.
            code: JSON-RPC error code.
            message: Human-readable error description. Characters it holds
                that UTF-8 cannot encode, such as lone surrogates, are
                written as backslash escapes.
            data: Structured error detail, a payload model or plain data.

        Returns:
            The failure frame.

        Raises:
            UnicodeDecodeError: If a bytes value in ``data`` is not UTF-8.
        """  # noqa: DOC502
        payload = build_json_payload(data)
        encodable_message = message.encode("utf-8", "backslashreplace").decode("utf-8")
        error = ErrorObject(code=code, message=encodable_message, data=payload)

        return FailureFrame(jsonrpc=JSONRPC_VERSION, id=correlation_id, error=error)

    async def write(self, writer: asyncio.StreamWriter, frame: OutgoingFrame) -> None:
        """Write one length-prefixed frame and flush it.

        The prefix and body go out in one call, so concurrent writers on the
        shared socket never interleave a partial frame. A frame whose body
        exceeds ``MAXIMUM_FRAME_BYTES`` is refused before anything is written,
        so the connection stays usable.

        Args:
            writer: Stream writer of the connection.
            frame: Frame to send.

        Raises:
            FrameTooLargeError: If the frame body exceeds ``MAXIMUM_FRAME_BYTES``.
            ValueError: If the frame holds an infinite or NaN number, or text
                that cannot be encoded as UTF-8.
            ConnectionError: If the peer closed the connection.
        """  # noqa: DOC502
        body = json.dumps(frame.model_dump(), allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode()

        if len(body) > MAXIMUM_FRAME_BYTES:
            raise FrameTooLargeError(len(body))

        writer.write(struct.pack(LENGTH_PREFIX_FORMAT, len(body)) + body)
        await writer.drain()

    async def read(self, reader: asyncio.StreamReader) -> Envelope:
        """Read one length-prefixed frame.

        Args:
            reader: Stream reader of the connection.

        Returns:
            The frame as an envelope, its payload not yet validated.

        Raises:
            ConnectionClosedError: If the peer closed the connection before
                the next frame began.
            FrameTooLargeError: If the frame exceeds ``MAXIMUM_FRAME_BYTES``.
            MalformedFrameError: If the frame body is not a JSON-RPC message.
            FrameError: If the peer closed the connection partway through a
                frame.
        """
        try:
            header = await reader.readexactly(LENGTH_PREFIX_BYTES)
        except asyncio.IncompleteReadError as error:
            if not error.partial:
                raise ConnectionClosedError("Connection closed") from error

            raise FrameError("Connection closed while reading frame header") from error

        (length,) = struct.unpack(LENGTH_PREFIX_FORMAT, header)

        if length > MAXIMUM_FRAME_BYTES:
            raise FrameTooLargeError(length)

        try:
            body = await reader.readexactly(length)
        except asyncio.IncompleteReadError as error:
            raise FrameError("Connection closed while reading frame body") from error

        try:
            return Envelope.model_validate_json(body)
        except ValidationError as error:
            raise MalformedFrameError(f"Malformed frame body: {error}") from error
