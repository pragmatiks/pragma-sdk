"""The result a lifecycle method returns to wait for the resources it applied."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChildrenPending(BaseModel):
    """Returned from ``on_create`` or ``on_update`` instead of outputs, to wait for the resources it applied.

    The operation then carries no outputs. The platform waits until every
    resource this operation applied that was not ready is ready, then runs
    the resource again: a computed resource through ``on_update``, a
    resource with an external object through ``on_create`` when observing
    finds the object absent, else through ``on_update``. That second pass
    applies the same resources again, which changes nothing, reads them
    through ``Resource.read`` and returns the outputs.

    Both passes run the same code, and a pass whose resources are already
    ready returns its outputs at once. The second pass's
    ``previous_config`` is the configuration completed before the first
    pass, ``None`` after a create.

    A resource that this operation applied and that fails its create, or
    is deleted, fails the waiting resource. Returning it when every
    resource the operation applied was already ready and unchanged, or
    when it applied none, fails the resource, since nothing would wake it:
    return it only after ``Resource.read`` found one not ready. The
    platform tears applied resources down before the resource that owns
    them, so ``on_delete`` deletes none of them.
    """

    model_config = ConfigDict(frozen=True)
