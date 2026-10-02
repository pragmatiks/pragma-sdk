"""Correlation of outgoing requests with the responses that answer them."""

from __future__ import annotations

import asyncio
from typing import Any


class PendingResponses:
    """Correlation map from outgoing request ids to the futures awaiting their responses.

    One connection end uses one instance for the requests it sends. Ids are
    allocated monotonically, so concurrent requests never collide.

    Attributes:
        futures: Open futures keyed by correlation id.
        next_id: Next correlation id to allocate.
    """

    def __init__(self) -> None:
        """Initialize an empty map starting at id zero."""
        self.futures: dict[int, asyncio.Future[Any]] = {}
        self.next_id: int = 0

    def allocate_id(self) -> int:
        """Allocate a correlation id without registering a future for it.

        Returns:
            The new correlation id, never handed out before by this instance.
        """
        correlation_id = self.next_id
        self.next_id += 1

        return correlation_id

    def register(self) -> tuple[int, asyncio.Future[Any]]:
        """Allocate a correlation id and a future to await its response.

        Returns:
            The new correlation id and the future to await.

        Raises:
            RuntimeError: If called outside a running event loop.
        """  # noqa: DOC502
        correlation_id = self.allocate_id()

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.futures[correlation_id] = future

        return correlation_id, future

    def resolve(self, correlation_id: int, value: Any) -> bool:
        """Complete the future of a correlation id with a value.

        An unknown or already completed id is ignored.

        Args:
            correlation_id: Id of the request being answered.
            value: Value to deliver to the waiter.

        Returns:
            Whether a waiter received the value.
        """
        future = self.futures.pop(correlation_id, None)

        if future is None or future.done():
            return False

        future.set_result(value)

        return True

    def discard(self, correlation_id: int) -> None:
        """Forget a correlation id without completing its future.

        Args:
            correlation_id: Id of the request to forget.
        """
        self.futures.pop(correlation_id, None)

    def fail_all(self, error: BaseException) -> None:
        """Fail every open future with the same error and forget them all.

        Args:
            error: Exception to set on each open future.
        """
        for future in self.futures.values():
            if not future.done():
                future.set_exception(error)

        self.futures.clear()
