"""Typed file reference for provider Config fields.

Pragmatiks resolves ``pragma://files/<name>`` config values to
short-lived signed download URLs before invoking provider lifecycle
methods. ``FileReference`` wraps such a URL and exposes async helpers to
download the bytes, stream chunks, or save to disk.

Provider authors declare file inputs via :data:`FileField` (in
``pragma_sdk.models.references``) and consume them through these helpers
instead of writing raw ``httpx`` calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, GetJsonSchemaHandler, ValidationInfo, field_validator, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


_PRAGMA_FILE_SCHEME = "pragma://files/"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PRAGMA_FILE_PATTERN = "^pragma://files/.+$"

_FILE_REFERENCES_CONTEXT_KEY = "file_references"
"""Validation context key holding the original references keyed by the signed URL each resolved to."""


def build_file_reference_context(resolved_files: dict[str, str]) -> dict[str, dict[str, str]]:
    """Build the validation context that lets a ``FileReference`` carry its original reference.

    Args:
        resolved_files: Signed URLs keyed by original Pragmatiks file reference.

    Returns:
        A context for ``model_validate`` holding each original reference
        keyed by the signed URL it resolved to.
    """
    return {_FILE_REFERENCES_CONTEXT_KEY: {signed: reference for reference, signed in resolved_files.items()}}


class FileReference(BaseModel):
    """Typed reference to a file the runtime has resolved for this provider.

    The runtime substitutes ``pragma://files/<name>`` config values with a
    signed HTTPS URL before pydantic validates the Config. ``FileReference``
    accepts that string directly and exposes async I/O helpers that fetch,
    stream, or save the file. A fresh ``httpx.AsyncClient`` is created per
    call to avoid sharing connection state across unrelated downloads.

    The published JSON schema is the applied form, a ``pragma://files/<name>``
    string, since Pragmatiks validates the applied config against it before
    the runtime resolves the reference.

    Attributes:
        url: Signed HTTPS URL produced by the runtime's file resolver.
        name: The ``pragma://files/<name>`` reference the address was
            signed for; ``None`` for a config the worker did not build.
    """

    url: str
    name: str | None = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        """Publish the applied ``pragma://files/<name>`` string as the schema.

        Args:
            core_schema: Pydantic core schema of the model, unused.
            handler: Pydantic JSON schema handler, unused.

        Returns:
            A string schema matching a Pragmatiks file reference.
        """
        return {
            "type": "string",
            "pattern": _PRAGMA_FILE_PATTERN,
            "description": "Pragmatiks file reference, `pragma://files/<name>`.",
        }

    @model_validator(mode="before")
    @classmethod
    def _accept_url_string(cls, value: Any, info: ValidationInfo) -> Any:
        """Coerce the input into a ``FileReference`` payload.

        Pydantic invokes this before model construction so a Config field
        annotated with :data:`FileField` accepts both the dict form
        (``{"url": ...}``) and the plain string that the runtime substitutes
        into string slots. With a validation context from
        :func:`build_file_reference_context`, ``name`` always comes from the
        context by URL, so a caller-supplied ``name`` is never trusted.

        Args:
            value: Raw input value passed to model validation.
            info: Validation info whose context, when set, maps signed URLs
                to their original references.

        Returns:
            For a string, or a dict validated with a file-references context,
            a dict with ``url`` set to the URL and ``name`` set to the
            original reference from the context, or ``None`` when the context
            has none; any other value unchanged.
        """
        references = (info.context or {}).get(_FILE_REFERENCES_CONTEXT_KEY)

        if isinstance(value, str):
            return {"url": value, "name": (references or {}).get(value)}

        if references is not None and isinstance(value, dict):
            url = value.get("url")
            return {"url": url, "name": references.get(url) if isinstance(url, str) else None}

        return value

    @field_validator("url", mode="before")
    @classmethod
    def _reject_unresolved_reference(cls, value: Any) -> Any:
        """Reject an unsubstituted ``pragma://files/<name>`` reference.

        If the URL still carries the ``pragma://files/`` scheme, the runtime
        did not resolve it, almost always because the runtime image is too
        old to support inline file resolution. Failing here surfaces the
        upgrade requirement instead of letting the provider try to ``GET`` a
        non-HTTP URL.

        Args:
            value: Candidate URL string.

        Returns:
            The value unchanged when it does not carry the pragma scheme.

        Raises:
            ValueError: When the value is an unresolved pragma file reference.
        """
        if isinstance(value, str) and value.startswith(_PRAGMA_FILE_SCHEME):
            raise ValueError(
                f"FileReference received unresolved file reference {value!r}. "
                "Pragmatiks did not substitute this reference — "
                "your runtime image is likely outdated. Upgrade to a runtime "
                "that resolves pragma:// file references inline."
            )
        return value

    async def fetch(self) -> bytes:
        """Download the file in a single request and return its bytes.

        Returns:
            Raw file content.

        Raises:
            httpx.HTTPStatusError: If the server returns a non-2xx response.
            httpx.HTTPError: For network, timeout, or protocol failures.
        """  # noqa: DOC502
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            return response.content

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the file body as a sequence of byte chunks.

        Use this for files large enough that holding the full body in memory
        would be wasteful. Chunk size is whatever httpx decides per response.

        Yields:
            Successive chunks of the response body.

        Raises:
            httpx.HTTPStatusError: If the server returns a non-2xx response.
            httpx.HTTPError: For network, timeout, or protocol failures.
        """  # noqa: DOC502
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", self.url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    async def save(self, path: Path) -> None:
        """Stream the file to ``path`` on local disk.

        Args:
            path: Destination path. Parent directories must already exist.
                Any existing file at ``path`` is overwritten.

        Raises:
            httpx.HTTPStatusError: If the server returns a non-2xx response.
            httpx.HTTPError: For network, timeout, or protocol failures.
            OSError: If the file cannot be opened or written.
        """  # noqa: DOC502
        with path.open("wb") as file_handle:
            async for chunk in self.stream():
                file_handle.write(chunk)
