"""Python SDK for the Pragmatiks platform.

Core exports for provider authoring:
    Provider: Decorator for defining providers.
    Resource: Base class for provider resources.
    Config: Base class for resource configuration.
    Outputs: Base class for resource outputs.
    Field, FieldReference, Dependency: For cross-resource references.

Core exports for API consumers:
    PragmaClient: Synchronous HTTP client.
    AsyncPragmaClient: Asynchronous HTTP client.

Additional imports available from submodules:
    pragma_sdk.models: Data models (BuildInfo, ProviderStatus, etc.)
    pragma_sdk.types: Type definitions (LifecycleState, HealthStatus, LogEntry)
    pragma_sdk.context: Runtime context utilities
    pragma_sdk.platform: Platform resource types (SecretConfig, etc.)
"""

from pragma_sdk.client import (
    AsyncPragmaClient,
    AsyncProjectResources,
    PragmaClient,
    ProjectResources,
)
from pragma_sdk.exceptions import (
    ProjectHasResourcesError,
    ProjectMismatchError,
    ProviderVersionConflictError,
    ResourceFailedError,
)
from pragma_sdk.models import (
    BuildInfo,
    BuildStatus,
    Config,
    CreateProjectRequest,
    DeleteProjectRequest,
    Dependency,
    DeploymentResult,
    DeploymentStatus,
    Field,
    FieldReference,
    FileField,
    FileReference,
    Immutable,
    ImmutableDependency,
    ImmutableField,
    ImmutableSensitiveField,
    InvalidResourceIdentityError,
    Organization,
    OrganizationStatus,
    Outputs,
    PaginatedResponse,
    Project,
    ProviderAuthor,
    ProviderDeleteResult,
    ProviderInfo,
    ProviderInstallation,
    ProviderScope,
    ProviderVersion,
    ProviderVersionMetadata,
    PushResult,
    Resource,
    ResourceIdentity,
    ResourceSchema,
    Sensitive,
    SensitiveDependency,
    SensitiveField,
    SensitiveOutput,
    UpdateProjectRequest,
    UpgradePolicy,
    VersionStatus,
)
from pragma_sdk.models import Provider as ProviderModel
from pragma_sdk.provider import Provider
from pragma_sdk.types import (
    CompatibilityConstraint,
    CopyContext,
    CopyResult,
    CopyStrategy,
    HealthStatus,
    LifecycleState,
    LogEntry,
    PatchDefinition,
    PatchResult,
)


__all__ = [
    "AsyncPragmaClient",
    "AsyncProjectResources",
    "BuildInfo",
    "BuildStatus",
    "CompatibilityConstraint",
    "Config",
    "CopyContext",
    "CopyResult",
    "CopyStrategy",
    "CreateProjectRequest",
    "DeleteProjectRequest",
    "Dependency",
    "DeploymentResult",
    "DeploymentStatus",
    "Field",
    "FieldReference",
    "FileField",
    "FileReference",
    "HealthStatus",
    "Immutable",
    "ImmutableDependency",
    "ImmutableField",
    "ImmutableSensitiveField",
    "InvalidResourceIdentityError",
    "LifecycleState",
    "LogEntry",
    "Organization",
    "OrganizationStatus",
    "Outputs",
    "PaginatedResponse",
    "PatchDefinition",
    "PatchResult",
    "PragmaClient",
    "Project",
    "ProjectHasResourcesError",
    "ProjectMismatchError",
    "ProjectResources",
    "Provider",
    "ProviderAuthor",
    "ProviderDeleteResult",
    "ProviderInfo",
    "ProviderInstallation",
    "ProviderModel",
    "ProviderScope",
    "ProviderVersion",
    "ProviderVersionConflictError",
    "ProviderVersionMetadata",
    "PushResult",
    "Resource",
    "ResourceFailedError",
    "ResourceIdentity",
    "ResourceSchema",
    "Sensitive",
    "SensitiveDependency",
    "SensitiveField",
    "SensitiveOutput",
    "UpdateProjectRequest",
    "UpgradePolicy",
    "VersionStatus",
]
