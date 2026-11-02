"""The top-level keys a model class accepts when it validates data."""

from __future__ import annotations

from pydantic import AliasChoices, AliasPath, BaseModel
from pydantic.fields import FieldInfo


def compute_declared_keys(model_class: type[BaseModel]) -> set[str]:
    """Compute the top-level keys a model class accepts, such as a Config or an Outputs class.

    Args:
        model_class: Model class the data is read as.

    Returns:
        Every field's name and alias, its validation alias, each choice of an
        ``AliasChoices`` validation alias, and the first key of each
        ``AliasPath``.
    """
    declared_keys: set[str] = set()

    for name, field in model_class.model_fields.items():
        declared_keys.add(name)
        declared_keys.update(compute_field_alias_keys(field))

    return declared_keys


def compute_field_alias_keys(field: FieldInfo) -> set[str]:
    """Compute the top-level keys a field accepts through its aliases.

    Args:
        field: The field.

    Returns:
        Its alias, its validation alias, each choice of an ``AliasChoices``
        validation alias, and the first key of each ``AliasPath``.
    """
    aliases: list[str | AliasPath | AliasChoices | None] = [field.alias, field.validation_alias]

    if isinstance(field.validation_alias, AliasChoices):
        aliases.extend(field.validation_alias.choices)

    alias_keys = {alias for alias in aliases if isinstance(alias, str)}
    alias_keys.update(
        alias.path[0] for alias in aliases if isinstance(alias, AliasPath) and isinstance(alias.path[0], str)
    )

    return alias_keys
