"""Python SDK for the Pragmatiks platform.

Core exports for provider authoring:
    Provider: Decorator for defining providers.
    Resource: Base class for provider resources.
    ChildrenPending: Returned from a lifecycle method to wait for the resources it applied.
    Config: Base class for resource configuration.
    Outputs: Base class for resource outputs.
    Field, FieldReference, Dependency: For cross-resource references.

Core exports for API consumers:
    PragmaClient: Synchronous HTTP client.
    AsyncPragmaClient: Asynchronous HTTP client.

Additional imports available from submodules:
    pragma_sdk.models: Data models (ProviderVersion, ProviderInstallation, etc.)
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
    AlreadyExistsError,
    CallbackRefusedError,
    ProjectHasResourcesError,
    ProjectMismatchError,
    RenamedWheelWriteError,
    ResourceFailedError,
    WheelExpansionLimitError,
    WheelUploadError,
    WheelUploadExpiredError,
)
from pragma_sdk.models import (
    SETTLED_VERSION_STATUSES,
    AdmissionFailureCategory,
    ChildrenPending,
    Config,
    CreateProjectRequest,
    DeclaredProviderVersion,
    DeleteProjectRequest,
    Dependency,
    Field,
    FieldReference,
    FileField,
    FileReference,
    Immutable,
    ImmutableDependency,
    ImmutableField,
    ImmutableSensitiveField,
    InstallationLifecycleState,
    InstallationOperationType,
    InvalidResourceIdentityError,
    LifecycleEventFrame,
    MemberRole,
    Organization,
    OrganizationBootstrapState,
    OrganizationBootstrapStatus,
    OrganizationMember,
    OrganizationStatus,
    Outputs,
    PaginatedResponse,
    Project,
    ProviderAuthor,
    ProviderInstallation,
    ProviderInstallationEvent,
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
    VersionStatus,
    WheelUploadAddress,
    WheelUploadOutcome,
)
from pragma_sdk.models import Provider as ProviderModel
from pragma_sdk.protocol.constants import MAXIMUM_OPERATION_DEADLINE_SECONDS
from pragma_sdk.provider import (
    DuplicateResourceTypeError,
    MissingObserveError,
    MissingOutputsError,
    OperationDeadlineOutOfRangeError,
    Provider,
)
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
from pragma_sdk.wheel_rename import rename_provider_wheel


__all__ = [
    "MAXIMUM_OPERATION_DEADLINE_SECONDS",
    "SETTLED_VERSION_STATUSES",
    "AdmissionFailureCategory",
    "AlreadyExistsError",
    "AsyncPragmaClient",
    "AsyncProjectResources",
    "CallbackRefusedError",
    "ChildrenPending",
    "CompatibilityConstraint",
    "Config",
    "CopyContext",
    "CopyResult",
    "CopyStrategy",
    "CreateProjectRequest",
    "DeclaredProviderVersion",
    "DeleteProjectRequest",
    "Dependency",
    "DuplicateResourceTypeError",
    "Field",
    "FieldReference",
    "FileField",
    "FileReference",
    "HealthStatus",
    "Immutable",
    "ImmutableDependency",
    "ImmutableField",
    "ImmutableSensitiveField",
    "InstallationLifecycleState",
    "InstallationOperationType",
    "InvalidResourceIdentityError",
    "LifecycleEventFrame",
    "LifecycleState",
    "LogEntry",
    "MemberRole",
    "MissingObserveError",
    "MissingOutputsError",
    "OperationDeadlineOutOfRangeError",
    "Organization",
    "OrganizationBootstrapState",
    "OrganizationBootstrapStatus",
    "OrganizationMember",
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
    "ProviderInstallation",
    "ProviderInstallationEvent",
    "ProviderModel",
    "ProviderScope",
    "ProviderVersion",
    "ProviderVersionDeclaration",
    "RenamedWheelWriteError",
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
    "VersionStatus",
    "WheelExpansionLimitError",
    "WheelUploadAddress",
    "WheelUploadError",
    "WheelUploadExpiredError",
    "WheelUploadOutcome",
    "read_wheel_declaration",
    "rename_provider_wheel",
]
