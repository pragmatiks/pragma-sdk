"""The runtime context through which a dispatch's platform callbacks reach the host."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from pragma_sdk.diagnostics.error_descriptions import build_error_message
from pragma_sdk.diagnostics.validation_errors import build_validation_error_message
from pragma_sdk.protocol.codec import FrameError, JsonRpcCodec
from pragma_sdk.protocol.constants import APPLY_RESOURCE_METHOD, WAIT_FOR_STATE_METHOD
from pragma_sdk.protocol.frames import (
    ApplyResourceParams,
    ChildResourcePayload,
    Envelope,
    WaitForStateParams,
    WaitForStateResult,
)
from pragma_sdk.protocol.pending import PendingResponses
from pragma_sdk.types import LifecycleState


logger = structlog.get_logger()


class RuntimeContextProxy:
    """Runtime context that forwards a dispatch's platform callbacks to the host.

    Implements the SDK ``RuntimeContext`` protocol. One proxy serves one
    dispatch, so the host scopes each callback to the dispatch that made it.
    """

    def __init__(
        self, dispatch_id: int, writer: asyncio.StreamWriter, pending_responses: PendingResponses, codec: JsonRpcCodec
    ) -> None:
        """Initialize a proxy for one dispatch.

        Args:
            dispatch_id: Correlation id of the dispatch this proxy serves.
            writer: Stream writer to the host.
            pending_responses: Correlation map for callback responses.
            codec: Codec building and writing frames.
        """
        self.dispatch_id = dispatch_id
        self.writer = writer
        self.pending_responses = pending_responses
        self.codec = codec

    async def wait_for_state(self, resource_id: str, target_state: LifecycleState, timeout: float) -> dict[str, Any]:
        """Ask the host to wait until a resource reaches a lifecycle state.

        Args:
            resource_id: Canonical id of the resource to watch.
            target_state: State to wait for.
            timeout: Maximum seconds to wait, a finite number.

        Returns:
            The reached ``lifecycle_state`` and the resource's ``outputs``.

        Raises:
            RuntimeError: If the callback fails: the host reports a failure,
                including when the state is not reached within ``timeout``,
                answers with a malformed result, or cannot be reached, or
                ``timeout`` is infinite or NaN.
        """  # noqa: DOC502
        params = WaitForStateParams(
            dispatch_id=self.dispatch_id, resource_id=resource_id, target_state=target_state, timeout=timeout
        )

        result = await self.call(WAIT_FOR_STATE_METHOD, params)

        try:
            return WaitForStateResult.model_validate(result).model_dump()
        except ValidationError as error:
            message = build_validation_error_message(error)
            raise RuntimeError(f"Callback {WAIT_FOR_STATE_METHOD} returned a malformed result: {message}") from None

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        """Ask the host to apply a resource through the platform.

        Args:
            resource_data: The resource to apply, as ``Resource.apply`` builds it.

        Raises:
            ValidationError: If ``resource_data`` does not match the child
                resource payload, extra keys included.
            RuntimeError: If the callback fails: the host reports a failure
                or cannot be reached.
        """  # noqa: DOC502
        child = ChildResourcePayload.model_validate(resource_data)
        params = ApplyResourceParams(dispatch_id=self.dispatch_id, child=child)

        await self.call(APPLY_RESOURCE_METHOD, params)

    async def call(self, method: str, params: BaseModel) -> Any:
        """Send a callback request to the host and await its result.

        Args:
            method: Callback method name.
            params: Callback parameters.

        Returns:
            The callback's result, unvalidated.

        Raises:
            RuntimeError: If the host reports that the callback failed, the
                request cannot be sent, or the host connection is lost.
        """
        correlation_id, future = self.pending_responses.register()

        try:
            await self.send_request(correlation_id, method, params)
            response: Envelope = await future
        except (FrameError, OSError) as error:
            raise RuntimeError(f"Callback {method} failed: {build_error_message(error)}") from error
        finally:
            self.pending_responses.discard(correlation_id)

        if response.error is not None:
            raise RuntimeError(f"Callback {method} failed ({response.error.code}): {response.error.message}")

        return response.result

    async def send_request(self, correlation_id: int, method: str, params: BaseModel) -> None:
        """Write a callback request to the host.

        A request that cannot be sent never reaches the host, so the worker
        logs it as ``callback_send_failed``.

        Args:
            correlation_id: Correlation id of the request.
            method: Callback method name.
            params: Callback parameters.

        Raises:
            RuntimeError: If the request cannot be encoded, exceeds
                ``MAXIMUM_FRAME_BYTES``, or cannot be written.
        """
        try:
            request = self.codec.build_request(correlation_id, method, params)
            await self.codec.write(self.writer, request)
        except (FrameError, OSError, ValueError) as error:
            message = build_error_message(error)
            logger.warning("callback_send_failed", callback_method=method, error=message)
            raise RuntimeError(f"Callback {method} failed: {message}") from error
