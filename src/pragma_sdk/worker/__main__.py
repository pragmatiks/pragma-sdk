"""Entry point of ``python -m pragma_sdk.worker --socket-descriptor <number>``.

The worker takes over the inherited descriptor, loads the declared provider
and imports its package, then serves the host over that socket. It sends its
first frame only after the provider has loaded. Exits with
status 1 when the descriptor does not hold a connected Unix stream socket,
loading fails, the handshake does not complete, the host sends a frame that
cannot be read, or serving fails unexpectedly, a provider task calling
``sys.exit()`` included, and with status 2 when the command line names no
usable descriptor. Log events go to stderr as JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import os
import socket
from contextlib import closing, suppress
from functools import partial
from typing import Any, Literal

import structlog
from packaging.utils import canonicalize_name

from pragma_sdk.declaration.provider import DeclaredProvider, load_declared_provider, load_package_catalog_names
from pragma_sdk.declaration.settings import load_provider_settings
from pragma_sdk.diagnostics.structured_logging import configure_logging
from pragma_sdk.protocol.codec import FrameError, JsonRpcCodec
from pragma_sdk.protocol.pending import PendingResponses
from pragma_sdk.worker.executor import RequestScopedExecutor
from pragma_sdk.worker.handlers import RequestHandlers
from pragma_sdk.worker.resource_builder import ResourceBuilder
from pragma_sdk.worker.runtime_context import RuntimeContextProxy
from pragma_sdk.worker.session import HandshakeError, WorkerSession


logger = structlog.get_logger()

LOWEST_SOCKET_DESCRIPTOR = 3

type SocketDescriptorFailureReason = Literal["not_open", "wrong_socket_kind", "not_connected"]


class SocketDescriptorError(OSError):
    """Raised when the inherited descriptor does not hold the host's socket.

    Attributes:
        reason: Why the descriptor was refused: it is ``not_open``, it holds
            something that is not a Unix stream socket
            (``wrong_socket_kind``), or the socket it holds is
            ``not_connected``.
        fields: Structured detail of the refusal, safe to log.
    """

    def __init__(self, reason: SocketDescriptorFailureReason, message: str, **fields: Any) -> None:
        """Initialize the error.

        Args:
            reason: Why the descriptor was refused.
            message: Human-readable description of the refusal.
            **fields: Structured detail of the refusal, safe to log.
        """
        self.reason = reason
        self.fields = fields
        super().__init__(message)


def parse_socket_descriptor() -> int:
    """Read the number of the inherited host socket descriptor from the command line.

    Returns:
        Number of the descriptor the host passed its end of the connected
        socket pair on, at least :data:`LOWEST_SOCKET_DESCRIPTOR`, so the
        worker never takes over one of its own standard streams.

    Raises:
        SystemExit: With status 2, the usage on stderr, when the command line
            carries no ``--socket-descriptor`` number of at least
            :data:`LOWEST_SOCKET_DESCRIPTOR`.
    """  # noqa: DOC502
    parser = argparse.ArgumentParser(description="Pragmatiks provider worker")
    parser.add_argument("--socket-descriptor", type=int, required=True)
    arguments = parser.parse_args()

    if arguments.socket_descriptor < LOWEST_SOCKET_DESCRIPTOR:
        parser.error(
            f"--socket-descriptor must name a descriptor of {LOWEST_SOCKET_DESCRIPTOR} or more, "
            f"not {arguments.socket_descriptor}, which a standard stream holds."
        )

    return arguments.socket_descriptor


def adopt_host_socket(descriptor: int) -> socket.socket:
    """Take over the connected host socket the inherited descriptor holds.

    The socket is non-inheritable from here on, so a child an ``exec`` starts
    later never holds the host connection.

    Args:
        descriptor: Number of the descriptor the host passed the socket on.

    Returns:
        The connected Unix stream socket, non-inheritable.

    Raises:
        SocketDescriptorError: If the descriptor is not open, does not hold a
            socket, or does not hold a connected Unix stream socket.
    """
    try:
        host_socket = socket.socket(fileno=descriptor)
    except OSError as error:
        if error.errno == errno.EBADF:
            raise SocketDescriptorError("not_open", f"Descriptor {descriptor} is not open: {error}.") from error

        raise SocketDescriptorError(
            "wrong_socket_kind", f"Descriptor {descriptor} is not a socket: {error}.", errno=error.errno
        ) from error

    family = host_socket.family
    socket_type = host_socket.type

    if family != socket.AF_UNIX or socket_type != socket.SOCK_STREAM:
        host_socket.close()
        raise SocketDescriptorError(
            "wrong_socket_kind",
            f"Descriptor {descriptor} is not a Unix stream socket: family {family!r}, type {socket_type!r}.",
            family=int(family),
            socket_type=int(socket_type),
        )

    try:
        host_socket.getpeername()
    except OSError as error:
        host_socket.close()
        raise SocketDescriptorError(
            "not_connected", f"Descriptor {descriptor} holds an unconnected Unix stream socket: {error}."
        ) from error

    host_socket.set_inheritable(False)

    return host_socket


def load_provider() -> DeclaredProvider:
    """Load the provider ``PRAGMA_PROVIDER_DISTRIBUTION`` names, exiting when it cannot load.

    The normalized distribution name is bound to the structlog context as
    soon as the settings name it, so every later log event names it, a load
    failure included.

    Returns:
        The declared provider, its package imported.

    Raises:
        SystemExit: With status 1 when the settings are invalid, the provider
            cannot be loaded, or the provider package exits while it loads.
    """
    try:
        settings = load_provider_settings()
        structlog.contextvars.bind_contextvars(distribution=canonicalize_name(settings.distribution))

        return load_declared_provider(settings.distribution)
    except (Exception, SystemExit) as error:
        logger.exception("worker_startup_failed")
        raise SystemExit(1) from error


async def serve_host(host_socket: socket.socket, declared_provider: DeclaredProvider) -> None:
    """Serve the provider over the host socket until shutdown or disconnect, then close the connection.

    Args:
        host_socket: The connected Unix stream socket to the host. Ownership
            passes to the stream opened over it, which this function closes
            before it returns; the caller's own close is then a no-op.
        declared_provider: The provider to serve, its package imported.

    Raises:
        OSError: If the stream over the host socket cannot be opened.
        HandshakeError: If the handshake with the host does not complete.
        FrameError: If the host sends a frame that cannot be read.
    """  # noqa: DOC502
    executor = RequestScopedExecutor()
    asyncio.get_running_loop().set_default_executor(executor)
    reader, writer = await asyncio.open_unix_connection(sock=host_socket)
    codec = JsonRpcCodec()
    pending_responses = PendingResponses()
    session = WorkerSession(
        reader=reader,
        writer=writer,
        handshake_params=declared_provider.build_handshake_params(),
        codec=codec,
        request_handlers=RequestHandlers(declared_provider.resource_types, ResourceBuilder()),
        pending_responses=pending_responses,
        runtime_context_factory=partial(
            RuntimeContextProxy, writer=writer, pending_responses=pending_responses, codec=codec
        ),
        package_catalog_names_loader=load_package_catalog_names,
        executor=executor,
    )

    try:
        await session.serve()
    finally:
        writer.close()

        with suppress(ConnectionError):
            await writer.wait_closed()


def main() -> None:
    """Run the worker until the host shuts it down or disconnects.

    Raises:
        SystemExit: With status 2 when the command line names no usable
            descriptor, and with status 1 when that descriptor does not hold a
            connected Unix stream socket, the provider cannot load, the
            handshake does not complete, the host sends a frame that cannot be
            read, or serving fails unexpectedly, a provider task calling
            ``sys.exit()`` included.
    """
    configure_logging()
    socket_descriptor = parse_socket_descriptor()
    structlog.contextvars.bind_contextvars(pid=os.getpid(), socket_descriptor=socket_descriptor)

    try:
        host_socket = adopt_host_socket(socket_descriptor)
    except SocketDescriptorError as error:
        logger.error("worker_socket_descriptor_rejected", reason=error.reason, error=str(error), **error.fields)
        raise SystemExit(1) from error

    with closing(host_socket):
        declared_provider = load_provider()
        structlog.contextvars.bind_contextvars(
            distribution_version=declared_provider.distribution_version,
            sdk_version=declared_provider.sdk_version,
        )

        try:
            asyncio.run(serve_host(host_socket, declared_provider))
        except HandshakeError as error:
            logger.error("worker_handshake_failed", reason=error.reason, error=str(error), **error.fields)
            raise SystemExit(1) from error
        except FrameError as error:
            logger.error("worker_frame_rejected", error=str(error))
            raise SystemExit(1) from error
        except OSError as error:
            logger.error("worker_host_socket_failed", error=str(error))
            raise SystemExit(1) from error
        except (Exception, SystemExit) as error:
            logger.exception("worker_failed")
            raise SystemExit(1) from error


if __name__ == "__main__":
    main()
