"""Task comment models with dual authorship for users and agents.

Mirrors the API task comment models. Authorship is captured by graph
edges on the server side — ``authored_by_user`` for user comments and
the ``authored_by_instance`` / ``authored_by_type`` pair for agent
comments — so the type-level attribution survives instance garbage
collection.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class CommentAuthorType(StrEnum):
    """Source of a task comment."""

    USER = "user"
    AGENT = "agent"


class TaskComment(BaseModel):
    """A markdown comment on a task.

    Comments are first-class graph nodes connected to tasks via
    ``has_comment`` edges. User comments are creatable via the SDK;
    agent comments are written by the agent runtime through a separate
    code path and only ever appear on read responses.

    Attributes:
        id: SurrealDB record ID.
        organization_id: Owning organization (mirrored from the task).
        task_id: Task this comment belongs to.
        body: Markdown body of the comment.
        author_type: Whether the author is a user or an agent.
        author_user_id: User ID when ``author_type`` is ``user``.
        author_instance_id: Agent instance ID when ``author_type`` is ``agent``.
        author_agent_type_id: Agent type ID when ``author_type`` is ``agent``.
        edited: True after at least one update.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str | None = None
    organization_id: str
    task_id: str
    body: str
    author_type: CommentAuthorType
    author_user_id: str | None = None
    author_instance_id: str | None = None
    author_agent_type_id: str | None = None
    edited: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskCommentCreate(BaseModel):
    """Request body for creating a task comment.

    Authorship is set by the server from the authenticated request
    context — only the body is sent.

    Attributes:
        body: Markdown body of the comment.
    """

    body: str


class TaskCommentUpdate(BaseModel):
    """Request body for editing a task comment.

    Only the original author may edit a comment; the server returns
    ``TaskCommentForbiddenError`` for any other actor.

    Attributes:
        body: New markdown body to replace the existing one.
    """

    body: str
