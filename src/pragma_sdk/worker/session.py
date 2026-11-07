"""The worker's session with the Pragmatiks host.

The session sends the handshake, then runs each request the host sends and
answers it, and sends a heartbeat at the interval the host names until the
session ends. Lifecycle methods' platform callbacks travel back to the host
over the same connection through the runtime context each dispatch is given.
A request the host cancels at its operation's deadline is answered with
``CANCELLED_CODE`` once the executor work its handler left has ended.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

import structlog
from packaging.utils import canonicalize_name
from pydantic import BaseModel, RootModel, ValidationError

from pragma_sdk.context import RuntimeContext, compute_defining_provider, package_catalog_names_scope
from pragma_sdk.declaration.provider import ProviderDeclarationError
from pragma_sdk.diagnostics.error_descriptions import build_error_message
from pragma_sdk.diagnostics.validation_errors import build_validation_error_details, build_validation_error_message
from pragma_sdk.exceptions import AlreadyExistsError
from pragma_sdk.models.enums import ProviderFailureCode
from pragma_sdk.protocol.codec import (
    ConnectionClosedError,
    FrameError,
    FrameTooLargeError,
    JsonRpcCodec,
    MalformedFrameError,
)
from pragma_sdk.protocol.constants import (
    ALREADY_EXISTS_CODE,
    CANCEL_METHOD,
    CANCELLED_CODE,
    DISPATCH_METHOD,
    HANDSHAKE_METHOD,
    HEARTBEAT_METHOD,
    INTERNAL_ERROR_CODE,
    INVALID_PARAMS_CODE,
    MAXIMUM_FRAME_BYTES,
    METHOD_NOT_FOUND_CODE,
    MIGRATE_METHOD,
    OBSERVE_METHOD,
    RESOURCE_HEALTH_METHOD,
    RESOURCE_LOGS_METHOD,
    SHUTDOWN_METHOD,
)
from pragma_sdk.protocol.frames import (
    CancelParams,
    DispatchParams,
    Envelope,
    FailureFrame,
    FrameKind,
    HandshakeParams,
    HandshakeResult,
    MigrateParams,
    ObserveParams,
    ResourcePayload,
    ResourceQueryParams,
    SuccessFrame,
)
from pragma_sdk.protocol.pending import PendingResponses
from pragma_sdk.types import EventType
from pragma_sdk.worker.executor import RequestScopedExecutor, request_scope
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

REQUEST_CANCELLED_MESSAGE = "The request was cancelled at its deadline."

type HandshakeFailureReason = Literal[
    "closed",
    "oversized",
    "unexpected_frame",
    "refused",
    "malformed_result",
    "version_not_offered",
    "distribution_not_cataloged",
    "distribution_not_loadable",
]


class HandshakeError(Exception):
    """Raised when the handshake with the host does not complete.

    Attributes:
        reason: Why the handshake failed: the host ``closed`` the connection,
            the worker's handshake was ``oversized``, the host sent an
            ``unexpected_frame`` or an unreadable one, ``refused`` the
            handshake, sent a ``malformed_result``, accepted a version the
            worker did not offer (``version_not_offered``), named no catalog
            name for the distribution the worker serves
            (``distribution_not_cataloged``), or named a catalog name for a
            distribution the worker cannot load as a provider
            (``distribution_not_loadable``).
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
class AcceptedHandshake:
    """What a session serves with once the host accepted its handshake.

    Attributes:
        protocol_version: Protocol version the connection speaks from now on.
        package_catalog_names: Provider catalog name of the package of the
            served distribution and of every provider distribution it
            embeds, keyed by package.
        heartbeat_interval_seconds: Seconds between two heartbeats the
            worker sends.
    """

    protocol_version: int
    package_catalog_names: dict[str, str]
    heartbeat_interval_seconds: float


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


class MethodNotFoundError(Exception):
    """Raised by the route of a request for a method the worker does not serve."""

    def __init__(self, method: str) -> None:
        """Name the method the worker does not serve.

        Args:
            method: Method name of the request.
        """
        super().__init__(f"Method {method!r} is not served.")


class UnknownMethodParams(RootModel[Any]):
    """Params of a request for a method the worker does not serve, accepted as they are."""


