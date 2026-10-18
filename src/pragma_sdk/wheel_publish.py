"""What a provider wheel publish needs besides its API calls.

The temporary directory the renamed wheel is written to, the note naming the
declared version on an error, and the version without its upload address.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from typing import TYPE_CHECKING

from pragma_sdk.blocking import run_in_worker_thread
from pragma_sdk.exceptions import RenamedWheelWriteError
from pragma_sdk.models import ProviderVersion
from pragma_sdk.wheel_rename import log_path_not_removed


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from pragma_sdk.models import DeclaredProviderVersion


logger = logging.getLogger(__name__)


class RenamedWheelDirectory:
    """The temporary directory a publish writes the renamed copy of its wheel to.

    Attributes:
        path: The directory's path once created, or ``None`` before.
    """

    def __init__(self) -> None:
        """Start without a directory."""
        self.path: str | None = None

    def create(self) -> str:
        """Create the directory in the system's temporary directory, which ``TMPDIR`` names.

        Returns:
            The directory's path, also kept in :attr:`path`.

        Raises:
            RenamedWheelWriteError: If the directory cannot be created; the
                ``OSError`` creating it raised is its ``__cause__``.
        """
        try:
            self.path = tempfile.mkdtemp(prefix="pragma-publish-")
        except OSError as error:
            raise RenamedWheelWriteError(error) from error

        return self.path

    def remove(self) -> None:
        """Remove the directory and everything in it, if it was created.

        Removal is best effort, so it never fails a publish whose declaration
        and upload succeeded: whatever cannot be removed is left in place and
        logged as :func:`~pragma_sdk.wheel_rename.log_path_not_removed` logs
        it, the ``publish_renamed_wheel_directory_not_removed`` event.
        """
        if self.path is not None:
            shutil.rmtree(self.path, onexc=log_removal_failure)


def log_removal_failure(_function: Callable[..., object], path: str, error: BaseException) -> None:
    """Log a path of the renamed wheel directory that could not be removed.

    Args:
        _function: The removal function that failed.
        path: The path it failed on.
        error: What it raised.
    """
    log_path_not_removed(logger, "publish_renamed_wheel_directory_not_removed", path, error)


@contextlib.contextmanager
def temporary_renamed_wheel_directory() -> Iterator[str]:
    """Create the temporary directory a publish writes the renamed copy of its wheel to, and remove it on exit.

    Yields:
        The directory's path, removed as :meth:`RenamedWheelDirectory.remove` describes.

    Raises:
        RenamedWheelWriteError: If the directory cannot be created.
    """  # noqa: DOC502
    directory = RenamedWheelDirectory()

    try:
        yield directory.create()
    finally:
        directory.remove()


@contextlib.asynccontextmanager
async def temporary_renamed_wheel_directory_async() -> AsyncIterator[str]:
    """Async mirror of :func:`temporary_renamed_wheel_directory`, off the event loop.

    A directory created while the caller was being cancelled is removed too.

    Yields:
        The directory's path, removed as :meth:`RenamedWheelDirectory.remove` describes.

    Raises:
        RenamedWheelWriteError: If the directory cannot be created.
    """  # noqa: DOC502
    directory = RenamedWheelDirectory()

    try:
        yield await run_in_worker_thread(directory.create)
    finally:
        await run_in_worker_thread(directory.remove)


@contextlib.contextmanager
def noting_declared_version(version: DeclaredProviderVersion) -> Iterator[None]:
    """Add a note naming a declared version to any error raised inside the block.

    The note names the provider, as ``prefix/name``, the version and the
    publish operation the platform's publish events are keyed by. It is one
    of the error's ``__notes__``, which a traceback and a logger given the
    exception show, and ``str()`` does not.

    Args:
        version: The declaration's answer.

    Yields:
        Nothing; the block runs as is.
    """
    try:
        yield
    except Exception as error:
        error.add_note(
            f"Declared as {version.canonical} {version.version} in publish operation {version.operation_id}."
        )
        raise


def build_version_without_upload_address(version: DeclaredProviderVersion) -> ProviderVersion:
    """Build the version a declaration returned, without its upload address.

    Args:
        version: The declaration's answer.

    Returns:
        The same version, as every other version response gives it.
    """
    return ProviderVersion.model_validate(version.model_dump(exclude={"wheel_upload"}))
