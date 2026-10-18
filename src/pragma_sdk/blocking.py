"""Blocking work run off the event loop on worker threads."""

from __future__ import annotations

import asyncio
import contextvars
import functools
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable


async def run_in_worker_thread[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a blocking function in a worker thread, and wait for the thread even when cancelled.

    A cancelled caller is cancelled only once the thread has returned,
    however often it is cancelled meanwhile, including by
    :func:`asyncio.run` cancelling every task on shutdown, so nothing the
    thread still uses is cleaned up under it. The function runs in a copy of
    the caller's context.

    Args:
        function: The blocking function.
        *args: Positional arguments passed to ``function``.
        **kwargs: Keyword arguments passed to ``function``.

    Returns:
        What ``function`` returns.

    Raises:
        asyncio.CancelledError: If the caller is cancelled, once the thread
            has returned; what the thread raised, if anything, is its
            ``__context__``.
        Exception: Whatever ``function`` raises, when the caller is not
            cancelled.

    Note:
        Under an anyio cancel scope, such as ``anyio.move_on_after`` or a
        cancelled task group, a cancelled caller keeps the event loop busy
        until the thread returns.
    """  # noqa: DOC502
    call_in_context = functools.partial(contextvars.copy_context().run, function, *args, **kwargs)
    worker_future = asyncio.get_running_loop().run_in_executor(None, call_in_context)
    cancellation: asyncio.CancelledError | None = None

    while not worker_future.done():
        try:
            await asyncio.wait({worker_future})
        except asyncio.CancelledError as error:
            cancellation = error

    if cancellation is not None:
        cancellation.__context__ = worker_future.exception()
        raise cancellation

    return worker_future.result()
