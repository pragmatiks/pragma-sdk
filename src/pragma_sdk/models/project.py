"""Project model — the unit of scoping for resources.

A :class:`Project` groups resources for a single organization. Every
resource on the platform belongs to exactly one project; the project is
the scope for listing, applying, and deleting resources. A project may
be flagged ``is_private`` to mark it as platform-owned and hide it from
user-facing list endpoints.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Project(BaseModel):
    """A project owned by an organization.

    Attributes:
        id: Unique project identifier (UUID).
        organization_id: Organization the project belongs to.
        name: Human-readable project name.
        slug: URL-safe slug, unique within the organization.
        is_private: True for platform-owned projects. Private projects are
            excluded from user-facing list endpoints and are internal to
            pragma-os.
        created_at: Timestamp when the project was created.
        updated_at: Timestamp of the last update.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    organization_id: str
    name: str
    slug: str
    is_private: bool = False
    created_at: datetime
    updated_at: datetime


class CreateProjectRequest(BaseModel):
    """Request body for creating a new project.

    Attributes:
        name: Human-readable project name.
        slug: Optional URL-safe slug. If omitted the API derives one from
            ``name``.
    """

    name: str
    slug: str | None = None


class UpdateProjectRequest(BaseModel):
    """Request body for updating project metadata.

    Only ``name`` may be updated. ``slug`` is immutable and must be set at
    creation time.

    Attributes:
        name: New human-readable name for the project.
    """

    name: str | None = None


class DeleteProjectRequest(BaseModel):
    """Typed-confirmation request for deleting a project.

    Project deletion is a hard delete and cannot be undone. The caller
    must pass the project's current ``slug`` in ``confirmation``; the
    server rejects the request if the values do not match.

    Attributes:
        confirmation: Must equal the target project's slug.
    """

    confirmation: str
