"""Task models for agent work tracking and board management.

Mirrors the Pydantic models exposed by the API at
``/agents/tasks/*``. Subtask relationships are modeled as graph edges
in SurrealDB rather than a flat parent reference, so :class:`Task`
itself does not carry a parent identifier — use the subtask client
methods to traverse the graph.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from pragma_sdk.models.enums import TaskSource, TaskStatus


class Task(BaseModel):
    """An agent task representing a unit of work.

    Subtask relationships are modeled as ``has_subtask`` graph edges in
    SurrealDB rather than a flat parent reference. Use the subtask client
    methods (``list_subtasks``, ``create_subtask``, ``link_subtask``,
    ``unlink_subtask``) to traverse the graph.

    Attributes:
        id: SurrealDB record ID.
        organization_id: Owning organization.
        title: Short description of the task.
        description: Detailed description.
        status: Current task status.
        priority: Priority level (1=urgent, 2=high, 3=normal, 4=low).
        assigned_to_type_id: Agent type assigned to this task.
        assigned_to_instance_id: Agent instance working on this task.
        assigned_to_user_id: User assigned to this task.
        correlation_bucket_id: Correlation bucket for related events.
        created_by: User ID of the creator.
        source: How this task was created.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str | None = None
    organization_id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: int = Field(default=3, ge=1, le=4)
    assigned_to_type_id: str | None = None
    assigned_to_instance_id: str | None = None
    assigned_to_user_id: str | None = None
    correlation_bucket_id: str | None = None
    created_by: str | None = None
    source: TaskSource = TaskSource.MANUAL
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskCreate(BaseModel):
    """Request body for creating a task.

    The owning organization and creator are taken from the authenticated
    request context — callers do not (and cannot) set them. Status
    transitions after creation must go through the dedicated transition
    endpoint.

    Attributes:
        title: Short description of the task.
        description: Detailed description.
        status: Initial task status.
        priority: Priority level (1=urgent, 2=high, 3=normal, 4=low).
        assigned_to_type_id: Agent type to assign.
        assigned_to_instance_id: Agent instance to assign.
        assigned_to_user_id: User to assign.
        correlation_bucket_id: Correlation bucket for related events.
        source: How this task was created.
    """

    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: int = Field(default=3, ge=1, le=4)
    assigned_to_type_id: str | None = None
    assigned_to_instance_id: str | None = None
    assigned_to_user_id: str | None = None
    correlation_bucket_id: str | None = None
    source: TaskSource = TaskSource.MANUAL


class TaskUpdate(BaseModel):
    """Request body for updating a task. All fields optional.

    Status changes are not allowed here — use the transition endpoint
    instead.

    Attributes:
        title: Short description of the task.
        description: Detailed description.
        priority: Priority level (1=urgent, 2=high, 3=normal, 4=low).
        assigned_to_type_id: Agent type to assign.
        assigned_to_instance_id: Agent instance to assign.
        assigned_to_user_id: User to assign.
        correlation_bucket_id: Correlation bucket for related events.
        source: How this task was created.
    """

    title: str | None = None
    description: str | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    assigned_to_type_id: str | None = None
    assigned_to_instance_id: str | None = None
    assigned_to_user_id: str | None = None
    correlation_bucket_id: str | None = None
    source: TaskSource | None = None


class TaskAssign(BaseModel):
    """Request body for assigning a task.

    Exactly one of ``instance_id``, ``user_id``, or ``type_id`` should be
    provided. The server replaces any existing assignment of the same
    kind with the new value.

    Attributes:
        instance_id: Agent instance to assign.
        user_id: User to assign.
        type_id: Agent type to assign.
    """

    instance_id: str | None = None
    user_id: str | None = None
    type_id: str | None = None


class TaskTransition(BaseModel):
    """Request body for transitioning a task to a new status.

    The transition is validated against the allowed status graph on the
    server — invalid transitions return ``InvalidLifecycleTransitionError``.

    Attributes:
        status: Target status.
    """

    status: TaskStatus


class BoardSummary(BaseModel):
    """Aggregate task counts per status for the board summary view.

    Attributes:
        counts: Mapping of status to task count.
        total: Total number of tasks across all statuses.
    """

    counts: dict[str, int]
    total: int
