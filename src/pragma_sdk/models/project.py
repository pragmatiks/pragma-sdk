"""Project model — the unit of scoping for resources.

A :class:`Project` groups resources for a single organization. Every
resource on the platform belongs to exactly one project; the project is
the scope for listing, applying, and deleting resources.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Project(BaseModel):
    """A project owned by an organization.

    Attributes:
        project_id: Unique project identifier (server-assigned UUID).
        organization_id: Organization the project belongs to.
        name: Human-readable project name.
        created_at: Timestamp when the project was created.
        updated_at: Timestamp of the last update.
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    organization_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class CreateProjectRequest(BaseModel):
    """Request body for creating a new project.

    Attributes:
        name: Human-readable project name. The server assigns the
            project's ID.
    """

    name: str


class UpdateProjectRequest(BaseModel):
    """Request body for updating project metadata.

    Only ``name`` may be updated.

    Attributes:
        name: New human-readable name for the project.
    """

    name: str


class DeleteProjectRequest(BaseModel):
    """Typed-confirmation request for deleting a project.

    Project deletion is a hard delete and cannot be undone. The caller
    must pass the project's current ``name`` in ``confirmation``; the
    server rejects the request if the values do not match. By default
    the server also refuses to delete a project that still contains
    resources; set ``orphan_resources`` to opt out of that safety check.

    Attributes:
        confirmation: Must equal the target project's name.
        orphan_resources: If True, delete the project without requiring
            it to be empty. Any resources still attached to the project
            are removed from Pragma's tracking WITHOUT triggering
            provider lifecycle events — no cloud infrastructure is torn
            down and the caller becomes responsible for the orphaned
            infrastructure afterwards. Defaults to False, which preserves
            the safety check and causes the server to return 409 when
            resources remain.
    """

    confirmation: str
    orphan_resources: bool = False
