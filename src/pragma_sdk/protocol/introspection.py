"""The report ``python -m pragma_sdk.introspection`` writes about a provider distribution."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, RootModel

from pragma_sdk.protocol.frames import HandshakeParams, ProtocolOffer


class ResourceTypeSchema(BaseModel):
    """Schemas and descriptions of one resource type.

    Attributes:
        resource: Registered resource type name.
        description: First paragraph of the resource class docstring, joined
            into one line, or ``None``.
        config_schema: JSON schema of the resource's config.
        field_descriptions: Config field descriptions by field name.
        outputs_schema: JSON schema of the resource's outputs, or ``None``.
        output_descriptions: Output field descriptions by field name.
    """

    resource: str
    description: str | None
    config_schema: dict[str, Any]
    field_descriptions: dict[str, str]
    outputs_schema: dict[str, Any] | None
    output_descriptions: dict[str, str]


class IntrospectionSuccess(HandshakeParams):
    """Report of a distribution that imported and declared valid resource types.

    Attributes:
        outcome: Always ``"success"``.
        schemas: Schemas of every resource type the provider defines.
    """

    outcome: Literal["success"]
    schemas: list[ResourceTypeSchema]


class IntrospectionFailure(ProtocolOffer):
    """Report of a distribution that could not be introspected.

    Attributes:
        outcome: Always ``"failure"``.
        category: ``"missing_observe"`` when a non-computed resource type
            the provider's package defines has no ``on_observe``;
            ``"import_failed"`` for every other failure, including a missing
            distribution or entry point and such a type imported from
            outside the package.
        detail: Error message; for ``"import_failed"``, ``"{Type}: {message}"``.
        resource: Offending resource type for ``"missing_observe"``,
            otherwise ``None``.
    """

    outcome: Literal["failure"]
    category: Literal["missing_observe", "import_failed"]
    detail: str
    resource: str | None


class IntrospectionReport(
    RootModel[Annotated[IntrospectionSuccess | IntrospectionFailure, Field(discriminator="outcome")]]
):
    """The introspection report, read by its ``outcome``.

    The report is written by the process that imported the provider's code, so
    the provider controls every field. Readers treat it as untrusted input:
    they bound its size before parsing and do not take ``distribution`` from
    it as proof.
    """
