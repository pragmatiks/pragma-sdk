"""Settings the Pragmatiks host passes to a worker or an introspection run."""

from __future__ import annotations

from typing import Any, cast

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    """Provider distribution to serve, read from ``PRAGMA_PROVIDER_*`` environment variables.

    Attributes:
        distribution: Wheel distribution name of the provider, from
            ``PRAGMA_PROVIDER_DISTRIBUTION``. Required: construction fails
            when it is unset.
    """

    model_config = SettingsConfigDict(env_prefix="PRAGMA_PROVIDER_")

    distribution: str


def load_provider_settings() -> ProviderSettings:
    """Read the provider settings from the environment.

    Returns:
        The settings.

    Raises:
        ValidationError: If ``PRAGMA_PROVIDER_DISTRIBUTION`` is unset.
    """  # noqa: DOC502
    return cast("Any", ProviderSettings)()
