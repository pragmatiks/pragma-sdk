"""Project model and request/response shapes for the project CRUD API.

Projects provide a level of nesting between an organization and its resources.
Each project maps to its own physical SurrealDB namespace so isolation between
projects is enforced at the storage layer, not by filtering.

Every organization is seeded with two projects at creation time:
    - A user-visible ``main`` project, the default landing place for resources.
    - A fully hidden private project holding platform-managed resources
      (LLM credentials, tier resources, platform agents). User-facing list
      endpoints never return private projects, but admin and platform code
      paths need to read and manipulate them, so the SDK model still carries
      the ``is_private`` flag.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field as PydanticField


class Project(BaseModel):
    """A project within an organization.

    Projects are the unit of resource isolation below an organization. Every
    resource belongs to exactly one project, and each project maps to its own
    SurrealDB namespace.

    Attributes:
        id: Server-generated UUID for the project.
        organization_id: Clerk organization identifier that owns this project.
        name: Human-readable display name (e.g., "Main").
        slug: URL-safe identifier, unique within the organization. Immutable
            once the project is created.
        is_private: True for platform-hidden projects that hold platform-
            managed resources. User-facing list endpoints never return
            projects with this flag set, but admin and platform code paths
            still need to access them.
        created_at: When the project was created.
        updated_at: When the project was last updated.
    """

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
        name: Human-readable display name for the project.
        slug: URL-safe identifier. If omitted, the server generates one from
            the name. Must be unique within the organization.
    """

    name: str
    slug: str | None = None


class UpdateProjectRequest(BaseModel):
    """Request body for updating a project.

    Only the display name can be updated. The slug is immutable once the
    project is created, matching how resource names behave.

    Attributes:
        name: New display name for the project. None leaves the name unchanged.
    """

    name: str | None = None


class DeleteProjectRequest(BaseModel):
    """Request body for hard-deleting a project.

    Hard delete with typed confirmation: the caller must pass the project's
    slug in ``confirmation`` and the server rejects the request unless the
    value matches exactly. There is no soft-delete phase; the project and all
    resources inside it are removed.

    Attributes:
        confirmation: The project's slug. Must match the target project's slug
            exactly or the server rejects the request.
    """

    confirmation: str = PydanticField(
        description="Typed confirmation — must match the target project's slug exactly.",
    )


ProjectDeleteConfirmation = DeleteProjectRequest
