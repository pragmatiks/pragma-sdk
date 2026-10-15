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
    pragma_sdk.models: Data models (ProviderVersion, ProviderStatus, etc.)
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
    ResourceFailedError,
)
from pragma_sdk.models import (
    SETTLED_VERSION_STATUSES,
    AdmissionFailureCategory,
    Config,
    CreateProjectRequest,
    DeclaredProviderVersion,
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
    LifecycleEventFrame,
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
    ProviderVersionDeclaration,
    Resource,
    ResourceIdentity,
    ResourceSchema,
    Sensitive,
    SensitiveDependency,
    SensitiveField,
    SensitiveOutput,
    TeardownAction,
    TeardownImpact,
    TeardownResponse,
    UpdateProjectRequest,
    UpgradePolicy,
    VersionStatus,
)
from pragma_sdk.models import Provider as ProviderModel
from pragma_sdk.provider import MissingObserveError, Provider
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
from pragma_sdk.wheel import read_wheel_declaration


__all__ = [
    "SETTLED_VERSION_STATUSES",
    "AdmissionFailureCategory",
    "AsyncPragmaClient",
    "AsyncProjectResources",
    "CompatibilityConstraint",
    "Config",
    "CopyContext",
    "CopyResult",
    "CopyStrategy",
    "CreateProjectRequest",
    "DeclaredProviderVersion",
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
    "LifecycleEventFrame",
    "LifecycleState",
    "LogEntry",
    "MissingObserveError",
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
    "ProviderVersionDeclaration",
    "Resource",
    "ResourceFailedError",
    "ResourceIdentity",
    "ResourceSchema",
    "Sensitive",
    "SensitiveDependency",
    "SensitiveField",
    "SensitiveOutput",
    "TeardownAction",
    "TeardownImpact",
    "TeardownResponse",
    "UpdateProjectRequest",
    "UpgradePolicy",
    "VersionStatus",
    "read_wheel_declaration",
]
