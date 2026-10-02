"""The worker's session with the Pragmatiks host.

The session sends the handshake, then runs each request the host sends and
answers it. Lifecycle methods' platform callbacks travel back to the host
over the same connection through the runtime context each dispatch is given.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ValidationError

from pragma_sdk.context import RuntimeContext
from pragma_sdk.diagnostics.error_descriptions import build_error_message
from pragma_sdk.diagnostics.validation_errors import build_validation_error_details, build_validation_error_message
from pragma_sdk.protocol.codec import (
    ConnectionClosedError,
    FrameError,
    FrameTooLargeError,
    JsonRpcCodec,
    MalformedFrameError,
)
from pragma_sdk.protocol.constants import (
    DISPATCH_METHOD,
    HANDSHAKE_METHOD,
    INTERNAL_ERROR_CODE,
    INVALID_PARAMS_CODE,
    MAXIMUM_FRAME_BYTES,
    MIGRATE_METHOD,
    RESOURCE_HEALTH_METHOD,
    RESOURCE_LOGS_METHOD,
    SHUTDOWN_METHOD,
)
from pragma_sdk.protocol.frames import (
    DispatchParams,
    Envelope,
    FailureFrame,
    FrameKind,
    HandshakeParams,
    HandshakeResult,
    MigrateParams,
    ResourcePayload,
    ResourceQueryParams,
    SuccessFrame,
)
from pragma_sdk.protocol.pending import PendingResponses
from pragma_sdk.worker.handlers import RequestHandlers


logger = structlog.get_logger()

RESULT_NOT_SERIALIZABLE_MESSAGE = (
    "The handler returned a result that cannot be serialized as JSON. Return only JSON-serializable values: "
    "finite numbers, UTF-8 bytes and valid Unicode text."
)

RESULT_TOO_LARGE_MESSAGE = (
    f"The handler returned a result larger than {MAXIMUM_FRAME_BYTES // (1024 * 1024)} MiB. Return smaller outputs."
)

HANDLER_EXITED_MESSAGE = "The handler called sys.exit(). Return a result or raise an exception instead."

HANDSHAKE_CLOSED_MESSAGE = "Host closed the connection during the handshake"

type HandshakeFailureReason = Literal[
    "closed", "oversized", "unexpected_frame", "refused", "malformed_result", "version_not_offered"
]


class HandshakeError(Exception):
    """Raised when the handshake with the host does not complete.

    Attributes:
        reason: Why the handshake failed: the host ``closed`` the connection,
            the worker's handshake was ``oversized``, the host sent an
            ``unexpected_frame`` or an unreadable one, ``refused`` the
            handshake, sent a ``malformed_result``, or accepted a version the
            worker did not offer (``version_not_offered``).
        fields: Structured detail of the failure, safe to log.
    """

    def __init__(self, reason: HandshakeFailureReason, message: str, **fields: Any) -> None:
        """Initialize the error.

        Args:
            reason: Why the handshake failed.
            message: Human-readable description of the failure.
            **fields: Structured detail of the failure, safe to log.
        """
        self.reason = reason
        self.fields = fields
        super().__init__(message)


@dataclass(frozen=True)
class RequestRoute:
    """How the worker runs one request method.

    Attributes:
        params_model: Model the request's params are validated against.
        failure_log_event: Log event recorded when the handler fails.
        logs_start: Whether ``request_started`` is logged as the handler
            starts; health and logs queries, polled often, are not.
        log_fields: Picks the fields identifying what the request acts on
            from its validated params; every event logged while the request
            runs carries them.
        handler: Coroutine function taking the validated params and returning
            the result to answer with.
    """

    params_model: type[BaseModel]
    failure_log_event: str
    logs_start: bool
    log_fields: Callable[[Any], dict[str, Any]]
    handler: Callable[[Any], Awaitable[Any]]


class WorkerSession:
    """The worker's session with the host over one connection, from handshake to shutdown or disconnect."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handshake_params: HandshakeParams,
        codec: JsonRpcCodec,
        request_handlers: RequestHandlers,
        pending_responses: PendingResponses,
        runtime_context_factory: Callable[[int], RuntimeContext],
    ) -> None:
        """Initialize the session on an open host connection.

        Args:
            reader: Stream reader of the host connection.
            writer: Stream writer of the host connection.
            handshake_params: The handshake the worker offers the host.
            codec: Codec building, writing and reading frames.
            request_handlers: Handlers running the provider's side of each request.
            pending_responses: Correlation map for the requests the worker sends.
            runtime_context_factory: Builds the runtime context serving one
                dispatch's platform callbacks, from the dispatch's correlation id.
        """
        self.reader = reader
        self.writer = writer
        self.handshake_params = handshake_params
        self.codec = codec
        self.request_handlers = request_handlers
        self.pending_responses = pending_responses
        self.runtime_context_factory = runtime_context_factory
        self.request_tasks: dict[int, asyncio.Task[None]] = {}
        self.request_routes: dict[str, RequestRoute] = {
            MIGRATE_METHOD: RequestRoute(
                params_model=MigrateParams,
                failure_log_event="migration_failed",
                logs_start=True,
                log_fields=build_migration_log_fields,
                handler=request_handlers.run_migration,
            ),
            RESOURCE_LOGS_METHOD: RequestRoute(
                params_model=ResourceQueryParams,
                failure_log_event="resource_logs_failed",
                logs_start=False,
                log_fields=build_query_log_fields,
                handler=request_handlers.run_resource_logs,
            ),
            RESOURCE_HEALTH_METHOD: RequestRoute(
                params_model=ResourceQueryParams,
                failure_log_event="resource_health_failed",
                logs_start=False,
                log_fields=build_query_log_fields,
                handler=request_handlers.run_resource_health,
            ),
        }

    async def serve(self) -> None:
        """Perform the handshake and serve requests until shutdown or disconnect.

        Requests still running when serving ends are cancelled. The caller
        owns the connection and closes it.

        Raises:
            HandshakeError: If the handshake with the host does not complete.
            FrameError: If the host sends a frame that cannot be read: a
                malformed or oversized frame, or one cut off by a disconnect.
        """  # noqa: DOC502
        try:
            protocol_version = await self.perform_handshake()
            logger.info("worker_serving", protocol_version=protocol_version)
            await self.receive_frames()
        finally:
            await self.cancel_request_tasks()

    async def perform_handshake(self) -> int:
        """Send the handshake and read the host's answer, before any other frame.

        Returns:
            The protocol version the host accepted.

        Raises:
            HandshakeError: If the handshake exceeds ``MAXIMUM_FRAME_BYTES``,
                the host closes the connection, answers with another or an
                unreadable frame, refuses the handshake, sends a malformed
                result, or accepts a version the worker did not offer.
        """
        correlation_id = self.pending_responses.allocate_id()
        request = self.codec.build_request(correlation_id, HANDSHAKE_METHOD, self.handshake_params)

        try:
            await self.codec.write(self.writer, request)
        except FrameTooLargeError as error:
            raise HandshakeError(
                "oversized",
                "Worker handshake exceeds the maximum frame size",
                length=error.length,
            ) from error
        except ConnectionError as error:
            raise HandshakeError("closed", HANDSHAKE_CLOSED_MESSAGE, cause=str(error)) from error

        try:
            envelope = await self.codec.read(self.reader)
        except (MalformedFrameError, FrameTooLargeError) as error:
            raise HandshakeError(
                "unexpected_frame",
                "Host answered the handshake with an unreadable frame",
                cause=str(error),
            ) from error
        except (FrameError, ConnectionError) as error:
            raise HandshakeError("closed", HANDSHAKE_CLOSED_MESSAGE, cause=str(error)) from error

        if envelope.kind is not FrameKind.RESPONSE or envelope.id != correlation_id:
            raise HandshakeError(
                "unexpected_frame",
                "Host answered the handshake with an unexpected frame",
                kind=envelope.kind,
                method=envelope.method,
            )

        if envelope.error is not None:
            raise HandshakeError(
                "refused",
                f"Host refused the handshake: {envelope.error.message}",
                code=envelope.error.code,
                host_message=envelope.error.message,
                data=envelope.error.data,
                protocol_versions=self.handshake_params.protocol_versions,
            )

        try:
            result = HandshakeResult.model_validate(envelope.result)
        except ValidationError as error:
            raise HandshakeError(
                "malformed_result",
                "Host accepted the handshake with a malformed result",
                errors=build_validation_error_details(error),
            ) from None

        if result.protocol_version not in self.handshake_params.protocol_versions:
            raise HandshakeError(
                "version_not_offered",
                "Host accepted a protocol version the worker did not offer",
                accepted_version=result.protocol_version,
                protocol_versions=self.handshake_params.protocol_versions,
            )

        return result.protocol_version

    async def receive_frames(self) -> None:
        """Read frames from the host and route them until shutdown or disconnect.

        Raises:
            FrameError: If the host sends a frame that cannot be read: a
                malformed or oversized frame, or one cut off by a disconnect.
        """  # noqa: DOC502
        while True:
            try:
                envelope = await self.codec.read(self.reader)
            except (ConnectionClosedError, ConnectionError) as error:
                logger.info("worker_disconnected", error=str(error))
                return

            if envelope.method == SHUTDOWN_METHOD:
                logger.info("worker_shutdown_requested")
                return

            self.route_frame(envelope)

    async def cancel_request_tasks(self) -> None:
        """Cancel and await every request still running."""
        if not self.request_tasks:
            return

        tasks = dict(self.request_tasks)
        logger.warning(
            "worker_requests_cancelled",
            count=len(tasks),
            methods=[task.get_name() for task in tasks.values()],
            correlation_ids=list(tasks),
        )

        for task in tasks.values():
            task.cancel()

        await asyncio.gather(*tasks.values(), return_exceptions=True)

    def route_frame(self, envelope: Envelope) -> None:
        """Route one frame by kind: resolve a response, start a request.

        Notifications other than shutdown, responses no callback awaits, and
        requests for methods the worker does not serve are ignored, each
        logged as a ``frame_ignored`` warning.

        Args:
            envelope: The frame read from the host.
        """
        match envelope.kind:
            case FrameKind.RESPONSE:
                self.resolve_response(envelope)

            case FrameKind.REQUEST:
                self.start_request(envelope)

            case FrameKind.NOTIFICATION:
                log_ignored_frame(envelope, "notification")

    def resolve_response(self, envelope: Envelope) -> None:
        """Hand a response to the callback awaiting it.

        Args:
            envelope: The response frame.
        """
        if envelope.id is None or not self.pending_responses.resolve(envelope.id, envelope):
            log_ignored_frame(envelope, "unsolicited_response")

    def start_request(self, envelope: Envelope) -> None:
        """Run a request in its own task, so requests run concurrently.

        Args:
            envelope: The request frame.
        """
        if envelope.id is None or envelope.method is None:
            log_ignored_frame(envelope, "missing_id")

            return

        correlation_id = envelope.id
        method = envelope.method
        route = self.find_request_route(correlation_id, method)

        if route is None:
            log_ignored_frame(envelope, "unknown_method")

            return

        task = asyncio.create_task(self.run_request(correlation_id, method, envelope.params, route), name=method)
        self.request_tasks[correlation_id] = task
        task.add_done_callback(partial(self.forget_request_task, correlation_id))

    def find_request_route(self, correlation_id: int, method: str) -> RequestRoute | None:
        """Find how to run a request.

        Args:
            correlation_id: Correlation id of the request.
            method: Method name of the request.

        Returns:
            The route to run the request with, or ``None`` when the worker
            does not serve the method.
        """
        if method == DISPATCH_METHOD:
            return self.build_dispatch_route(correlation_id)

        return self.request_routes.get(method)

    def build_dispatch_route(self, dispatch_id: int) -> RequestRoute:
        """Build the route of one dispatch, bound to a runtime context of its own.

        Args:
            dispatch_id: Correlation id of the dispatch.

        Returns:
            The route running the dispatch's lifecycle method.
        """
        context = self.runtime_context_factory(dispatch_id)

        return RequestRoute(
            params_model=DispatchParams,
            failure_log_event="dispatch_failed",
            logs_start=True,
            log_fields=build_dispatch_log_fields,
            handler=partial(self.request_handlers.run_dispatch, context=context),
        )

    async def run_request(self, correlation_id: int, method: str, params: Any, route: RequestRoute) -> None:
        """Run a request and write its response to the host.

        Every event logged meanwhile, by the worker or the provider, carries
        the request's ``correlation_id`` and ``method``.

        Args:
            correlation_id: Correlation id of the request.
            method: Method name of the request.
            params: The request's params, unvalidated.
            route: How to run the method.
        """
        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id, method=method):
            await self.run_route(correlation_id, params, route)

    async def run_route(self, correlation_id: int, params: Any, route: RequestRoute) -> None:
        """Validate a request's params, run its handler and write the response.

        Invalid params are answered with ``INVALID_PARAMS_CODE``, described by
        field and message only, never by the rejected value. Every event
        logged while the handler runs and its response is written carries
        the route's log fields. Routes with ``logs_start`` log
        ``request_started`` as the handler starts. ``request_completed``,
        logged once the result is written, records how long the handler ran
        and its result took to encode.

        Args:
            correlation_id: Correlation id of the request.
            params: The request's params, unvalidated.
            route: How to run the method.
        """
        try:
            validated_params = route.params_model.model_validate(params)
        except ValidationError as error:
            logger.error("request_params_invalid", errors=build_validation_error_details(error))
            message = build_validation_error_message(error)
            failure = self.codec.build_failure(correlation_id, INVALID_PARAMS_CODE, message)
            await self.write_response(correlation_id, failure)

            return

        with structlog.contextvars.bound_contextvars(**route.log_fields(validated_params)):
            if route.logs_start:
                logger.info("request_started")

            started_at = time.monotonic()
            response = await self.run_handler(correlation_id, validated_params, route, started_at)
            duration_seconds = measure_elapsed_seconds(started_at)
            written = await self.write_response(correlation_id, response)

            if written and isinstance(response, SuccessFrame):
                logger.info("request_completed", duration_seconds=duration_seconds)

    async def run_handler(
        self, correlation_id: int, validated_params: BaseModel, route: RequestRoute, started_at: float
    ) -> SuccessFrame | FailureFrame:
        """Run a request's handler on its validated params and build the frame answering it.

        A handler that raises, raises ``CancelledError`` while its request is
        not being cancelled, or calls ``sys.exit()`` in its own task, is
        answered with ``INTERNAL_ERROR_CODE`` and a message that repeats no
        rejected input or URL secret, and logged as the route's failure event
        with how long it ran. ``sys.exit()`` called in a task the handler
        starts ends the worker instead.

        Args:
            correlation_id: Correlation id of the request.
            validated_params: The request's validated params.
            route: How to run the method.
            started_at: :func:`time.monotonic` reading taken as the handler started.

        Returns:
            The success or failure frame answering the request.

        Raises:
            CancelledError: If the request is being cancelled.
        """  # noqa: DOC502
        try:
            result = await route.handler(validated_params)
        except SystemExit:
            logger.exception(route.failure_log_event, duration_seconds=measure_elapsed_seconds(started_at))

            return self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, HANDLER_EXITED_MESSAGE)
        except (asyncio.CancelledError, Exception) as error:
            if isinstance(error, asyncio.CancelledError) and is_cancellation_requested():
                raise

            logger.exception(route.failure_log_event, duration_seconds=measure_elapsed_seconds(started_at))

            return self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, build_error_message(error))

        return self.encode_result(correlation_id, result)

    def encode_result(self, correlation_id: int, result: Any) -> SuccessFrame | FailureFrame:
        """Build the success frame of a handler's result, or a failure when the result cannot be encoded.

        Args:
            correlation_id: Correlation id of the request.
            result: The handler's result.

        Returns:
            The success frame, or a failure frame with ``INTERNAL_ERROR_CODE``
            when converting the result raises, whatever the exception.
        """
        try:
            return self.codec.build_success(correlation_id, result)
        except Exception:
            logger.exception("response_encoding_failed")

            return self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, RESULT_NOT_SERIALIZABLE_MESSAGE)

    async def write_response(self, correlation_id: int, response: SuccessFrame | FailureFrame) -> bool:
        """Write a response, answering with a failure instead when it cannot be serialized or exceeds the frame limit.

        A response that cannot be written because the connection is lost is
        logged, not raised.

        Args:
            correlation_id: Correlation id of the request.
            response: The response to write.

        Returns:
            Whether the response itself was written; ``False`` when a failure
            was written in its place or the connection was lost.
        """
        try:
            await self.codec.write(self.writer, response)
        except FrameTooLargeError as error:
            logger.error("response_too_large", length=error.length)
            failure = self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, RESULT_TOO_LARGE_MESSAGE)
            await self.write_response(correlation_id, failure)

            return False
        except ValueError:
            logger.exception("response_encoding_failed")
            failure = self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, RESULT_NOT_SERIALIZABLE_MESSAGE)
            await self.write_response(correlation_id, failure)

            return False
        except OSError as error:
            logger.warning("response_write_failed", error=str(error))

            return False

        return True

    def forget_request_task(self, correlation_id: int, task: asyncio.Task[None]) -> None:
        """Stop tracking a finished request task, unless another task took over its correlation id.

        Args:
            correlation_id: Correlation id of the request.
            task: The finished task.
        """
        if self.request_tasks.get(correlation_id) is task:
            del self.request_tasks[correlation_id]


