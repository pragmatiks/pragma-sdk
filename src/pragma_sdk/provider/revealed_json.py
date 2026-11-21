"""JSON encoding of provider models with secret values revealed."""

from __future__ import annotations

from typing import Any

from pragma_sdk.protocol.codec import build_json_payload


def build_revealed_json(value: Any) -> Any:
    """Encode a model as JSON-compatible data with every secret value revealed.

    Use it where a provider writes a model into a plain-text carrier, such as
    an environment variable, that must hold the real secret: ``str()`` and a
    JSON-mode ``model_dump`` write asterisks in place of a ``SecretStr`` or
    ``SecretBytes`` value. The result is the form the worker sends the host.

    Args:
        value: A model, mapping, sequence or scalar to encode.

    Returns:
        Dicts, lists and scalars ready for ``json.dumps``, with every
        ``Secret``, ``SecretStr`` and ``SecretBytes`` value replaced by the
        value it holds.

    Raises:
        UnicodeDecodeError: If a bytes value, secret or not, is not UTF-8.
    """  # noqa: DOC502
    return build_json_payload(value)
