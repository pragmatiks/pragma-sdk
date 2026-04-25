"""Task graph diff and mutation log models.

Mirrors the API models for the ``/agents/tasks/{task_id}/mutations``
and ``/agents/tasks/{task_id}/graph-diff`` endpoints, which expose the
``task->mutated->resource`` edge stream as two complementary shapes:

- A **mutation log** — every mutation tied to a task, paginated, with
  full before/after snapshots.
- A **graph diff** — the net delta per affected resource, computed by
  replaying the ordered mutation sequence for each resource and
  collapsing create + delete into a no-op.

The graph diff endpoint scans up to a server-side cap of mutations per
request (5000 by default). Tasks that exceed the cap return a partial
rollup with ``truncated=True`` and ``has_more=True``; clients should
direct users to the paginated mutation log for the full audit trail.
Sensitive fields on snapshots are masked by default; pass ``reveal=True``
on the SDK methods to see actual values.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class MutationOperation(StrEnum):
    """Discriminator for the underlying resource write on a mutation edge."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class MutationActorType(StrEnum):
    """Who initiated the mutation."""

    USER = "user"
    AGENT_INSTANCE = "agent_instance"


class ResourceFieldDiff(BaseModel):
    """Before/after value for a single resource field.

    Attributes:
        field: Top-level resource field name (e.g. ``config``, ``outputs``).
        before: Value of the field before the mutation, or ``None`` when
            the field did not exist on the before snapshot.
        after: Value of the field after the mutation, or ``None`` when the
            field was removed by the mutation.
    """

    field: str
    before: Any | None = None
    after: Any | None = None


class ResourceMutation(BaseModel):
    """A single resource mutation tied to a task.

    Each entry corresponds to one ``task->mutated->resource`` edge in
    the shared SurrealDB namespace.

    Attributes:
        edge_id: SurrealDB edge identifier (id part, no table prefix).
            Stable across reads — used together with ``timestamp`` as a
            composite pagination cursor.
        timestamp: When the mutation was persisted.
        operation: ``create``, ``update``, or ``delete``.
        resource_table: SurrealDB table that holds the target resource.
            Always ``resources`` in the current schema.
        resource_id: SurrealDB record id part for the target resource.
            No table prefix.
        before: Resource snapshot before the mutation, or ``None`` on
            create.
        after: Resource snapshot after the mutation, or ``None`` on
            delete.
        fields_changed: Top-level field names whose value differs between
            ``before`` and ``after``. Noise fields (``updated_at``,
            ``version``, etc.) are filtered out by the server.
        version_before: Resource version before the mutation, or ``None``
            on create.
        version_after: Resource version after the mutation, or ``None``
            on delete.
        actor_type: ``user`` or ``agent_instance`` — who initiated the
            mutation.
        actor_id: Identifier of the actor (user id or instance id). May
            be ``None`` for system-level writes.
    """

    edge_id: str
    timestamp: datetime
    operation: MutationOperation
    resource_table: str
    resource_id: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    fields_changed: list[str] = []
    version_before: int | None = None
    version_after: int | None = None
    actor_type: MutationActorType
    actor_id: str | None = None


class TaskMutationPage(BaseModel):
    """Paginated page of mutation log entries.

    Attributes:
        items: Mutation entries for the current page, newest first.
        next_cursor: Composite cursor for the next page, or ``None``
            when no more entries exist. Built as
            ``"<iso-timestamp>|<edge-id>"`` over the last entry in
            ``items``.
    """

    items: list[ResourceMutation]
    next_cursor: str | None = None


class ResourceNetDelta(BaseModel):
    """Net delta for a single resource across a task's mutation sequence.

    The net delta is the last-winning projection of every mutation for
    the same ``(resource_table, resource_id)`` pair ordered by
    timestamp. Create + delete collapses to ``net_operation = "noop"``
    because the resource neither exists before nor after the task —
    there is nothing for the reviewer to scan.

    Attributes:
        resource_table: SurrealDB table for the affected resource.
        resource_id: SurrealDB record id part (no table prefix).
        net_operation: One of:

            - ``create`` — resource did not exist before the task and
              exists after.
            - ``update`` — resource existed before and after with at
              least one observable field change.
            - ``delete`` — resource existed before the task and does
              not after.
            - ``noop`` — resource was created and then deleted inside
              the same task; the net effect is that the resource never
              existed. Also used when a no-op update produced no field
              changes.

        before: First observed before snapshot, or ``None`` when the
            net effect is a create or a noop via create+delete.
        after: Last observed after snapshot, or ``None`` when the net
            effect is a delete or noop.
        fields_changed: Union of all top-level fields that differ
            between the earliest before and the latest after snapshots.
        mutation_count: Number of raw ``mutated`` edges collapsed into
            this net delta. Useful for rendering "3 changes" badges.
    """

    resource_table: str
    resource_id: str
    net_operation: Literal["create", "update", "delete", "noop"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    fields_changed: list[str] = []
    mutation_count: int = 0


class GraphDiff(BaseModel):
    """Top-level graph diff response for a task.

    Attributes:
        task_id: Task SurrealDB record id part. Echoed back so clients
            can correlate the response with the request.
        resources: Net delta per affected resource, ordered by
            ``(resource_table, resource_id)`` for deterministic output.
        total_mutations: Raw mutation edge count folded into the
            rollup. When ``truncated`` is ``True`` this is the cap, not
            the real total — the real total is not known without a
            full scan.
        total_mutations_scanned: Number of mutation edges actually
            consumed from the database before the rollup was cut off.
            Equals ``total_mutations`` in the common (non-truncated)
            case. Exposed so clients can surface "5000 mutations
            scanned" when ``truncated`` is ``True``.
        truncated: ``True`` when the task had more mutation edges than
            the server-side cap. The rollup is still safe to display
            but does not reflect every mutation; use the paginated
            mutation log for the full audit trail.
        has_more: Alias for ``truncated`` — kept for clients that use
            the has-more convention.
    """

    task_id: str
    resources: list[ResourceNetDelta]
    total_mutations: int
    total_mutations_scanned: int
    truncated: bool = False
    has_more: bool = False
