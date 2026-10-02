"""Entry point of ``python -m pragma_sdk.worker --socket <path>``.

Reads the settings, loads the declared provider and imports its package
before connecting, so a provider that cannot load exits without ever
reaching the host. Exits with status 1 when loading fails, the host socket
cannot be reached, the handshake does not complete, the host sends a frame
that cannot be read, or serving fails unexpectedly, a provider task calling
``sys.exit()`` included. Log events go to stderr as JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import suppress
from functools import partial

import structlog

from pragma_sdk.declaration.provider import DeclaredProvider, load_declared_provider, normalize_distribution_name
from pragma_sdk.declaration.settings import load_provider_settings
from pragma_sdk.diagnostics.structured_logging import configure_logging
from pragma_sdk.protocol.codec import FrameError, JsonRpcCodec
from pragma_sdk.protocol.pending import PendingResponses
from pragma_sdk.worker.handlers import RequestHandlers
from pragma_sdk.worker.resource_builder import ResourceBuilder
from pragma_sdk.worker.runtime_context import RuntimeContextProxy
from pragma_sdk.worker.session import HandshakeError, WorkerSession


logger = structlog.get_logger()


def parse_socket_path() -> str:
    """Read the host socket path from the command line.

    Returns:
        Filesystem path of the host's Unix socket.
    """
    parser = argparse.ArgumentParser(description="Pragmatiks provider worker")
    parser.add_argument("--socket", required=True)
    arguments = parser.parse_args()

    return arguments.socket


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
        structlog.contextvars.bind_contextvars(distribution=normalize_distribution_name(settings.distribution))

        return load_declared_provider(settings.distribution)
    except (Exception, SystemExit) as error:
        logger.exception("worker_startup_failed")
        raise SystemExit(1) from error


async def serve_host(socket_path: str, declared_provider: DeclaredProvider) -> None:
    """Connect to the host socket and serve the provider until shutdown or disconnect, then close the connection.

    Args:
        socket_path: Filesystem path of the host's Unix socket.
        declared_provider: The provider to serve, its package imported.

    Raises:
        OSError: If the host socket cannot be reached.
        HandshakeError: If the handshake with the host does not complete.
        FrameError: If the host sends a frame that cannot be read.
    """  # noqa: DOC502
    reader, writer = await asyncio.open_unix_connection(socket_path)
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
        SystemExit: With status 1 when the provider cannot load, the host
            socket cannot be reached, the handshake does not complete, the
            host sends a frame that cannot be read, or serving fails
            unexpectedly, a provider task calling ``sys.exit()`` included.
    """
    configure_logging()
    socket_path = parse_socket_path()
    structlog.contextvars.bind_contextvars(pid=os.getpid(), socket=socket_path)

    declared_provider = load_provider()
    structlog.contextvars.bind_contextvars(
        distribution_version=declared_provider.distribution_version,
        sdk_version=declared_provider.sdk_version,
    )

    try:
        asyncio.run(serve_host(socket_path, declared_provider))
    except HandshakeError as error:
        logger.error("worker_handshake_failed", reason=error.reason, error=str(error), **error.fields)
        raise SystemExit(1) from error
    except FrameError as error:
        logger.error("worker_frame_rejected", error=str(error))
        raise SystemExit(1) from error
    except OSError as error:
        logger.error("worker_connect_failed", error=str(error))
        raise SystemExit(1) from error
    except (Exception, SystemExit) as error:
        logger.exception("worker_failed")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
