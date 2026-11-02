"""The worker's default executor, which records the work each request submits to it.

A request runs inside :func:`request_scope`. Work its task, or any task it
spawns, submits to the event loop's default executor while the scope is
active, through ``asyncio.to_thread``, ``loop.run_in_executor(None, ...)`` or
the loop's own ``getaddrinfo``, is recorded under the request's correlation
id until it ends. Work submitted outside any scope, and threads, processes or
executors a handler starts itself, are not recorded.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from typing import Any


request_correlation_id: ContextVar[int | None] = ContextVar("request_correlation_id", default=None)
"""Correlation id of the request whose scope is active, or ``None`` outside every request.

Only :func:`request_scope` sets it.
"""


@contextmanager
def request_scope(correlation_id: int) -> Generator[None]:
    """Attribute the executor work submitted within the block to one request.

    Tasks created within the block copy the scope, so work they submit is
    attributed to the request too.

    Args:
        correlation_id: Correlation id of the request.

    Yields:
        Nothing; the scope ends when the block exits.
    """
    token = request_correlation_id.set(correlation_id)

    try:
        yield
    finally:
        request_correlation_id.reset(token)


class RequestScopedExecutor(ThreadPoolExecutor):
    """Thread pool that records the work each request submits, so the worker can wait for it.

    Install it as the event loop's default executor with
    ``loop.set_default_executor``. :meth:`submit` also records the future
    under the active request scope until the work is done;
    :meth:`snapshot_work` lists a request's recorded work and
    :meth:`wait_for_work` waits for it.
    """

    def __init__(self) -> None:
        """Initialize the pool with no recorded work."""
        super().__init__(thread_name_prefix="asyncio")
        self.recorded_lock = threading.Lock()
        self.recorded_futures: dict[int, set[Future[Any]]] = {}

    def submit[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        """Schedule a callable on the pool, recording it under the active request's correlation id.

        Args:
            fn: Callable to run in a pool thread.
            *args: Positional arguments for ``fn``.
            **kwargs: Keyword arguments for ``fn``.

        Returns:
            The future of the work, recorded until it is done when a request
            scope is active.

        Raises:
            RuntimeError: If the pool is shut down, as ``ThreadPoolExecutor`` raises.
        """  # noqa: DOC502
        future = super().submit(fn, *args, **kwargs)
        correlation_id = request_correlation_id.get()

        if correlation_id is None:
            return future

        with self.recorded_lock:
            self.recorded_futures.setdefault(correlation_id, set()).add(future)

        future.add_done_callback(partial(self.forget_work, correlation_id))

        return future

    def forget_work(self, correlation_id: int, future: Future[Any]) -> None:
        """Stop recording one finished piece of work.

        Runs in the pool thread that finished the work, or in the thread
        that recorded it when the work was already done.

        Args:
            correlation_id: Correlation id the work is recorded under.
            future: The finished work.
        """
        with self.recorded_lock:
            futures = self.recorded_futures.get(correlation_id)

            if futures is None:
                return

            futures.discard(future)

            if not futures:
                del self.recorded_futures[correlation_id]

    def snapshot_work(self, correlation_id: int) -> list[Future[Any]]:
        """Copy the work still recorded under one request.

        Args:
            correlation_id: Correlation id of the request.

        Returns:
            The futures of the request's work that are not done yet.
        """
        with self.recorded_lock:
            return list(self.recorded_futures.get(correlation_id, ()))

    async def wait_for_work(self, correlation_id: int) -> int:
        """Wait until the work recorded under one request when the wait starts has ended.

        The work is read once, as the wait starts: work submitted under the
        request while it waits is not waited for, so the wait always ends
        once the work already submitted does. An error the work raised is
        never re-raised, and cancelling this wait leaves the work running.

        Args:
            correlation_id: Correlation id of the request.

        Returns:
            How many pieces of the waited-for work ran, leaving out work
            cancelled before it started.
        """
        futures = self.snapshot_work(correlation_id)

        if not futures:
            return 0

        awaited = [asyncio.wrap_future(future) for future in futures]
        await asyncio.wait(awaited)

        for wrapped in awaited:
            if not wrapped.cancelled():
                wrapped.exception()

        return sum(not future.cancelled() for future in futures)
