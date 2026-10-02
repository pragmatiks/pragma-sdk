"""Builds the introspection report of a provider distribution."""

from __future__ import annotations

from pragma_sdk.declaration.provider import DeclaredProvider
from pragma_sdk.diagnostics.error_descriptions import build_error_message
from pragma_sdk.protocol.constants import OFFERED_PROTOCOL_VERSIONS
from pragma_sdk.protocol.introspection import IntrospectionFailure, IntrospectionSuccess
from pragma_sdk.provider.loading import build_resource_type_schema
from pragma_sdk.provider.provider import MissingObserveError


def build_introspection_success(declared_provider: DeclaredProvider) -> IntrospectionSuccess:
    """Build the report of a provider that loaded.

    Args:
        declared_provider: The loaded provider.

    Returns:
        The handshake the provider's worker would send, plus the schemas of
        every resource type, sorted by name.

    Raises:
        ValueError: If a resource class has no Config-typed config field.
        PydanticInvalidForJsonSchema: If a config or outputs type cannot be
            expressed as JSON schema.
    """  # noqa: DOC502
    handshake = declared_provider.build_handshake_params()
    schemas = [
        build_resource_type_schema(resource_class)
        for _, resource_class in sorted(declared_provider.resource_types.items())
    ]

    return IntrospectionSuccess(**handshake.model_dump(), outcome="success", schemas=schemas)


def build_introspection_failure(error: BaseException) -> IntrospectionFailure:
    """Build the report of a provider that could not be introspected.

    Args:
        error: Why introspection failed.

    Returns:
        A ``missing_observe`` failure naming the resource type when a
        non-computed type defines no ``on_observe``; otherwise an
        ``import_failed`` failure whose detail is ``"{Type}: {message}"``,
        or ``"{Type}"`` alone when the exception has no readable text. The
        message repeats no rejected input or URL secret, and characters
        UTF-8 cannot encode, such as lone surrogates, are written as
        backslash escapes.
    """
    if isinstance(error, MissingObserveError):
        return IntrospectionFailure(
            protocol_versions=OFFERED_PROTOCOL_VERSIONS,
            outcome="failure",
            category="missing_observe",
            detail=str(error),
            resource=error.resource,
        )

    type_name = type(error).__name__
    message = build_error_message(error)
    detail = type_name if message == type_name else f"{type_name}: {message}"

    return IntrospectionFailure(
        protocol_versions=OFFERED_PROTOCOL_VERSIONS,
        outcome="failure",
        category="import_failed",
        detail=detail.encode("utf-8", "backslashreplace").decode("utf-8"),
        resource=None,
    )
