"""Credential discovery and configuration management for Pragma SDK."""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def credentials_file_path() -> Path:
    """Return the credentials file path.

    Returns:
        Path to the credentials file, respecting XDG_CONFIG_HOME if set.
    """
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        config_dir = Path(xdg_config_home) / "pragma"
    else:
        config_dir = Path.home() / ".config" / "pragma"
    return config_dir / "credentials"


def load_credentials(context: str) -> str | None:
    """Load authentication token for a context from the credentials file.

    Args:
        context: Context name to load credentials for.

    Returns:
        Token string, or None if context not found.
    """
    creds_file = credentials_file_path()
    if not creds_file.exists():
        return None

    try:
        for line in creds_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):  # Skip empty lines and comments
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                if key.strip() == context:
                    return value.strip()
    except OSError:
        return None

    return None


def get_current_context_from_config() -> str | None:
    """Read the current context from the CLI config file.

    Returns:
        Context name, or None if not configured.
    """
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        config_dir = Path(xdg_config_home) / "pragma"
    else:
        config_dir = Path.home() / ".config" / "pragma"

    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        return None

    try:
        config = yaml.safe_load(config_file.read_text())
        return config.get("current_context") if config else None
    except (OSError, yaml.YAMLError):
        return None


def get_token_for_context(context: str | None = None) -> str | None:
    """Discover authentication token for a context.

    Checks environment variables first, then credentials file.

    Args:
        context: Context name. If None, uses PRAGMA_CONTEXT env var,
            CLI config, or 'default'.

    Returns:
        Token string, or None if not found.
    """
    if context is None:
        context = os.getenv("PRAGMA_CONTEXT")
        if context is None:
            context = get_current_context_from_config()
        if context is None:
            context = "default"

    context_env_var = f"PRAGMA_AUTH_TOKEN_{context.upper()}"
    context_token = os.getenv(context_env_var)
    if context_token:
        return context_token

    generic_token = os.getenv("PRAGMA_AUTH_TOKEN")
    if generic_token:
        return generic_token

    file_token = load_credentials(context)
    if file_token:
        return file_token

    return None
