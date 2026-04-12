"""LLM infrastructure models: catalog, organization settings, and cost estimates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field as PydanticField

from pragma_sdk.models.enums import ModelTier, PerformanceProfile


class CatalogEntry(BaseModel):
    """A single row in the platform LLM catalog.

    Describes a concrete model offered by an LLM provider, its tier
    classification, pricing, and feature support. The API uses catalog
    entries to resolve an organization's performance profile into a
    concrete model for platform agent invocations.

    Attributes:
        provider: Provider slug (e.g. 'anthropic', 'openai', 'google').
        model_id: Concrete model identifier (e.g. 'claude-sonnet-4-6',
            'gpt-5.4-mini', 'gemini-3.1-pro').
        label: Human-readable label shown in the UI (e.g. 'Claude Sonnet 4.6').
        tier: Capability tier that maps to a PerformanceProfile selection.
        context_window: Maximum context window in tokens.
        input_price_per_mtok: USD price per million input tokens.
        output_price_per_mtok: USD price per million output tokens.
        supports_structured_output: Whether the model supports JSON schema
            constrained output. Defaults to True.
        supports_vision: Whether the model accepts image inputs. Defaults to False.
    """

    provider: str
    model_id: str
    label: str
    tier: ModelTier
    context_window: int
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal
    supports_structured_output: bool = True
    supports_vision: bool = False


class OrganizationSettings(BaseModel):
    """Per-organization LLM settings for platform agents.

    Holds the organization's choice of LLM provider resource and
    performance profile. The API reads these settings whenever a
    platform agent is invoked to resolve the concrete model and
    credentials to use.

    Attributes:
        organization_id: Owning organization.
        provider: Resource identifier of the LLM provider resource the
            organization has selected. Opaque string; format matches the
            rest of the SDK's resource id references (SurrealDB record id
            shape, e.g. 'resources:anthropic_default_abc123'). Use
            'platform_default' to reference the shared platform provider.
        performance_profile: Which tier of catalog model to use for
            platform agent invocations.
        updated_at: Timestamp of the last settings change.
    """

    organization_id: str
    provider: str
    performance_profile: PerformanceProfile
    updated_at: datetime


class ProviderComparisonRow(BaseModel):
    """One row in the provider comparison table shown in the settings UI.

    Attributes:
        provider: Provider slug (e.g. 'anthropic', 'openai', 'google').
        performance_profile: Profile tier this row estimates.
        monthly_estimate_usd: Projected monthly cost in USD at the
            standard assumption documented on CostEstimate.
    """

    provider: str
    performance_profile: PerformanceProfile
    monthly_estimate_usd: Decimal


class CostEstimate(BaseModel):
    """Cost projection for a proposed LLM provider and performance profile.

    Returned by the settings cost preview endpoint so the UI can show a
    live comparison before the user commits to a change. The standard
    assumption used for the projection is 10,000 platform-agent
    invocations per month with an average of 2,000 input tokens and 500
    output tokens per invocation; the API applies this assumption
    uniformly across providers so the comparison is apples-to-apples.

    Attributes:
        provider: Provider slug the estimate targets.
        performance_profile: Profile tier the estimate targets.
        monthly_estimate_usd: Projected monthly cost in USD at the
            standard assumption.
        per_call_estimate_usd: Projected cost per platform-agent
            invocation in USD at the standard assumption.
        provider_comparison: Alternative provider/profile combinations
            and their monthly cost estimates, for side-by-side display.
    """

    provider: str
    performance_profile: PerformanceProfile
    monthly_estimate_usd: Decimal
    per_call_estimate_usd: Decimal
    provider_comparison: list[ProviderComparisonRow] = PydanticField(default_factory=list)


class LLMProviderSummary(BaseModel):
    """Summary entry for the 'available LLM providers' settings picker.

    One summary row per LLM provider the organization can select. Tells
    the UI which providers are already connected, which tiers each
    provider supports, and which entry represents the shared platform
    default (used when the organization has not connected their own
    provider yet).

    Attributes:
        slug: Provider slug. Use 'platform_default' for the shared
            platform-managed provider, otherwise a concrete LLM provider
            slug like 'anthropic', 'openai', or 'google'.
        label: Human-readable label for the provider.
        connected: Whether the organization already has an LLM provider
            resource configured for this slug. Always True for the
            platform default entry.
        is_platform_default: True only for the 'platform_default' entry.
            Defaults to False.
        tiers_available: ModelTiers this provider exposes through its
            catalog. Drives which PerformanceProfile options are
            selectable when the user picks this provider.
    """

    slug: str
    label: str
    connected: bool
    is_platform_default: bool = False
    tiers_available: list[ModelTier] = PydanticField(default_factory=list)
