"""Provider class for grouping Resource classes (like FastAPI's APIRouter)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pragma_sdk.models import Resource
from pragma_sdk.models.base import derive_outputs_class
from pragma_sdk.protocol.constants import MAXIMUM_OPERATION_DEADLINE_SECONDS


ResourceT = TypeVar("ResourceT", bound=Resource)

RESOURCE_MARKER = "__pragma_resource__"


class MissingObserveError(TypeError):
    """Raised when a non-computed resource type does not define ``on_observe``.

    Attributes:
        resource: Registered name of the offending resource type.
        module_name: Module defining the offending resource class.
    """

    def __init__(self, resource: str, module_name: str) -> None:
        """Build the error for a resource type.

        Args:
            resource: Registered name of the offending resource type.
            module_name: Module defining the offending resource class.
        """
        self.resource = resource
        self.module_name = module_name
        super().__init__(f'resource "{resource}" does not define on_observe. Add it, or declare the resource computed.')


class MissingOutputsError(TypeError):
    """Raised when a non-computed resource type declares no ``Outputs`` class.

    The platform observes a non-computed type before it acts on it, and an
    observe reports the object present only through outputs the type's
    ``Outputs`` class validates. A type without one could never be seen as
    present, so every create would fail and every delete would skip
    ``on_delete``.

    Attributes:
        resource: Registered name of the offending resource type.
        module_name: Module defining the offending resource class.
    """

    def __init__(self, resource: str, module_name: str) -> None:
        """Build the error for a resource type.

        Args:
            resource: Registered name of the offending resource type.
            module_name: Module defining the offending resource class.
        """
        self.resource = resource
        self.module_name = module_name
        super().__init__(
            f'resource "{resource}" declares no Outputs class. '
            "Declare one as Resource[YourConfig, YourOutputs], or declare the resource computed."
        )


class OperationDeadlineOutOfRangeError(TypeError):
    """Raised when a resource type declares an ``operation_deadline_seconds`` that is not a whole number in range.

    The accepted values are ``None`` and the ``int`` values from 1 to
    ``MAXIMUM_OPERATION_DEADLINE_SECONDS``; a ``bool`` is refused. The
    message never repeats the declared value.

    Attributes:
        resource: Registered name of the offending resource type.
        module_name: Module defining the offending resource class.
    """

    def __init__(self, resource: str, module_name: str) -> None:
        """Build the error for a resource type.

        Args:
            resource: Registered name of the offending resource type.
            module_name: Module defining the offending resource class.
        """
        self.resource = resource
        self.module_name = module_name
        super().__init__(self.build_message())

    def build_message(self) -> str:
        """Build the error's message.

        Returns:
            A message naming the resource type, the accepted range and how
            to fix the declaration.
        """
        return (
            f'resource "{self.resource}" declares operation_deadline_seconds outside 1 to '
            f"{MAXIMUM_OPERATION_DEADLINE_SECONDS} seconds. Set it within that range, or remove it to use the default."
        )


class OperationDeadlineFieldError(OperationDeadlineOutOfRangeError):
    """Raised when a resource type declares ``operation_deadline_seconds`` as an annotated field.

    Catch :class:`OperationDeadlineOutOfRangeError` to handle it with every
    other refused ``operation_deadline_seconds``.
    """

    def build_message(self) -> str:
        """Build the error's message.

        Returns:
            A message naming the resource type and how to declare the
            attribute instead.
        """
        return (
            f'resource "{self.resource}" declares operation_deadline_seconds as a field; '
            "write a bare `operation_deadline_seconds = <seconds>` without a type annotation."
        )


def is_operation_deadline_in_range(value: object) -> bool:
    """Tell whether a declared ``operation_deadline_seconds`` is accepted.

    Args:
        value: The declared value.

    Returns:
        Whether it is ``None``, or an ``int`` that is not a ``bool`` from 1
        to ``MAXIMUM_OPERATION_DEADLINE_SECONDS``.
    """
    if value is None:
        return True

    if isinstance(value, bool) or not isinstance(value, int):
        return False

    return 1 <= value <= MAXIMUM_OPERATION_DEADLINE_SECONDS


class Provider:
    """Group Resource classes under a provider.

    Provider identity (the catalog name like ``pragmatiks/gcp``) is an
    external concern injected at build time and runtime. The Provider
    class only groups Resource classes and sets their ``resource`` class
    variable.

    Example:
        from pragma_sdk import Provider, Resource, Config, Outputs

        postgres = Provider()

        @postgres.resource("database")
        class Database(Resource[DatabaseConfig, DatabaseOutputs]):
            async def on_create(self) -> DatabaseOutputs:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_observe(self) -> DatabaseOutputs | None:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_update(self, previous_config: DatabaseConfig | None) -> DatabaseOutputs:
                return DatabaseOutputs(connection_url=f"postgres://localhost/{self.config.name}")

            async def on_delete(self) -> None:
                pass
    """

    def __init__(self) -> None:
        """Initialize a Provider."""
        self._resources: dict[str, type[Resource]] = {}

    def resource(self, name: str) -> Callable[[type[ResourceT]], type[ResourceT]]:
        """Register a Resource class under this provider.

        Args:
            name: Resource type name (e.g., "database", "warehouse").

        Returns:
            Decorator function that registers the Resource class.

        Raises:
            TypeError: If the decorated object is not a Resource subclass, or
                declares ``computed`` as a field.
            MissingObserveError: If a non-computed class does not override ``on_observe``.
            MissingOutputsError: If a non-computed class declares no ``Outputs`` class.
            OperationDeadlineOutOfRangeError: If the class declares an
                ``operation_deadline_seconds`` other than ``None`` or a whole
                number of seconds from 1 to ``MAXIMUM_OPERATION_DEADLINE_SECONDS``,
                or declares it as an annotated field
                (:class:`OperationDeadlineFieldError`).
            ValueError: If ``name`` is already registered on this provider.

        Example:
            @postgres.resource("database")
            class Database(Resource[DatabaseConfig, DatabaseOutputs]):
                ...
        """  # noqa: DOC502

        def decorator(cls: type[ResourceT]) -> type[ResourceT]:
            if not isinstance(cls, type) or not issubclass(cls, Resource):
                raise TypeError(f"@resource() can only decorate Resource subclasses, got {cls!r}")

            if "computed" in cls.model_fields:
                raise TypeError(
                    f'resource "{name}" declares computed as a field; '
                    "write a bare `computed = True` without a type annotation."
                )

            if "operation_deadline_seconds" in cls.model_fields:
                raise OperationDeadlineFieldError(name, cls.__module__)

            if not is_operation_deadline_in_range(cls.operation_deadline_seconds):
                raise OperationDeadlineOutOfRangeError(name, cls.__module__)

            if not cls.computed and cls.on_observe is Resource.on_observe:
                raise MissingObserveError(name, cls.__module__)

            if not cls.computed and derive_outputs_class(cls) is None:
                raise MissingOutputsError(name, cls.__module__)

            cls.resource = name

            setattr(cls, RESOURCE_MARKER, True)

            if name in self._resources:
                existing = self._resources[name]
                raise ValueError(
                    f"Resource '{name}' already registered (existing: {existing.__name__}, new: {cls.__name__})"
                )

            self._resources[name] = cls
            return cls

        return decorator

    @property
    def resources(self) -> dict[str, type[Resource]]:
        """All resources registered with this provider.

        Returns:
            Dictionary mapping resource names to Resource classes.
        """
        return self._resources.copy()

    def __repr__(self) -> str:
        """Return string representation of this provider.

        Returns:
            String showing registered resources.
        """
        return f"Provider(resources={list(self._resources.keys())})"