def build_unknown_method_route(method: str) -> RequestRoute:
    """Build the route answering a request for a method the worker does not serve.

    Its params are not checked, its handler raises :class:`MethodNotFoundError`,
    answered with ``METHOD_NOT_FOUND_CODE`` and logged as ``request_method_unknown``.

    Args:
        method: Method name of the request.

    Returns:
        The route refusing the method.
    """
    return RequestRoute(
        params_model=UnknownMethodParams,
        failure_log_event="request_method_unknown",
        logs_start=False,
        log_fields=lambda _params: {},
        handler=partial(refuse_unknown_method, method),
    )


async def refuse_unknown_method(method: str, _params: Any) -> None:
    """Refuse a request for a method the worker does not serve.

    Args:
        method: Method name of the request.
        _params: The request's params, ignored.

    Raises:
        MethodNotFoundError: Always.
    """
    raise MethodNotFoundError(method)


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
        package_catalog_names_loader: Callable[[dict[str, str]], dict[str, str]],
        executor: RequestScopedExecutor,
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
            package_catalog_names_loader: Turns the provider catalog name of
                each distribution, keyed by normalized distribution name, into
                the catalog name of each distribution's package; raises
                ``ProviderDeclarationError`` for a distribution it cannot load.
            executor: The event loop's default executor, which records the
                work each request submits to it.
        """
        self.reader = reader
        self.writer = writer
        self.handshake_params = handshake_params
        self.codec = codec
        self.request_handlers = request_handlers
        self.pending_responses = pending_responses
        self.runtime_context_factory = runtime_context_factory
        self.package_catalog_names_loader = package_catalog_names_loader
        self.executor = executor
        self.request_tasks: dict[int, asyncio.Task[None]] = {}
        self.cancelled_correlation_ids: set[int] = set()
        self.answered_correlation_ids: set[int] = set()
        self.request_routes: dict[str, RequestRoute] = {
            MIGRATE_METHOD: RequestRoute(
                params_model=MigrateParams,
                failure_log_event="migration_failed",
                logs_start=True,
                log_fields=build_migration_log_fields,
                handler=request_handlers.run_migration,
            ),
            OBSERVE_METHOD: RequestRoute(
                params_model=ObserveParams,
                failure_log_event="observe_failed",
                logs_start=True,
                log_fields=self.build_targeted_log_fields,
                handler=request_handlers.run_observe,
            ),
            RESOURCE_LOGS_METHOD: RequestRoute(
                params_model=ResourceQueryParams,
                failure_log_event="resource_logs_failed",
                logs_start=False,
                log_fields=self.build_targeted_log_fields,
                handler=request_handlers.run_resource_logs,
            ),
            RESOURCE_HEALTH_METHOD: RequestRoute(
                params_model=ResourceQueryParams,
                failure_log_event="resource_health_failed",
                logs_start=False,
                log_fields=self.build_targeted_log_fields,
                handler=request_handlers.run_resource_health,
            ),
        }

    async def serve(self) -> None:
        """Perform the handshake and serve requests until shutdown or disconnect.

        Every request is served with the accepted handshake's package
        catalog names bound, so a resource's ``provider`` is the catalog name
        of the distribution defining its class. From the accepted handshake
        on, a heartbeat goes out every ``heartbeat_interval_seconds`` the
        host named; a heartbeat that cannot be written ends serving, as a
        disconnect does. Requests still running when serving ends are
        cancelled and not answered. The caller owns the connection and
        closes it.

        Raises:
            HandshakeError: If the handshake with the host does not complete.
            FrameError: If the host sends a frame that cannot be read: a
                malformed or oversized frame, or one cut off by a disconnect.
        """  # noqa: DOC502
        try:
            accepted_handshake = await self.perform_handshake()
            logger.info(
                "worker_serving",
                protocol_version=accepted_handshake.protocol_version,
                package_catalog_names=accepted_handshake.package_catalog_names,
                heartbeat_interval_seconds=accepted_handshake.heartbeat_interval_seconds,
            )

            with package_catalog_names_scope(accepted_handshake.package_catalog_names):
                await self.serve_frames(accepted_handshake.heartbeat_interval_seconds)
        finally:
            await self.cancel_request_tasks()

    async def perform_handshake(self) -> AcceptedHandshake:
        """Send the handshake and read the host's answer, before any other frame.

        Returns:
            The accepted protocol version, and the provider catalog name of
            the package of each distribution the host names.

        Raises:
            HandshakeError: If the handshake exceeds ``MAXIMUM_FRAME_BYTES``,
                the host closes the connection, answers with another or an
                unreadable frame, refuses the handshake, sends a malformed
                result, accepts a version the worker did not offer, names no
                catalog name for the distribution the worker serves, or names
                one for a distribution the worker cannot load as a provider.
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

        distribution_catalog_names: dict[str, str] = {
            canonicalize_name(distribution): catalog_name
            for distribution, catalog_name in result.distribution_catalog_names.items()
        }

        if self.handshake_params.distribution not in distribution_catalog_names:
            raise HandshakeError(
                "distribution_not_cataloged",
                "Host accepted the handshake without a catalog name for the served distribution",
                distribution=self.handshake_params.distribution,
                cataloged_distributions=sorted(distribution_catalog_names),
            )

        return AcceptedHandshake(
            protocol_version=result.protocol_version,
            package_catalog_names=self.load_package_catalog_names(distribution_catalog_names),
            heartbeat_interval_seconds=result.heartbeat_interval_seconds,
        )

    def load_package_catalog_names(self, distribution_catalog_names: dict[str, str]) -> dict[str, str]:
        """Load the catalog name of each cataloged distribution's package, refusing the handshake when one cannot load.

        Args:
            distribution_catalog_names: Provider catalog name of each
                distribution the host names, keyed by normalized
                distribution name.

        Returns:
            The same catalog names, keyed by each distribution's package.

        Raises:
            HandshakeError: If a distribution is not installed, declares no
                loadable ``pragma.provider`` entry point, or declares the
                same package as another under a different catalog name.
        """
        try:
            return self.package_catalog_names_loader(distribution_catalog_names)
        except ProviderDeclarationError as error:
            raise HandshakeError(
                "distribution_not_loadable",
                f"Host named a catalog name for a distribution the worker cannot load as a provider: {error}",
                cataloged_distributions=sorted(distribution_catalog_names),
            ) from error

    async def serve_frames(self, heartbeat_interval_seconds: float) -> None:
        """Route the host's frames while sending heartbeats, until shutdown, disconnect or a failed heartbeat.

        Args:
            heartbeat_interval_seconds: Seconds between two heartbeats.

        Raises:
            FrameError: If the host sends a frame that cannot be read: a
                malformed or oversized frame, or one cut off by a disconnect.
        """  # noqa: DOC502
        receiving = asyncio.create_task(self.receive_frames(), name="receive_frames")
        heartbeating = asyncio.create_task(self.send_heartbeats(heartbeat_interval_seconds), name="send_heartbeats")

        try:
            await asyncio.wait((receiving, heartbeating), return_when=asyncio.FIRST_COMPLETED)
        finally:
            receiving.cancel()
            heartbeating.cancel()
            await asyncio.gather(receiving, heartbeating, return_exceptions=True)

        for task in (receiving, heartbeating):
            if not task.cancelled():
                task.result()

    async def send_heartbeats(self, interval_seconds: float) -> None:
        """Send a bare ``heartbeat`` notification every interval, until cancelled or a write fails.

        A heartbeat that cannot be written, because the connection is lost,
        is logged as ``heartbeat_write_failed`` and ends the loop.

        Args:
            interval_seconds: Seconds between two heartbeats; the first goes
                out one interval after the call.
        """
        heartbeat = self.codec.build_notification(HEARTBEAT_METHOD)

        while True:
            await asyncio.sleep(interval_seconds)

            try:
                await self.codec.write(self.writer, heartbeat)
            except OSError as error:
                logger.warning("heartbeat_write_failed", error=str(error))

                return

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
        """Route one frame by kind: resolve a response, start a request, cancel a request.

        Notifications other than shutdown and cancel, a cancel whose params
        are invalid, responses no callback awaits, and requests for methods
        the worker does not serve are ignored, each logged as a
        ``frame_ignored`` warning.

        Args:
            envelope: The frame read from the host.
        """
        match envelope.kind:
            case FrameKind.RESPONSE:
                self.resolve_response(envelope)

            case FrameKind.REQUEST:
                self.start_request(envelope)

            case FrameKind.NOTIFICATION if envelope.method == CANCEL_METHOD:
                self.receive_cancel(envelope)

            case FrameKind.NOTIFICATION:
                log_ignored_frame(envelope, "notification")

    def receive_cancel(self, envelope: Envelope) -> None:
        """Cancel the request a ``cancel`` notification names.

        Args:
            envelope: The cancel notification.
        """
        try:
            params = CancelParams.model_validate(envelope.params)
        except ValidationError:
            log_ignored_frame(envelope, "invalid_params")

            return

        self.cancel_request(params.correlation_id)

    def cancel_request(self, correlation_id: int) -> None:
        """Cancel a running request at its operation's deadline, so it is answered with ``CANCELLED_CODE``.

        An accepted cancel is logged as ``request_cancel_received``. A
        request whose task has not started yet is not interrupted; it skips
        its handler as it starts. A cancel for a request with no running
        task, one whose task is done, one already cancelled, or one whose
        response is being written is logged as
        ``cancel_ignored`` at debug level, with its ``reason``, and ignored.

        Args:
            correlation_id: Correlation id of the request to cancel.
        """
        task = self.request_tasks.get(correlation_id)
        ignored_reason = self.find_cancel_ignored_reason(correlation_id, task)

        if task is None or ignored_reason is not None:
            logger.debug("cancel_ignored", correlation_id=correlation_id, reason=ignored_reason)

            return

        logger.warning("request_cancel_received", correlation_id=correlation_id, method=task.get_name())
        self.cancelled_correlation_ids.add(correlation_id)

        if is_task_started(task):
            task.cancel()

    def find_cancel_ignored_reason(self, correlation_id: int, task: asyncio.Task[None] | None) -> str | None:
        """Find why a cancel for a request is to be ignored.

        Args:
            correlation_id: Correlation id the cancel names.
            task: The request's running task, or ``None`` when none runs.

        Returns:
            ``unknown_request`` when no task runs the request,
            ``already_done`` when its task is done, ``already_cancelled``
            when the host already cancelled it, ``already_answered`` when its
            response is being written, otherwise ``None``.
        """
        if task is None:
            return "unknown_request"

        if task.done():
            return "already_done"

        if correlation_id in self.cancelled_correlation_ids:
            return "already_cancelled"

        if correlation_id in self.answered_correlation_ids:
            return "already_answered"

        return None

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

        self.cancelled_correlation_ids.discard(correlation_id)
        self.answered_correlation_ids.discard(correlation_id)

        task = asyncio.create_task(self.run_request(correlation_id, method, envelope.params, route), name=method)
        self.request_tasks[correlation_id] = task
        task.add_done_callback(partial(self.forget_request_task, correlation_id))

    def find_request_route(self, correlation_id: int, method: str) -> RequestRoute:
        """Find how to run a request.

        Args:
            correlation_id: Correlation id of the request.
            method: Method name of the request.

        Returns:
            The route to run the request with; for a method the worker does
            not serve, a route answering ``METHOD_NOT_FOUND_CODE``.
        """
        if method == DISPATCH_METHOD:
            return self.build_dispatch_route(correlation_id)

        return self.request_routes.get(method) or build_unknown_method_route(method)

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
            log_fields=self.build_dispatch_log_fields,
            handler=partial(self.request_handlers.run_dispatch, context=context),
        )

    async def run_request(self, correlation_id: int, method: str, params: Any, route: RequestRoute) -> None:
        """Run a request and write its response to the host.

        Every event logged meanwhile, by the worker or the provider, carries
        the request's ``correlation_id`` and ``method``, and the work it
        submits to the default executor is recorded under its correlation id.

        A request the host cancels is answered with ``CANCELLED_CODE`` as
        :meth:`answer_cancel` describes, wherever the cancel lands: before
        the request starts, while its params are validated, while its
        handler runs, whether the handler then raises ``CancelledError``,
        returns or raises another error. A cancel that lands once its
        response is being written is ignored, so every request gets one
        answer.

        Args:
            correlation_id: Correlation id of the request.
            method: Method name of the request.
            params: The request's params, unvalidated.
            route: How to run the method.

        Raises:
            CancelledError: If the request is being cancelled for another
                reason than the host's cancel, such as the end of the
                session; it is then not answered.
        """  # noqa: DOC502
        started_at = time.monotonic()

        with (
            structlog.contextvars.bound_contextvars(correlation_id=correlation_id, method=method),
            request_scope(correlation_id),
        ):
            try:
                if correlation_id not in self.cancelled_correlation_ids:
                    await self.run_route(correlation_id, params, route, started_at)
            except asyncio.CancelledError:
                if not self.is_cancel_unanswered(correlation_id):
                    raise

            if self.is_cancel_unanswered(correlation_id):
                await self.answer_cancel(correlation_id, started_at)

    def is_cancel_unanswered(self, correlation_id: int) -> bool:
        """Tell whether the host cancelled a request that has no answer written yet.

        Args:
            correlation_id: Correlation id of the request.

        Returns:
            Whether the request is cancelled and unanswered.
        """
        return correlation_id in self.cancelled_correlation_ids and correlation_id not in self.answered_correlation_ids

    async def run_route(self, correlation_id: int, params: Any, route: RequestRoute, request_started_at: float) -> None:
        """Validate a request's params, run its handler and write the response.

        Invalid params are answered with ``INVALID_PARAMS_CODE``, described by
        field and message only, never by the rejected value. Every event
        logged while the handler runs and its response is written carries
        the route's log fields. Routes with ``logs_start`` log
        ``request_started`` as the handler starts. ``request_completed``,
        logged once the result is written, records how long the handler ran
        and its result took to encode. A handler that ends without raising
        ``CancelledError`` after the host cancelled its request is logged as
        ``request_cancel_swallowed`` and its response is not written. A
        cancel that lands while the handler runs is answered here, so
        ``request_cancelled`` carries the route's log fields too.

        Args:
            correlation_id: Correlation id of the request.
            params: The request's params, unvalidated.
            route: How to run the method.
            request_started_at: :func:`time.monotonic` reading taken as the request started.

        Raises:
            CancelledError: If the request is being cancelled for another
                reason than the host's cancel, such as the end of the
                session; it is then not answered.
        """  # noqa: DOC502
        try:
            validated_params = route.params_model.model_validate(params)
        except ValidationError as error:
            logger.error("request_params_invalid", errors=build_validation_error_details(error))
            message = build_validation_error_message(error)
            failure = self.codec.build_failure(correlation_id, INVALID_PARAMS_CODE, message)
            self.answered_correlation_ids.add(correlation_id)
            await self.write_response(correlation_id, failure)

            return

        with structlog.contextvars.bound_contextvars(**route.log_fields(validated_params)):
            if route.logs_start:
                logger.info("request_started")

            started_at = time.monotonic()

            try:
                response = await self.run_handler(correlation_id, validated_params, route, started_at)
            except asyncio.CancelledError:
                if correlation_id not in self.cancelled_correlation_ids:
                    raise

                await self.answer_cancel(correlation_id, request_started_at)

                return

            duration_seconds = measure_elapsed_seconds(started_at)

            if correlation_id in self.cancelled_correlation_ids:
                logger.warning("request_cancel_swallowed", duration_seconds=duration_seconds)
                await self.answer_cancel(correlation_id, request_started_at)

                return

            self.answered_correlation_ids.add(correlation_id)

            written = await self.write_response(correlation_id, response)

            if written and isinstance(response, SuccessFrame):
                logger.info("request_completed", duration_seconds=duration_seconds)

    async def run_handler(
        self, correlation_id: int, validated_params: BaseModel, route: RequestRoute, started_at: float
    ) -> SuccessFrame | FailureFrame:
        """Run a request's handler on its validated params and build the frame answering it.

        A create dispatch whose ``on_create``, or an observe whose
        ``on_observe``, raises
        :class:`~pragma_sdk.exceptions.AlreadyExistsError`, alone or inside an
        exception group, is answered with ``ALREADY_EXISTS_CODE`` and that
        error's message. Any other failure, also ``CancelledError`` while the
        request is not being cancelled, or ``sys.exit()`` called in the
        handler's own task, is answered with ``INTERNAL_ERROR_CODE`` and a
        message that repeats no rejected input or URL secret. Either is
        logged as the route's failure event. ``sys.exit()`` called in a task
        the handler starts ends the worker instead.

        Args:
            correlation_id: Correlation id of the request.
            validated_params: The request's validated params.
            route: How to run the method.
            started_at: :func:`time.monotonic` reading taken as the handler started.

        Returns:
            The success or failure frame answering the request.

        Raises:
            CancelledError: If the request is being cancelled, by the host's
                cancel or for another reason, such as the end of the session.
        """  # noqa: DOC502
        try:
            result = await route.handler(validated_params)
        except SystemExit:
            logger.exception(route.failure_log_event, duration_seconds=measure_elapsed_seconds(started_at))

            return self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, HANDLER_EXITED_MESSAGE)
        except (asyncio.CancelledError, Exception) as error:
            if isinstance(error, asyncio.CancelledError) and is_cancellation_requested():
                raise

            return self.build_handler_failure(correlation_id, validated_params, route, started_at, error)

        return self.encode_result(correlation_id, result)

    async def answer_cancel(self, correlation_id: int, started_at: float) -> None:
        """Answer a request the host cancelled with ``CANCELLED_CODE``, once the executor work it left has ended.

        The work waited for is what the request recorded in the default
        executor by the time its handler ended; the wait is logged as
        ``request_cancelled``.

        Args:
            correlation_id: Correlation id of the cancelled request.
            started_at: :func:`time.monotonic` reading taken as the request started.

        Raises:
            CancelledError: If the request is also being cancelled for
                another reason, such as the end of the session; it is then
                not answered.
        """
        self.answered_correlation_ids.add(correlation_id)

        if not self.consume_cancel():
            raise asyncio.CancelledError

        await self.wait_for_cancelled_work(correlation_id, started_at)
        await self.write_response(correlation_id, self.build_cancelled_failure(correlation_id))

    def consume_cancel(self) -> bool:
        """Withdraw the cancellation the host's cancel requested of the running request task.

        Returns:
            Whether nothing else, such as the end of the session, is
            cancelling the task too.
        """
        task = asyncio.current_task()

        if task is None or task.cancelling() == 0:
            return True

        return task.uncancel() == 0

    async def wait_for_cancelled_work(self, correlation_id: int, started_at: float) -> None:
        """Wait for the executor work a cancelled request left, logged as ``request_cancelled``.

        Args:
            correlation_id: Correlation id of the cancelled request.
            started_at: :func:`time.monotonic` reading taken as the request started.
        """
        executor_work_items = await self.executor.wait_for_work(correlation_id)
        logger.warning(
            "request_cancelled",
            duration_seconds=measure_elapsed_seconds(started_at),
            executor_work_items=executor_work_items,
        )

    def build_cancelled_failure(self, correlation_id: int) -> FailureFrame:
        """Build the frame answering a request the host cancelled.

        Args:
            correlation_id: Correlation id of the cancelled request.

        Returns:
            A failure with ``CANCELLED_CODE``.
        """
        return self.codec.build_failure(correlation_id, CANCELLED_CODE, REQUEST_CANCELLED_MESSAGE)

    def build_handler_failure(
        self,
        correlation_id: int,
        validated_params: BaseModel,
        route: RequestRoute,
        started_at: float,
        error: BaseException,
    ) -> FailureFrame:
        """Build the failure frame answering a request whose handler raised, logging the route's failure event.

        Args:
            correlation_id: Correlation id of the request.
            validated_params: The request's validated params.
            route: How the method was run.
            started_at: :func:`time.monotonic` reading taken as the handler started.
            error: What the handler raised.

        Returns:
            A failure with ``METHOD_NOT_FOUND_CODE`` for a method the worker
            does not serve, with ``ALREADY_EXISTS_CODE`` for a create dispatch
            refused because its object already exists, or an observe that
            found an object this resource did not create, otherwise with
            ``INTERNAL_ERROR_CODE``.
        """
        duration_seconds = measure_elapsed_seconds(started_at)

        if isinstance(error, MethodNotFoundError):
            logger.warning(route.failure_log_event, duration_seconds=duration_seconds)

            return self.codec.build_failure(correlation_id, METHOD_NOT_FOUND_CODE, str(error))

        already_exists_error = (
            find_already_exists_error(error) if is_already_exists_answered(validated_params) else None
        )

        if already_exists_error is not None:
            logger.warning(
                route.failure_log_event,
                failure_code=ProviderFailureCode.ALREADY_EXISTS,
                duration_seconds=duration_seconds,
                exc_info=already_exists_error,
            )

            return self.codec.build_failure(
                correlation_id, ALREADY_EXISTS_CODE, build_error_message(already_exists_error)
            )

        logger.error(route.failure_log_event, duration_seconds=duration_seconds, exc_info=error)

        return self.codec.build_failure(correlation_id, INTERNAL_ERROR_CODE, build_error_message(error))

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

    def build_dispatch_log_fields(self, params: DispatchParams) -> dict[str, Any]:
        """Pick the fields identifying what a dispatch acts on, for its log events.

        Args:
            params: The dispatch's validated params.

        Returns:
            Its event type, and the target's fields as
            :meth:`build_target_log_fields` picks them.
        """
        return {"event_type": params.event_type, **self.build_target_log_fields(params.target)}

    def build_targeted_log_fields(self, params: ObserveParams | ResourceQueryParams) -> dict[str, Any]:
        """Pick the fields identifying the resource an observe, logs or health request targets, for its log events.

        Args:
            params: The request's validated params.

        Returns:
            The target's fields as :meth:`build_target_log_fields` picks them.
        """
        return self.build_target_log_fields(params.target)

    def build_target_log_fields(self, target: ResourcePayload) -> dict[str, Any]:
        """Pick the fields identifying the resource a request acts on, for its log events.

        Must run with the session's package catalog names bound.

        Args:
            target: The resource a request acts on.

        Returns:
            Its project, resource type and name, and the catalog name of the
            provider defining its class as ``provider`` when the served
            provider defines its type.
        """
        resource_fields = build_resource_log_fields(target)
        resource_class = self.request_handlers.resource_types.get(target.resource)

        if resource_class is None:
            return resource_fields

        return {"provider": compute_defining_provider(resource_class), **resource_fields}

    def forget_request_task(self, correlation_id: int, task: asyncio.Task[None]) -> None:
        """Stop tracking a finished request task, and its cancel, unless another task took over its correlation id.

        Args:
            correlation_id: Correlation id of the request.
            task: The finished task.
        """
        if self.request_tasks.get(correlation_id) is task:
            del self.request_tasks[correlation_id]
            self.cancelled_correlation_ids.discard(correlation_id)
            self.answered_correlation_ids.discard(correlation_id)


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
        reason: Why it was dropped: ``notification``, ``invalid_params``
            (a cancel whose params are invalid), ``unsolicited_response``,
            or ``missing_id``.
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


