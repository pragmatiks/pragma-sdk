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
    BuildInfo,
    BuildStatus,
    Config,
    Dependency,
    DeploymentStatus,
    Field,
    FieldReference,
    InstalledProvider,
    InstalledProviderSummary,
    Outputs,
    PaginatedResponse,
    ProviderDeleteResult,
    ProviderInfo,
    PushResult,
    Resource,
    ResourceTier,
    StoreAuthor,
    StoreProvider,
    StoreProviderDetail,
    StoreProviderSummary,
    StoreVersion,
    StoreVersionDetail,
    TrustTier,
    UpgradePolicy,
    VersionStatus,
)
from pragma_sdk.provider import Provider
from pragma_sdk.types import HealthStatus, LifecycleState, LogEntry


__all__ = [
    "AsyncPragmaClient",
    "BuildInfo",
    "BuildStatus",
    "Config",
    "Dependency",
    "DeploymentStatus",
    "Field",
    "FieldReference",
    "HealthStatus",
    "InstalledProvider",
    "InstalledProviderSummary",
    "LifecycleState",
    "LogEntry",
    "Outputs",
    "PaginatedResponse",
    "PragmaClient",
    "Provider",
    "ProviderDeleteResult",
    "ProviderInfo",
    "PushResult",
    "Resource",
    "ResourceTier",
    "StoreAuthor",
    "StoreProvider",
    "StoreProviderDetail",
    "StoreProviderSummary",
    "StoreVersion",
    "StoreVersionDetail",
    "TrustTier",
    "UpgradePolicy",
    "VersionStatus",
]
