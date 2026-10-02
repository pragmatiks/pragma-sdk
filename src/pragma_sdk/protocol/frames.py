"""Frame models, handshake models and payloads of the worker protocol.

Incoming frames are read into the permissive :class:`Envelope`, whose kind
follows from ``id`` and ``method``; the receiver validates ``params`` or
``result`` against the payload model the method names.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from pragma_sdk.types import EventType, LifecycleState


class FrameKind(StrEnum):
    """The three JSON-RPC message kinds a frame can be."""

    REQUEST = "request"
    NOTIFICATION = "notification"
    RESPONSE = "response"


class ErrorObject(BaseModel):
    """Outgoing JSON-RPC error object describing why a request failed.

    Attributes:
        code: JSON-RPC error code.
        message: Human-readable error description.
        data: Structured error detail, or ``None``.
    """

    code: int
    message: str
    data: Any


class RequestFrame(BaseModel):
    """Outgoing request that expects a response with the same ``id``.

    Attributes:
        jsonrpc: JSON-RPC version, always ``"2.0"``.
        id: Correlation id unique among the sender's open requests.
        method: Method name.
        params: Method parameters.
    """

    jsonrpc: str
    id: int
    method: str
    params: Any


class NotificationFrame(BaseModel):
    """Outgoing notification that expects no response.

    Attributes:
        jsonrpc: JSON-RPC version, always ``"2.0"``.
        method: Method name.
    """

    jsonrpc: str
    method: str


class SuccessFrame(BaseModel):
    """Outgoing response reporting that a request succeeded.

    Attributes:
        jsonrpc: JSON-RPC version, always ``"2.0"``.
        id: Correlation id of the request being answered.
        result: Method result.
    """

    jsonrpc: str
    id: int
    result: Any


class FailureFrame(BaseModel):
    """Outgoing response reporting that a request failed.

    Attributes:
        jsonrpc: JSON-RPC version, always ``"2.0"``.
        id: Correlation id of the request being answered.
        error: Why the request failed.
    """

    jsonrpc: str
    id: int
    error: ErrorObject


class EnvelopeError(BaseModel):
    """Incoming JSON-RPC error object of a failed response.

    Validation errors of this model never repeat the rejected input.

    Attributes:
        code: JSON-RPC error code.
        message: Human-readable error description.
        data: Structured error detail, or ``None`` when the peer sent none.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    code: int
    message: str
    data: Any = None


class Envelope(BaseModel):
    """Incoming JSON-RPC 2.0 message of any kind.

    Validation errors of this model never repeat the rejected input, so a
    malformed frame carrying revealed secrets can be described safely.

    Attributes:
        jsonrpc: JSON-RPC version.
        id: Correlation id, absent for notifications.
        method: Method name, absent for responses.
        params: Request parameters, unvalidated.
        result: Success payload of a response, unvalidated.
        error: Error object of a failed response.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    jsonrpc: str
    id: int | None = None
    method: str | None = None
    params: Any = None
    result: Any = None
    error: EnvelopeError | None = None

    @property
    def kind(self) -> FrameKind:
        """Classify this frame from the presence of ``id`` and ``method``.

        A frame without ``id`` is a notification, one with ``id`` but no
        ``method`` is a response, and one carrying both is a request.

        Returns:
            The frame's kind.
        """
        if self.id is None:
            return FrameKind.NOTIFICATION

        if self.method is None:
            return FrameKind.RESPONSE

        return FrameKind.REQUEST


class ProtocolOffer(BaseModel):
    """Protocol versions a worker speaks.

    Attributes:
        protocol_versions: Protocol versions the worker speaks.
    """

    protocol_versions: list[int]


class ResourceTypeCapabilities(BaseModel):
    """What one resource type of a provider implements.

    Attributes:
        resource: Registered resource type name.
        observe: Whether the type defines ``on_observe``.
        computed: Whether the type is declared computed.
        logs: Whether the type overrides ``logs``.
        health: Whether the type overrides ``health``.
    """

    resource: str
    observe: bool
    computed: bool
    logs: bool
    health: bool


class HandshakeParams(ProtocolOffer):
    """Parameters of the ``handshake`` request, the worker's first frame.

    Attributes:
        sdk_version: Version of the Pragmatiks SDK the worker runs.
        distribution: Provider wheel distribution name the worker serves.
        short_name: Entry-point name the distribution declares.
        package: Importable package holding the provider's resource types.
        resource_types: Resource types the provider defines, with capabilities.
    """

    sdk_version: str
    distribution: str
    short_name: str
    package: str
    resource_types: list[ResourceTypeCapabilities]


class HandshakeResult(BaseModel):
    """Result of an accepted handshake.

    Attributes:
        protocol_version: Protocol version the connection speaks from now on.
    """

    protocol_version: int


class UnsupportedProtocolData(BaseModel):
    """Error data of a handshake refused for offering no supported protocol version.

    Attributes:
        protocol_version: Protocol version the host speaks.
    """

    protocol_version: int


class ResourcePayload(BaseModel):
    """The resource a dispatch or query acts on.

    Attributes:
        project_id: Project owning the resource.
        resource: Resource type name within the provider.
        name: Resource name.
        config: Resource configuration, with dependency and field-reference
            markers still in place.
        tags: Resource tags, or ``None``.
    """

    project_id: str
    resource: str
    name: str
    config: dict[str, Any]
    tags: list[str] | None


class OwnerReferencePayload(BaseModel):
    """Identity of the resource that owns the resources a dispatch applies.

    Attributes:
        project_id: Project of the owner.
        provider: Provider catalog name of the owner.
        resource: Resource type name of the owner.
        name: Resource name of the owner.
    """

    project_id: str
    provider: str
    resource: str
    name: str


class ChildResourcePayload(BaseModel):
    """A resource a lifecycle method applies through the platform.

    Attributes:
        project_id: Project of the resource.
        provider: Provider catalog name of the resource.
        resource: Resource type name.
        name: Resource name.
        config: Resource configuration.
        owner_references: Resources that own this one.
        tags: Resource tags, or ``None`` when the resource sets none.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    provider: str
    resource: str
    name: str
    config: dict[str, Any]
    owner_references: list[OwnerReferencePayload]
    tags: list[str] | None = None


