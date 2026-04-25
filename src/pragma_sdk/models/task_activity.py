"""Task activity timeline models.

Mirrors the API task activity entries. Each entry corresponds to one
graph edge — status transition, assignment, comment, agent start, or
resource mutation — composed into a single newest-first stream.
Mutation entries surface only the operation and changed field names;
full before/after snapshots live on the mutation log endpoint.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class TaskActivityKind(StrEnum):
    """Discriminator for entries in the task activity timeline."""

    TRANSITION = "transition"
    ASSIGNMENT = "assignment"
    MUTATION = "mutation"
    COMMENT = "comment"
    AGENT_STARTED = "agent_started"


class TaskActivityEntry(BaseModel):
    """A single entry in a task's derived activity timeline.

    Each entry corresponds to one graph edge. The shape varies by
    ``kind`` — only fields relevant to that kind are populated, all
    others are ``None``.

    Attributes:
        kind: Discriminator for the entry shape.
        timestamp: When the underlying edge was created.
        edge_id: SurrealDB edge identifier (no table prefix). Combined
            with ``timestamp`` to build the pagination cursor.
        from_status: Source status for transition entries.
        to_status: Target status for transition entries.
        assignee_table: Table of the assignee for assignment entries.
        assignee_id: Identifier of the assignee for assignment entries.
        comment_id: Comment node id for comment entries.
        instance_id: Agent instance id for agent_started entries.
        operation: Mutation operation for mutation entries
            (``create`` / ``update`` / ``delete``).
        resource_table: SurrealDB table for the mutated resource
            (``resources`` in the current schema).
        resource_id: SurrealDB record id of the mutated resource
            (id part only).
        fields_changed: Top-level field names whose value differs
            between the before and after snapshots on the mutation. The
            full before/after lives on the mutation log endpoint.
    """

    kind: TaskActivityKind
    timestamp: datetime
    edge_id: str
    from_status: str | None = None
    to_status: str | None = None
    assignee_table: str | None = None
    assignee_id: str | None = None
    comment_id: str | None = None
    instance_id: str | None = None
    operation: str | None = None
    resource_table: str | None = None
    resource_id: str | None = None
    fields_changed: list[str] | None = None
