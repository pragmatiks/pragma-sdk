"""The address declared bytes are uploaded to, straight to storage, and how storage took an upload."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


UPLOAD_URL_SCHEMES = frozenset({"http", "https"})
"""URL schemes a signed upload address may use."""

UPLOAD_URL_PORTS = range(1, 65536)
"""Ports a signed upload address may name."""

UPLOAD_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
"""An upload header name httpx can send: an HTTP token."""

UPLOAD_HEADER_VALUE_PATTERN = re.compile(r"(?:[!-~]+(?:[ \t]+[!-~]+)*)?")
"""An upload header value httpx can send.

Empty, or printable ASCII words joined by spaces or tabs, with none around them.
"""


class UploadAddress(BaseModel):
    """Where declared bytes are uploaded, straight to storage: a provider version's wheel or a project's file.

    The bytes are sent as the body of one ``PUT`` to ``url`` carrying
    ``headers`` verbatim; storage accepts exactly the declared size, once.

    Whoever holds ``url`` can write the declared bytes until ``expires_at``,
    so it is a :class:`~pydantic.SecretStr`, masked in the model's
    representation and JSON serialization, and a failed validation's
    ``str()`` and ``repr()`` leave the input out; its ``errors()`` and
    ``json()`` keep the input unless called with ``include_input=False``.
    A JSON-serialized address carries the mask, not the address, so it does
    not validate back; a Python-mode ``model_dump()`` keeps the ``SecretStr``
    and validates back to the address.

    Validation refuses a ``url`` that is not an ``http`` or ``https`` URL
    with a host, and ``headers`` whose names or values httpx cannot send;
    neither refusal's message carries the address.

    Attributes:
        url: Signed address the bytes are uploaded to; ``get_secret_value()``
            gives it.
        headers: Headers the upload must send unchanged; the address is
            signed over them.
        expires_at: When the address stops accepting the upload.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    url: SecretStr
    headers: dict[str, str]
    expires_at: datetime

    @field_validator("url")
    @classmethod
    def require_http_url(cls, url: SecretStr) -> SecretStr:
        """Refuse an address that is not an ``http`` or ``https`` URL with a host and a usable port.

        Args:
            url: The signed address.

        Returns:
            The address, unchanged.

        Raises:
            ValueError: When httpx cannot parse the address, or it has
                another scheme, no host, or a port outside 1 to 65535; the
                message never carries the address.
        """
        try:
            parsed_url: httpx.URL | None = httpx.URL(url.get_secret_value())
        except httpx.InvalidURL:
            parsed_url = None

        if (
            parsed_url is None
            or parsed_url.scheme not in UPLOAD_URL_SCHEMES
            or not parsed_url.host
            or (parsed_url.port is not None and parsed_url.port not in UPLOAD_URL_PORTS)
        ):
            raise ValueError("The upload address must be an http or https URL with a host")

        return url

    @field_validator("headers")
    @classmethod
    def require_sendable_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """Refuse headers whose names or values httpx cannot send.

        Args:
            headers: The headers the upload must send unchanged.

        Returns:
            The headers, unchanged.

        Raises:
            ValueError: When a name is not an HTTP token, or a value is
                neither empty nor printable ASCII words joined by spaces or
                tabs, with none around them; the message names the header,
                not its value.
        """
        for name, value in headers.items():
            if not UPLOAD_HEADER_NAME_PATTERN.fullmatch(name) or not UPLOAD_HEADER_VALUE_PATTERN.fullmatch(value):
                raise ValueError(f"The upload header {name!r} cannot be sent in an HTTP request")

        return headers


class UploadOutcome(StrEnum):
    """How storage took bytes uploaded to their address.

    Either outcome leaves the bytes stored under the address.
    """

    STORED = "stored"
    ALREADY_STORED = "already_stored"
