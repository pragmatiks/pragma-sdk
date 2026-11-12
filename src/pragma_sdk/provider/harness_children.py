"""Child resources the ProviderHarness brokers for the resources it runs.

The harness serves ``Resource.apply`` and ``Resource.read`` from an
in-memory record of resources in its project, each with its owner, and
answers them as the platform does: a new or changed child starts
PENDING, an unchanged one keeps its state and outputs, and an apply or
read of a live resource another resource owns, or no resource owns, is
refused with the platform's reason. A child's state changes only when the
test sets it.

The refusal texts are copied from pragma-os
``packages/api/src/pragma_api/models/resource_failure.py``
(``build_second_owner_reason``, ``build_unowned_child_reason``,
``build_read_refused_reason`` and ``build_nothing_pending_reason``).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from pragma_sdk.exceptions import CallbackRefusedError
from pragma_sdk.models.failure import RECOVERY_GUIDANCE
from pragma_sdk.models.identity import ResourceIdentity
from pragma_sdk.protocol.frames import ChildResourceStatePayload
from pragma_sdk.types import LifecycleState


def format_resource_id(identity: ResourceIdentity) -> str:
    """Format an identity in the ``provider/resource/name`` form the platform's reasons use.

    Args:
        identity: The resource identity.

    Returns:
        The identity without its project.
    """
    return f"{identity.provider}/{identity.resource}/{identity.name}"


def build_nothing_pending_message(owner: ResourceIdentity) -> str:
    """Build the reason a pass fails with when it returned ``ChildrenPending`` with nothing to wait for.

    Args:
        owner: Identity of the resource whose pass returned it.

    Returns:
        The platform's reason for that failure.
    """
    return (
        f"Resource {format_resource_id(owner)} asked to wait for its children, but it applied no child that was "
        f"not ready. {RECOVERY_GUIDANCE}"
    )


class BrokeredResource(NamedTuple):
    """A resource the harness brokers, with its owner and recorded state.

    Attributes:
        owner: Identity of its owner, or ``None`` for a resource no resource owns.
        state: Its recorded lifecycle state, config as applied, and outputs.
    """

    owner: ResourceIdentity | None
    state: ChildResourceStatePayload


class HarnessChildBroker:
    """Record of the resources in a harness project, with their owners and recorded state.

    Attributes:
        project_id: Project every brokered resource is kept in, whatever
            project an applied resource names.
        resources: Each resource's owner (``None`` for one no resource owns)
            and recorded state, keyed by identity.
    """

    def __init__(self, project_id: str) -> None:
        """Start an empty record.

        Args:
            project_id: Project every brokered resource is kept in.
        """
        self.project_id = project_id
        self.resources: dict[ResourceIdentity, BrokeredResource] = {}

    def build_identity(self, provider: str, resource: str, name: str) -> ResourceIdentity:
        """Build the identity of a resource in the harness project.

        Args:
            provider: Provider catalog name.
            resource: Resource type name.
            name: Resource name.

        Returns:
            The identity in the harness project.
        """
        return ResourceIdentity(project_id=self.project_id, provider=provider, resource=resource, name=name)

    def find_live(self, identity: ResourceIdentity) -> BrokeredResource | None:
        """Find a resource that is not deleted.

        Args:
            identity: The resource identity.

        Returns:
            Its owner and recorded state, or ``None`` when it is absent or DELETED.
        """
        record = self.resources.get(identity)

        if record is None or record.state.lifecycle_state == LifecycleState.DELETED:
            return None

        return record

    def bind(self, owner: ResourceIdentity) -> BrokeredRuntimeContext:
        """Build the runtime context one lifecycle pass of a resource calls back through.

        Args:
            owner: Identity of the dispatched resource, owner of what it applies.

        Returns:
            A runtime context recording what the pass applied.
        """
        return BrokeredRuntimeContext(self, owner)

    def clear(self) -> None:
        """Forget every brokered resource."""
        self.resources.clear()


class BrokeredRuntimeContext:
    """Runtime context serving one harness lifecycle pass's applies and reads.

    Attributes:
        broker: The record the pass applies to and reads from.
        owner: Identity of the dispatched resource.
        changed_by_identity: Whether each child the pass applied was new or
            changed, keyed by identity.
    """

    def __init__(self, broker: HarnessChildBroker, owner: ResourceIdentity) -> None:
        """Bind a pass to the broker.

        Args:
            broker: The record the pass applies to and reads from.
            owner: Identity of the dispatched resource.
        """
        self.broker = broker
        self.owner = owner
        self.changed_by_identity: dict[ResourceIdentity, bool] = {}

    async def apply_resource(self, resource_data: dict[str, Any]) -> None:
        """Apply a child owned by the dispatched resource, in the harness project.

        A new child, or one whose config changed, is recorded PENDING with no
        outputs; an unchanged one keeps its state and outputs. A DELETED
        resource counts as absent.

        Args:
            resource_data: Serialized resource data: provider, resource,
                name, config and tags.

        Raises:
            CallbackRefusedError: If a live resource with that identity is
                owned by another resource or by no resource.
        """  # noqa: DOC502
        identity = self.broker.build_identity(
            resource_data["provider"], resource_data["resource"], resource_data["name"]
        )
        config = resource_data["config"]
        record = self.broker.find_live(identity)

        if record is not None:
            self.refuse_foreign_apply(identity, record.owner)

        changed = record is None or record.state.config != config

        if changed:
            self.broker.resources[identity] = BrokeredResource(
                self.owner,
                ChildResourceStatePayload(lifecycle_state=LifecycleState.PENDING, config=config, outputs=None),
            )

        self.changed_by_identity[identity] = self.changed_by_identity.get(identity, False) or changed

    def refuse_foreign_apply(self, identity: ResourceIdentity, owner: ResourceIdentity | None) -> None:
        """Refuse an apply of a live resource the dispatched resource does not own.

        Args:
            identity: The applied resource's identity.
            owner: Its recorded owner, ``None`` for no owner.

        Raises:
            CallbackRefusedError: If ``owner`` is not the dispatched resource.
        """
        if owner == self.owner:
            return

        resource_id = format_resource_id(identity)

        if owner is None:
            raise CallbackRefusedError(
                f"Resource {resource_id} already exists and is owned by no resource. "
                f"Delete it before {format_resource_id(self.owner)} can own it."
            )

        raise CallbackRefusedError(
            f"Resource {resource_id} is already owned by {format_resource_id(owner)}. "
            "Delete it before another resource can own it."
        )

    async def read_resource(self, provider: str, resource: str, name: str) -> ChildResourceStatePayload | None:
        """Read a child the dispatched resource owns, its config as applied.

        Args:
            provider: Provider catalog name of the resource.
            resource: Resource type name of the resource.
            name: Name of the resource.

        Returns:
            The recorded state, or ``None`` when no live resource has that identity.

        Raises:
            CallbackRefusedError: If a live resource with that identity is
                not owned by the dispatched resource.
        """
        identity = self.broker.build_identity(provider, resource, name)
        record = self.broker.find_live(identity)

        if record is None:
            return None

        if record.owner != self.owner:
            raise CallbackRefusedError(
                f"Resource {format_resource_id(identity)} is not owned by this operation. "
                "Declare it as a dependency and read its resolved configuration."
            )

        return record.state

    def has_nothing_pending(self) -> bool:
        """Tell whether the pass applied no child, or only children READY and unchanged.

        Returns:
            Whether returning ``ChildrenPending`` from this pass waits for nothing.
        """
        return all(
            not changed and self.broker.resources[identity].state.lifecycle_state == LifecycleState.READY
            for identity, changed in self.changed_by_identity.items()
        )