def is_task_started(task: asyncio.Task[None]) -> bool:
    """Check whether a task has started running its coroutine.

    A task cancelled before it starts ends without running any of its
    coroutine, so nothing in it can answer the cancel.

    Args:
        task: The task to check.

    Returns:
        False while the task's coroutine has not run its first step.
    """
    coroutine = task.get_coro()

    return not isinstance(coroutine, Coroutine) or inspect.getcoroutinestate(coroutine) != inspect.CORO_CREATED


def measure_elapsed_seconds(started_at: float) -> float:
    """Measure the seconds elapsed since a monotonic clock reading, to the millisecond.

    Args:
        started_at: A :func:`time.monotonic` reading.

    Returns:
        The elapsed seconds, rounded to three decimals.
    """
    return round(time.monotonic() - started_at, 3)


def is_already_exists_answered(params: BaseModel) -> bool:
    """Tell whether a request answers an ``AlreadyExistsError`` with ``ALREADY_EXISTS_CODE``.

    Args:
        params: The request's validated params.

    Returns:
        Whether the request is an observe, or a dispatch whose event type
        is ``CREATE``.
    """
    if isinstance(params, ObserveParams):
        return True

    return isinstance(params, DispatchParams) and params.event_type == EventType.CREATE


def find_already_exists_error(error: BaseException) -> AlreadyExistsError | None:
    """Find the :class:`~pragma_sdk.exceptions.AlreadyExistsError` a handler raised, alone or inside an exception group.

    An error that only chains one as its cause or context does not count:
    the provider chose to raise something else.

    Args:
        error: What the handler raised.

    Returns:
        The error itself, the first one an exception group holds at any
        depth, or ``None`` when there is none.
    """
    if isinstance(error, AlreadyExistsError):
        return error

    if not isinstance(error, BaseExceptionGroup):
        return None

    matching_group = error.subgroup(AlreadyExistsError)

    if matching_group is None:
        return None

    return find_already_exists_error(matching_group.exceptions[0])