def build_dispatch_log_fields(params: DispatchParams) -> dict[str, Any]:
    """Pick the fields identifying what a dispatch acts on, for its log events.

    Args:
        params: The dispatch's validated params.

    Returns:
        Its event type and provider, and the target's project, resource type and name.
    """
    return {"event_type": params.event_type, "provider": params.provider, **build_resource_log_fields(params.target)}


def build_query_log_fields(params: ResourceQueryParams) -> dict[str, Any]:
    """Pick the fields identifying what a logs or health query acts on, for its log events.

    Args:
        params: The query's validated params.

    Returns:
        Its provider, and the target's project, resource type and name.
    """
    return {"provider": params.provider, **build_resource_log_fields(params.target)}


def build_migration_log_fields(params: MigrateParams) -> dict[str, Any]:
    """Pick the fields identifying what a migration acts on, for its log events.

    Args:
        params: The migration's validated params.

    Returns:
        Its resource type and direction.
    """
    return {"resource": params.resource, "direction": params.direction}


def build_resource_log_fields(target: ResourcePayload) -> dict[str, Any]:
    """Pick the fields identifying a resource, for log events.

    Args:
        target: The resource a request acts on.

    Returns:
        Its ``project_id``, ``resource`` type and ``name``.
    """
    return {"project_id": target.project_id, "resource": target.resource, "name": target.name}


def log_ignored_frame(envelope: Envelope, reason: str) -> None:
    """Log a frame the worker drops without answering it, as a ``frame_ignored`` warning.

    Args:
        envelope: The dropped frame.
        reason: Why it was dropped: ``notification``, ``unsolicited_response``,
            ``missing_id`` or ``unknown_method``.
    """
    logger.warning(
        "frame_ignored", reason=reason, correlation_id=envelope.id, method=envelope.method, kind=envelope.kind
    )


def is_cancellation_requested() -> bool:
    """Check whether the running task is being cancelled.

    Returns:
        True if :meth:`asyncio.Task.cancel` was called on the running task
        and the cancellation is still pending.
    """
    task = asyncio.current_task()

    return task is not None and task.cancelling() > 0


def measure_elapsed_seconds(started_at: float) -> float:
    """Measure the seconds elapsed since a monotonic clock reading, to the millisecond.

    Args:
        started_at: A :func:`time.monotonic` reading.

    Returns:
        The elapsed seconds, rounded to three decimals.
    """
    return round(time.monotonic() - started_at, 3)
