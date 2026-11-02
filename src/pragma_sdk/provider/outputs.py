"""Normalization of a lifecycle method's outputs into the data the host compares."""

from __future__ import annotations

from typing import Any

import structlog

from pragma_sdk.models.base import Outputs, Resource, derive_outputs_class
from pragma_sdk.models.declared_keys import compute_declared_keys
from pragma_sdk.protocol.codec import build_revealed_payload


logger = structlog.get_logger()


def normalize_outputs(resource_class: type[Resource], outputs: Any) -> dict[str, Any] | None:
    """Validate a lifecycle method's outputs into the dict the host receives.

    The outputs, an Outputs instance of any subclass or a dict, are read as
    plain data and validated against the resource class's Outputs class, so
    the result holds exactly the properties that class declares. Each
    property is read by its name or by its alias. Dropped
    top-level keys are logged as ``outputs_keys_ignored`` by name, never by
    value. A create's, an update's and an observe's outputs normalize the
    same way, so once the codec encodes them the host compares them as
    plain dicts.

    Args:
        resource_class: The resource class whose Outputs class validates the outputs.
        outputs: What the lifecycle method returned.

    Returns:
        The validated outputs, secret values still wrapped until the codec
        encodes them, or ``None`` when the method returned ``None``.

    Raises:
        ValueError: If the method returned outputs and the resource class
            declares no Outputs class.
        ValidationError: If the outputs do not match the Outputs class.
    """  # noqa: DOC502
    if outputs is None:
        return None

    outputs_class = derive_outputs_class(resource_class)

    if outputs_class is None:
        raise ValueError(
            f"{resource_class.__qualname__} returned outputs but declares no Outputs class. "
            "Declare it as Resource[YourConfig, YourOutputs], then publish again."
        )

    outputs_data = build_revealed_payload(outputs)
    log_ignored_outputs_keys(resource_class, outputs_class, outputs_data)

    return outputs_class.model_validate(outputs_data, by_alias=True, by_name=True).model_dump()


def log_ignored_outputs_keys(resource_class: type[Resource], outputs_class: type[Outputs], outputs_data: Any) -> None:
    """Log the top-level outputs keys an Outputs class does not declare, by name only.

    Args:
        resource_class: The resource class whose method returned the outputs.
        outputs_class: The Outputs class the outputs are validated against.
        outputs_data: The outputs as plain data.
    """
    if not isinstance(outputs_data, dict):
        return

    declared_keys = compute_declared_keys(outputs_class)
    ignored_keys = sorted(str(key) for key in outputs_data if key not in declared_keys)

    if ignored_keys:
        logger.info("outputs_keys_ignored", resource_type=resource_class.__name__, ignored_keys=ignored_keys)
