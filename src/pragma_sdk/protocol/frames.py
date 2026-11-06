"""Frame models, handshake models and payloads of the worker protocol.

Incoming frames are read into the permissive :class:`Envelope`, whose kind
follows from ``id`` and ``method``; the receiver validates ``params`` or
``result`` against the payload model the method names.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

from pragma_sdk.protocol.constants import MAXIMUM_OPERATION_DEADLINE_SECONDS
from pragma_sdk.types import EventType, LifecycleState


CatalogName = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?/[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
]
"""A provider catalog name, ``prefix/name``: two lowercase alphanumeric labels, inner hyphens allowed."""

CHILDREN_PENDING_WITH_OUTPUTS_MESSAGE = "A result that waits for its children carries no outputs."
"""Why a dispatch result or a provider response that sets ``children_pending`` and carries outputs is refused."""


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

    A notification without params serializes without a ``params`` member.

    Attributes:
        jsonrpc: JSON-RPC version, always ``"2.0"``.
        method: Method name.
        params: Method parameters, or ``None`` for a bare notification.
    """

    jsonrpc: str
    method: str
    params: Any = None

    @model_serializer(mode="wrap")
    def serialize_present_members(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize the frame, leaving ``params`` out when the notification carries none.

        Args:
            handler: Pydantic's default serializer of this model.

        Returns:
            The frame's members; ``params`` only when it is not ``None``.
        """
        members = handler(self)

        if self.params is None:
            members.pop("params", None)

        return members


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
        operation_deadline_seconds: Ceiling in whole seconds, from 1 to
            ``MAXIMUM_OPERATION_DEADLINE_SECONDS``, that heartbeats may
            carry one operation of the type to, or ``None`` when the type
            uses the platform default.
    """

    resource: str
    observe: bool
    computed: bool
    logs: bool
    health: bool
    operation_deadline_seconds: int | None = Field(default=None, gt=0, le=MAXIMUM_OPERATION_DEADLINE_SECONDS)


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
        distribution_catalog_names: Provider catalog name (``prefix/name``)
            of the served distribution and of every provider distribution it
            embeds, keyed by PEP 503-normalized distribution name. Must
            contain the served distribution, or the worker refuses the
            handshake.
        heartbeat_interval_seconds: Seconds between two ``heartbeat``
            notifications the worker sends from the accepted handshake
            until the session ends; a positive, finite number.
    """

    protocol_version: int
    distribution_catalog_names: dict[str, CatalogName]
    heartbeat_interval_seconds: float = Field(gt=0, allow_inf_nan=False)


class CancelParams(BaseModel):
    """Parameters of a ``cancel`` notification the host sends at an operation's deadline.

    Attributes:
        correlation_id: Correlation id of the request to cancel.
    """

    correlation_id: int


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


class ChildResourcePayload(BaseModel):
    """A resource a lifecycle method applies through the platform.

    The project is the dispatched resource's, and the host names the
    dispatched resource as its owner, so the payload names neither, as
    ``ReadResourceParams`` does.

    Attributes:
        provider: Provider catalog name of the resource.
        resource: Resource type name.
        name: Resource name.
        config: Resource configuration.
        tags: Resource tags, or ``None`` when the resource sets none.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    resource: str
    name: str
    config: dict[str, Any]
    tags: list[str] | None = None


class DispatchParams(BaseModel):
    """Parameters of a ``dispatch`` request running one lifecycle method.

    Attributes:
        event_type: Lifecycle event to run.
        target: The resource to run it on.
        previous_config: Configuration before an update, or ``None``.
        resolved_files: Signed download URLs keyed by Pragmatiks file reference.
    """

    event_type: Literal[EventType.CREATE, EventType.UPDATE, EventType.DELETE]
    target: ResourcePayload
    previous_config: dict[str, Any] | None
    resolved_files: dict[str, str]


class DispatchResult(BaseModel):
    """Result of a ``dispatch`` request.

    Attributes:
        outputs: Outputs the lifecycle method returned, validated against the
            resource's Outputs class and encoded with secrets revealed, or
            ``None``.
        children_pending: Whether ``on_create`` or ``on_update`` returned
            ``ChildrenPending``: it waits for the resources it applied to be
            ready, and carries no outputs.
    """

    outputs: dict[str, Any] | None
    children_pending: bool = False

    @model_validator(mode="after")
    def refuse_outputs_with_children_pending(self) -> Self:
        """Refuse a result that both waits for children and carries outputs.

        Returns:
            The validated result.

        Raises:
            ValueError: If ``children_pending`` is set and ``outputs`` is not ``None``.
        """
        if self.children_pending and self.outputs is not None:
            raise ValueError(CHILDREN_PENDING_WITH_OUTPUTS_MESSAGE)

        return self


class ObserveParams(BaseModel):
    """Parameters of an ``observe`` request reading the external object a resource owns.

    Attributes:
        target: The resource to observe.
        resolved_files: Signed download URLs keyed by Pragmatiks file reference.
    """

    target: ResourcePayload
    resolved_files: dict[str, str]


class ObserveResult(BaseModel):
    """Result of an ``observe`` request.

    Attributes:
        outputs: Outputs of the observed object, validated against the
            resource's Outputs class and encoded as a dispatch's outputs are,
            or ``None`` when the object is absent.
    """

    outputs: dict[str, Any] | None


class ApplyResourceParams(BaseModel):
    """Parameters of an ``apply_resource`` callback request.

    Attributes:
        dispatch_id: Correlation id of the dispatch issuing the callback.
        child: The resource to apply.
    """

    dispatch_id: int
    child: ChildResourcePayload


class ReadResourceParams(BaseModel):
    """Parameters of a ``read_resource`` callback request reading a resource the dispatched resource owns.

    The project is the dispatched resource's. The host answers only for a
    resource the dispatched resource owns, and refuses any other live
    resource with ``CALLBACK_REFUSED_CODE``.

    Attributes:
        dispatch_id: Correlation id of the dispatch issuing the callback.
        provider: Provider catalog name of the resource to read.
        resource: Resource type name of the resource to read.
        name: Name of the resource to read.
    """

    dispatch_id: int
    provider: str
    resource: str
    name: str


class ChildResourceStatePayload(BaseModel):
    """A child resource the dispatched resource owns, as the platform records it.

    The host serves it unmasked, sensitive values included, since only the
    owner's lifecycle method reads it.

    Attributes:
        lifecycle_state: Lifecycle state of the child resource.
        config: The child resource's stored configuration, as its owner
            applied it, with references unresolved.
        outputs: Outputs of the child resource, or ``None``.
    """

    lifecycle_state: LifecycleState
    config: dict[str, Any]
    outputs: dict[str, Any] | None


class ReadResourceResult(BaseModel):
    """Result of a ``read_resource`` callback.

    Attributes:
        resource: The child resource, or ``None`` when no live resource
            has that identity.
    """

    resource: ChildResourceStatePayload | None


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
    """

    target: ResourcePayload
    since: AwareDatetime | None
    tail: int
