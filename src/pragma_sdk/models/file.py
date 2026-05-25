"""Typed file reference for provider Config fields.

The pragma-os runtime resolves ``pragma://files/<name>`` config values to
short-lived signed download URLs before invoking provider lifecycle
methods. ``FileRef`` wraps such a URL and exposes async helpers to
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
from pydantic import BaseModel, field_validator, model_validator


_PRAGMA_FILE_SCHEME = "pragma://files/"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class FileRef(BaseModel):
    """Typed reference to a file the runtime has resolved for this provider.

    The runtime substitutes ``pragma://files/<name>`` config values with a
    signed HTTPS URL before pydantic validates the Config. ``FileRef``
    accepts that string directly and exposes async I/O helpers that fetch,
    stream, or save the file. A fresh ``httpx.AsyncClient`` is created per
    call to avoid sharing connection state across unrelated downloads.

    Attributes:
        url: Signed HTTPS URL produced by the runtime's file resolver.
        name: Original ``pragma://files/<name>`` reference, when known.
            Populated by callers for logging; not required at validation.
    """

    url: str
    name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_url_string(cls, value: Any) -> Any:
        """Coerce a bare URL string into a ``FileRef`` payload.

        Pydantic invokes this before model construction so a Config field
        annotated with :data:`FileField` accepts both the dict form
        (``{"url": ..., "name": ...}``) and the plain string that the
        runtime substitutes into string slots.

        Args:
            value: Raw input value passed to model validation.

        Returns:
            A dict with the ``url`` key when ``value`` is a string,
            otherwise the value unchanged.
        """
        if isinstance(value, str):
            return {"url": value}
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _reject_unresolved_reference(cls, value: Any) -> Any:
        """Reject an unsubstituted ``pragma://files/<name>`` reference.

        If the URL still carries the ``pragma://files/`` scheme, the runtime
        did not resolve it — almost always because the runtime image is too
        old to support inline file resolution (see pragma-os merge
        ``49fd7648d8``). Failing here surfaces the upgrade requirement
        instead of letting the provider try to ``GET`` a non-HTTP URL.

        Args:
            value: Candidate URL string.

        Returns:
            The value unchanged when it does not carry the pragma scheme.

        Raises:
            ValueError: When the value is an unresolved pragma file reference.
        """
        if isinstance(value, str) and value.startswith(_PRAGMA_FILE_SCHEME):
            raise ValueError(
                f"FileRef received unresolved file reference {value!r}. "
                "The pragma-os runtime did not substitute this reference — "
                "your runtime image is likely outdated. Upgrade to a runtime "
                "that resolves pragma:// file references inline "
                "(see pragma-os merge 49fd7648d8)."
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
