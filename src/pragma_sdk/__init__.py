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

from pragma_sdk.client import AsyncPragmaClient, PragmaClient
from pragma_sdk.models import (
    AgentInstance,
    AgentInstanceStatus,
    AgentLogType,
    AgentType,
    AgentTypeCreate,
    AgentTypeUpdate,
    BuildInfo,
    BuildStatus,
    Config,
    ConversationRoutingManifest,
    Dependency,
    DeploymentResult,
    DeploymentStatus,
    Field,
    FieldReference,
    FleetVitals,
    Immutable,
    ImmutableDependency,
    ImmutableField,
    ImmutableSensitiveField,
    Organization,
    OrganizationStatus,
    Outputs,
    PaginatedResponse,
    ProviderAuthor,
    ProviderDeleteResult,
    ProviderInfo,
    ProviderInstallation,
    ProviderScope,
    ProviderVersion,
    PushResult,
    Resource,
    ResourceSchema,
    ResourceTier,
    ScheduleConfig,
    Sensitive,
    SensitiveDependency,
    SensitiveField,
    SensitiveOutput,
    Task,
    TaskCreate,
    TaskSource,
    TaskStatus,
    TaskUpdate,
    TrustTier,
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
    "AgentInstance",
    "AgentInstanceStatus",
    "AgentLogType",
    "AgentType",
    "AgentTypeCreate",
    "AgentTypeUpdate",
    "AsyncPragmaClient",
    "BuildInfo",
    "BuildStatus",
    "CompatibilityConstraint",
    "Config",
    "ConversationRoutingManifest",
    "CopyContext",
    "CopyResult",
    "CopyStrategy",
    "Dependency",
    "DeploymentResult",
    "DeploymentStatus",
    "Field",
    "FieldReference",
    "FleetVitals",
    "HealthStatus",
    "Immutable",
    "ImmutableDependency",
    "ImmutableField",
    "ImmutableSensitiveField",
    "LifecycleState",
    "LogEntry",
    "Organization",
    "OrganizationStatus",
    "Outputs",
    "PaginatedResponse",
    "PatchDefinition",
    "PatchResult",
    "PragmaClient",
    "Provider",
    "ProviderAuthor",
    "ProviderDeleteResult",
    "ProviderInfo",
    "ProviderInstallation",
    "ProviderModel",
    "ProviderScope",
    "ProviderVersion",
    "PushResult",
    "Resource",
    "ResourceSchema",
    "ResourceTier",
    "ScheduleConfig",
    "Sensitive",
    "SensitiveDependency",
    "SensitiveField",
    "SensitiveOutput",
    "Task",
    "TaskCreate",
    "TaskSource",
    "TaskStatus",
    "TaskUpdate",
    "TrustTier",
    "UpgradePolicy",
    "VersionStatus",
]
