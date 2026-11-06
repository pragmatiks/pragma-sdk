<p align="center">
  <img src="assets/wordmark.png" alt="Pragmatiks" width="800">
</p>

# Pragma SDK

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/pragmatiks/pragma-sdk)
[![PyPI version](https://img.shields.io/pypi/v/pragmatiks-sdk.svg)](https://pypi.org/project/pragmatiks-sdk/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**[Documentation](https://docs.pragmatiks.io/sdk/overview)** | **[CLI](https://github.com/pragmatiks/pragma-cli)** | **[Providers](https://github.com/pragmatiks/pragma-providers)**

Build providers and interact with Pragmatiks programmatically.

## Quick Start

### Synchronous Client

```python
from pragma_sdk import PragmaClient, Resource

with PragmaClient() as client:
    # Apply a resource
    client.apply_resource(
        Resource(
            provider="gcp", resource="storage", name="my-bucket", config={"location": "US", "storage_class": "STANDARD"}
        )
    )

    # Get resource status
    bucket = client.get_resource("gcp", "storage", "my-bucket")
    print(bucket.outputs)
```

### Asynchronous Client

```python
import asyncio
from pragma_sdk import AsyncPragmaClient, Resource


async def main():
    async with AsyncPragmaClient() as client:
        # Apply a resource
        await client.apply_resource(
            Resource(
                provider="gcp",
                resource="storage",
                name="my-bucket",
                config={"location": "US", "storage_class": "STANDARD"},
            )
        )

        # Get resource status
        bucket = await client.get_resource("gcp", "storage", "my-bucket")
        print(bucket.outputs)


asyncio.run(main())
```

## Installation

```bash
pip install pragmatiks-sdk
```

Or with uv:

```bash
uv add pragmatiks-sdk
```

## Features

- **HTTP Clients** - Sync and async clients for the Pragmatiks API
- **Provider Authoring** - Build custom providers with type-safe Config and Outputs
- **Provider Publishing and Installation** - Publish provider versions, install, upgrade and downgrade them, and read an installation's history
- **Field References** - Reference outputs from other resources dynamically
- **Testing Harness** - Test provider lifecycle methods locally without deployment
- **Auto-discovery** - Automatic credential resolution from environment or config files

## Building Providers

Define resources with typed configuration and lifecycle methods:

```python
from pragma_sdk import AlreadyExistsError, Provider, Resource, Config, Outputs, Field

gcp = Provider()


class BucketConfig(Config):
    location: Field[str]
    storage_class: Field[str] = "STANDARD"


class BucketOutputs(Outputs):
    url: str
    created_at: str


@gcp.resource("storage")
class Bucket(Resource[BucketConfig, BucketOutputs]):
    async def on_create(self) -> BucketOutputs:
        """Provision the bucket; refuse a bucket that already exists."""
        bucket = await storage_client.create_bucket(self.name, location=self.config.location)

        if bucket is None:
            raise AlreadyExistsError(f"Bucket {self.name} already exists")

        return BucketOutputs(url=f"gs://{self.name}", created_at=bucket.created_at)

    async def on_observe(self) -> BucketOutputs | None:
        """Look the bucket up by identity; None when it does not exist."""
        bucket = await storage_client.find_bucket(self.name)

        if bucket is None:
            return None

        return BucketOutputs(url=f"gs://{self.name}", created_at=bucket.created_at)

    async def on_update(self, previous_config: BucketConfig | None) -> BucketOutputs:
        """Converge to self.config; previous_config is None when unknown."""
        bucket = await storage_client.update_bucket(self.name, storage_class=self.config.storage_class)

        return BucketOutputs(url=f"gs://{self.name}", created_at=bucket.created_at)

    async def on_delete(self) -> None:
        """Delete the bucket; succeeds when it is already gone."""
        await storage_client.delete_bucket(self.name, missing_ok=True)
```

`storage_client` stands for your vendor's client.

### Observing before acting

Before it creates, updates or deletes a resource of a type that is not `computed`, the platform calls `on_observe` to see whether the object exists, and calls it again once the lifecycle method returns:

- A type that is not `computed` must define `on_observe` and declare an `Outputs` class (`Resource[YourConfig, YourOutputs]`): `@provider.resource()` refuses it otherwise, with `MissingObserveError` or `MissingOutputsError`, since without outputs the platform could never see its object as present. Publishing such a provider fails admission with `missing_observe` or `missing_outputs`, naming the resource type.

- `on_observe` returns the object's outputs, or `None` only when the object does not exist. Platform callbacks such as `Resource.apply` and `Resource.read` are not available in it, so it finds its object by identity, never through child resources.
- `on_create` and `on_update` return only once `on_observe` finds the object, with outputs equal to what `on_observe` returns. Both sides are validated against the type's `Outputs` class before they are compared, so properties it does not declare never count. `on_delete` returns only once `on_observe` returns `None`.
- When the vendor refuses a create because the object already exists, raise `AlreadyExistsError` from `on_create` instead of adopting the object. The resource fails, and Pragmatiks never takes over an object it did not create.
- Outputs that fail validation, or that differ from what `on_observe` returns, fail the resource while the object stays in place, until a fixed provider version runs.

`ProviderHarness` results carry `normalized_outputs`, the outputs as the platform compares them, so a test can check that a create and an observe agree.

### Child resources

A resource can be composed of child resources it owns. `on_create` and `on_update` apply each child with `await child.apply()`, which returns once the platform accepted it and never waits, and read it with `await child.read()`, which returns the child as the platform records it (`lifecycle_state`, `config`, `outputs`), or `None` when it does not exist yet. While a child is not `READY`, the method returns `ChildrenPending()` instead of outputs:

```python
from pragma_sdk import ChildrenPending, LifecycleState


@gcp.resource("app")
class App(Resource[AppConfig, AppOutputs]):
    computed = True

    async def on_create(self) -> AppOutputs | ChildrenPending:
        return await self.converge()

    async def on_update(self, previous_config: AppConfig | None) -> AppOutputs | ChildrenPending:
        return await self.converge()

    async def converge(self) -> AppOutputs | ChildrenPending:
        bucket = Bucket(project_id=self.project_id, name=f"{self.name}-data", config=BucketConfig(location="EU"))
        await bucket.apply()
        applied = await bucket.read()

        if applied is None or applied.lifecycle_state != LifecycleState.READY or applied.outputs is None:
            return ChildrenPending()

        return AppOutputs(bucket_url=applied.outputs.url)
```

The platform then waits until every child this operation applied is `READY` and runs the resource a second time: a computed resource through `on_update`, any other through `on_create` when `on_observe` finds its object absent, else through `on_update`. That pass applies the same children, which changes nothing, reads them `READY` and returns the outputs. Its `previous_config` is the configuration completed before the first pass, `None` after a create. A pass whose children are already `READY` returns its outputs at once.

- A child that fails its create, or is deleted while its owner waits, fails the owner.
- Returning `ChildrenPending()` when every child the operation applied was already `READY` and unchanged, or when it applied none, fails the resource.
- Return `ChildrenPending()` only after `read()` found a child that is not `READY`.
- `apply()` and `read()` act in the running resource's project, whatever `project_id` the child carries. `read()` returns the child's `lifecycle_state`, `outputs` and the `config` its owner applied, with references unresolved; it carries no dependencies or tags.
- `read()` serves only resources the running resource owns. The platform refuses a read or an apply of a resource another resource owns, or one that exists and no resource owns, with `CallbackRefusedError`; let it propagate, and the resource fails with that reason.
- The platform tears owned children down before their owner, so `on_delete` deletes none of them.
- `ProviderHarness` serves no platform callback yet, so `apply()` and `read()` raise in a method it runs; it records a returned `ChildrenPending()` as `children_pending`.

### Operation deadline

Every operation of a resource type may run for 1200 seconds by default. A type declares another ceiling with a bare class attribute, a whole number of seconds from 1 to `MAXIMUM_OPERATION_DEADLINE_SECONDS` (3600):

```python
@gcp.resource("instance")
class Instance(Resource[InstanceConfig, InstanceOutputs]):
    operation_deadline_seconds = 1800
```

`@provider.resource()` refuses any other value with `OperationDeadlineOutOfRangeError`, and publishing such a provider fails admission with `operation_deadline_out_of_range`. Past the default an operation runs on only while the worker's event loop stays responsive: the worker sends heartbeats on its own, and a handler blocking the loop stops them and fails soon after. At the deadline the handler's `await` raises `asyncio.CancelledError`; let it propagate and clean up in `finally`. Work under `asyncio.to_thread` or `loop.run_in_executor(None, ...)` is not stopped by the cancel: the worker answers once it ends, and is stopped about 30 seconds after the deadline if it has not. Threads and processes a handler starts itself are not tracked; stop them in `finally`. Asyncio tasks a handler spawns or shields are not cancelled with it and not waited for; end them yourself, in a `TaskGroup` or in `finally`.

### Declaring the provider

A provider wheel declares itself with one entry point in the `pragma.provider` group, named after the provider and pointing at its importable package:

```toml
[project.entry-points."pragma.provider"]
gcp = "gcp_provider"
```

The Pragmatiks platform finds the provider's resource types through this entry point. Only resource classes defined inside that package belong to the provider; resource classes it imports from another provider stay that provider's.

A resource always names the provider whose package defines its class. When a provider builds or applies a resource of a type it imports from another provider, that resource and its identity name the other provider, and that provider manages it. Declare that provider as a dependency, so it is embedded, and install it in the organization too: the organization's installation of it is what manages those resources.

To check what the platform will read from an installed wheel, run introspection in its environment:

```bash
PRAGMA_PROVIDER_DISTRIBUTION=<distribution> python -m pragma_sdk.introspection --output report.json
```

`<distribution>` is the `name` in the wheel's `[project]` table. When you publish, the wheel is renamed to `<organization>-<provider name you publish under>` before it is uploaded, and the platform loads it under that name.

The report lists the resource types with their schemas, or why the provider failed to load.

### Reading other versions' data

`Outputs` ignore properties the class does not declare, so outputs a newer version of the provider recorded still read. `Config` stays strict for the resource being processed. A dependency's config is read with the unknown top-level fields of a strict `Config` ignored, so a dependency managed by a newer version of its provider still resolves.

## Field References

Reference outputs from other resources:

```python
from pragma_sdk import FieldReference

config = AppConfig(
    database_url=FieldReference(provider="postgres", resource="database", name="my-db", field="outputs.connection_url")
)
```

## Testing Providers

Test lifecycle methods locally with `ProviderHarness`:

```python
from pragma_sdk.provider import ProviderHarness


async def test_bucket_creation():
    harness = ProviderHarness()

    result = await harness.invoke_create(Bucket, name="test-bucket", config=BucketConfig(location="US"))

    assert result.success
    assert "gs://test-bucket" in result.outputs.url

    observed = await harness.invoke_observe(Bucket, name="test-bucket", config=BucketConfig(location="US"))

    assert observed.normalized_outputs == result.normalized_outputs
```

Inside the harness, a resource of a class from the tested class's top-level package carries the harness's `provider_name` (`test/local` by default). When the tested class builds resources of an embedded provider's types, name that provider's package the way the platform does, or reading their `provider` or `id` raises:

```python
harness = ProviderHarness(package_catalog_names={"gcp_provider": "pragmatiks/gcp"})
```

The harness serves no platform callback, so a resource composed of [child resources](#child-resources) cannot run `apply()` or `read()` in it.

## Authentication

Credentials are discovered automatically in this order:

1. Explicit `auth_token` parameter
2. Context-specific environment variable: `PRAGMA_AUTH_TOKEN_{CONTEXT}` (e.g., `PRAGMA_AUTH_TOKEN_PRODUCTION`)
3. Generic environment variable: `PRAGMA_AUTH_TOKEN`
4. Credentials file: `~/.config/pragma/credentials`

The context is determined by: explicit `context` parameter > `PRAGMA_CONTEXT` env var > CLI config > `"default"`.

```python
# Auto-discover credentials (uses default context)
client = PragmaClient()

# Explicit token
client = PragmaClient(auth_token="sk_...")

# Use a specific context (checks PRAGMA_AUTH_TOKEN_PRODUCTION first)
client = PragmaClient(context="production")

# Require authentication (fail if no token)
client = PragmaClient(require_auth=True)
```

## API Reference

### HTTP Client Methods

Both `PragmaClient` (sync) and `AsyncPragmaClient` (async) provide the same methods, except where noted below.

#### Resources

Resource operations live on a project handle: `client.project("my-project").list_resources()`.

| Method | Description |
|--------|-------------|
| `list_resources(provider, resource, tags)` | List resources with optional filters |
| `get_resource(provider, resource, name)` | Get a specific resource |
| `apply_resource(resource)` | Create or update a resource |
| `deactivate_resource(provider, resource, name, dry_run=False)` | Deactivate a resource; returns the teardown's impact list |
| `delete_resource(provider, resource, name, dry_run=False)` | Delete a resource; returns the teardown's impact list |
| `wait_ready(provider, resource, name, timeout=300)` | Poll until READY; `timeout=0` or `None` waits forever |
| `wait_deactivated(provider, resource, name, timeout=300)` | Poll until DRAFT; `timeout=0` or `None` waits forever |
| `wait_deleted(provider, resource, name, timeout=300)` | Poll until DELETED; an unknown resource raises instead of returning; `timeout=0` or `None` waits forever |

#### Lifecycle Events

On the client itself, `AsyncPragmaClient` only.

| Method | Description |
|--------|-------------|
| `stream_lifecycle_events(timeout=None)` | Yield lifecycle event frames from the API's SSE stream; `timeout=0` or `None` streams forever |

#### Providers

| Method | Description |
|--------|-------------|
| `list_providers(query, scope, tags, limit, offset)` | Browse and search the provider catalog |
| `get_provider(provider_name)` | Get a provider from the catalog |
| `list_provider_versions(provider_name)` | List a provider's versions |
| `get_provider_version(provider_name, version)` | Get one version of a provider, in any status |
| `publish_provider_version(wheel_path, changelog=None)` | Rename, declare and upload a provider wheel |
| `delete_provider(provider_name)` | Delete a provider from the catalog |

#### Installed Providers

| Method | Description |
|--------|-------------|
| `install_provider(provider_name, version=None, config=None)` | Install a provider; installing a failed installation again reuses its stored version and config |
| `list_installations()` | List installed providers with their state, latest operation and its target version, and failure message |
| `upgrade_provider(provider_name, target_version=None)` | Upgrade an installed provider (latest version if none) |
| `downgrade_provider(provider_name, target_version)` | Downgrade an installed provider |
| `uninstall_provider(provider_name, cascade=False)` | Uninstall a provider |
| `list_provider_history(provider_name)` | List an installation's history, newest first; kept 30 days, also after an uninstall |

#### User & Health

| Method | Description |
|--------|-------------|
| `is_healthy()` | Check API health |
| `get_me()` | Get current authenticated user information |

### Provider Classes

| Class | Description |
|-------|-------------|
| `Provider()` | Resource grouping with `@provider.resource()` decorator |
| `Resource[ConfigT, OutputsT]` | Base class with `on_create`, `on_observe`, `on_update`, `on_delete` |
| `computed = True` | Class attribute for a resource with no external object; exempts it from `on_observe` and from declaring an `Outputs` class |
| `operation_deadline_seconds = <seconds>` | Class attribute setting the longest time one operation of the type may run, from 1 to 3600 seconds; 1200 when left out |
| `Config` | Base class for resource configuration (Pydantic model) |
| `Outputs` | Base class for resource outputs (Pydantic model) |
| `AlreadyExistsError` | Raised from `on_create` when the object to create already exists |
| `Field[T]` | Type alias for `T | FieldReference` |
| `ProviderHarness` | Local testing harness |

## Development

```bash
# Install the toolchain, dependencies and git hooks
mise run install

# Format code
mise run format

# Type check and lint
mise run check
```

## License

MIT
