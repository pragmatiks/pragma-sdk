"""Uploads of a project's files straight to storage: the declaration, its answer and where an upload stands."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from pragma_sdk.models.upload import UploadAddress


class FileUploadDeclaration(BaseModel):
    """What an upload of one file sends, declared before any byte moves.

    Storage refuses bytes of any other size, content type or digest.

    Attributes:
        size: Size of the file in bytes.
        content_type: Media type the file is stored and served with, such as
            ``application/pdf``.
        md5: Base64 of the file's 16-byte MD5 digest, as ``Content-MD5``
            carries it.
    """

    size: int = Field(ge=0)
    content_type: str
    md5: str


class DeclaredFileUpload(BaseModel):
    """The answer to an upload declaration.

    Its input carries the signed upload address, which a failed validation
    shows, and its serialization carries, as :class:`UploadAddress`
    describes.

    Attributes:
        operation_id: Upload operation the declaration started or joined.
        upload: Address the bytes are sent to with one ``PUT``; ``None`` when
            nothing is left to send, because the bytes already reached storage.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    operation_id: str = Field(min_length=1)
    upload: UploadAddress | None = None


class FileMetadata(BaseModel):
    """A stored file of a project.

    Attributes:
        url: ``pragma://files/<name>`` reference resource configs use.
        public_url: Address that downloads the file with credentials, by redirect.
        size: Size in bytes.
        content_type: Media type the file is served with.
        checksum: Hex MD5 of the content, which storage verified when the file was stored.
        uploaded_at: When the file's current content was stored.
    """

    url: str
    public_url: str
    size: int
    content_type: str
    checksum: str
    uploaded_at: datetime


class FileUploadOutcome(StrEnum):
    """Where the latest upload of a file stands.

    Attributes:
        PENDING: The bytes are expected, or are being stored.
        STORED: The file holds the upload's content.
        DELETED: The upload was stored, and the file was deleted or replaced since.
        FAILED: Storing the upload failed; upload it again.
        EXPIRED: The bytes did not reach storage before the address expired; upload it again.
    """

    PENDING = "pending"
    STORED = "stored"
    DELETED = "deleted"
    FAILED = "failed"
    EXPIRED = "expired"


class FileUploadProgress(BaseModel):
    """The latest upload of a file, as ``GET /projects/{project_id}/files/{name}/upload`` answers it.

    Attributes:
        operation_id: Upload operation.
        outcome: Where the upload stands.
        stored_bytes: While ``pending``, bytes copied into place so far; otherwise ``None``.
        file: The stored file when ``stored``, else ``None``.
        error_message: Why the upload ended, for ``failed`` and ``expired``, else ``None``.
    """

    operation_id: str
    outcome: FileUploadOutcome
    stored_bytes: int | None = None
    file: FileMetadata | None = None
    error_message: str | None = None