class DispatchParams(BaseModel):
    """Parameters of a ``dispatch`` request running one lifecycle method.

    Attributes:
        event_type: Lifecycle event to run.
        target: The resource to run it on.
        previous_config: Configuration before an update, or ``None``.
        resolved_files: Signed download URLs keyed by Pragmatiks file reference.
        owner_reference: Owner recorded on resources the method applies.
        provider: Provider catalog name of the resource.
    """

    event_type: Literal[EventType.CREATE, EventType.UPDATE, EventType.DELETE]
    target: ResourcePayload
    previous_config: dict[str, Any] | None
    resolved_files: dict[str, str]
    owner_reference: OwnerReferencePayload
    provider: str


class WaitForStateParams(BaseModel):
    """Parameters of a ``wait_for_state`` callback request.

    Attributes:
        dispatch_id: Correlation id of the dispatch issuing the callback.
        resource_id: Canonical id of the resource to watch.
        target_state: Lifecycle state to wait for.
        timeout: Maximum seconds to wait.
    """

    dispatch_id: int
    resource_id: str
    target_state: LifecycleState
    timeout: float


class WaitForStateResult(BaseModel):
    """Result of a ``wait_for_state`` callback.

    Attributes:
        lifecycle_state: Lifecycle state the resource reached.
        outputs: Outputs of the resource, or ``None``.
    """

    lifecycle_state: LifecycleState
    outputs: dict[str, Any] | None


class ApplyResourceParams(BaseModel):
    """Parameters of an ``apply_resource`` callback request.

    Attributes:
        dispatch_id: Correlation id of the dispatch issuing the callback.
        child: The resource to apply.
    """

    dispatch_id: int
    child: ChildResourcePayload


class MigrateParams(BaseModel):
    """Parameters of a ``migrate`` request transforming one resource's state.

    Attributes:
        resource: Resource type name whose class runs the migration.
        direction: ``"up"`` runs ``upgrade``, ``"down"`` runs ``downgrade``.
        config: Configuration to transform.
        outputs: Outputs to transform, or ``None``.
    """

    resource: str
    direction: Literal["up", "down"]
    config: dict[str, Any]
    outputs: dict[str, Any] | None


class MigrateResult(BaseModel):
    """Result of a ``migrate`` request.

    Attributes:
        config: Transformed configuration.
        outputs: Transformed outputs, or ``None``.
    """

    config: dict[str, Any]
    outputs: dict[str, Any] | None


class ResourceQueryParams(BaseModel):
    """Parameters of a ``resource_logs`` or ``resource_health`` request.

    Attributes:
        target: The resource to query.
        since: Earliest log timestamp to include, with its UTC offset, or
            ``None``; health ignores it.
        tail: Maximum number of recent log entries; health ignores it.
        provider: Provider catalog name of the resource.
    """

    target: ResourcePayload
    since: AwareDatetime | None
    tail: int
    provider: str
