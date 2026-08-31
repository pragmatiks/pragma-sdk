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
            provider="gcp",
            resource="storage",
            name="my-bucket",
            config={"location": "US", "storage_class": "STANDARD"}
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
                config={"location": "US", "storage_class": "STANDARD"}
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
- **Provider Deployment** - Push, build, deploy, and rollback providers programmatically
- **Dead-Letter Queue** - Inspect and retry failed events for debugging and recovery
- **Field References** - Reference outputs from other resources dynamically
- **Testing Harness** - Test provider lifecycle methods locally without deployment
- **Auto-discovery** - Automatic credential resolution from environment or config files

## Building Providers

Define resources with typed configuration and lifecycle methods:

```python
from pragma_sdk import Provider, Resource, Config, Outputs, Field

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
        # Provision the bucket
        return BucketOutputs(url=f"gs://{self.name}", created_at="...")

    async def on_update(self, previous_config: BucketConfig) -> BucketOutputs:
        # Handle config changes
        return self.outputs

    async def on_delete(self) -> None:
        # Clean up
        pass
```

## Field References

Reference outputs from other resources:

```python
from pragma_sdk import FieldReference

config = AppConfig(
    database_url=FieldReference(
        provider="postgres",
        resource="database",
        name="my-db",
        field="outputs.connection_url"
    )
)
```

## Testing Providers

Test lifecycle methods locally with `ProviderHarness`:

```python
from pragma_sdk.provider import ProviderHarness

async def test_bucket_creation():
    harness = ProviderHarness()

    result = await harness.invoke_create(
        Bucket,
        name="test-bucket",
        config=BucketConfig(location="US")
    )

    assert result.success
    assert "gs://test-bucket" in result.outputs.url
```

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
| `list_providers()` | List all providers for the current tenant |
| `push_provider(provider_id, tarball)` | Push provider code and trigger a build |
| `deploy_provider(provider_id, version)` | Deploy a provider (latest build if no version) |
| `rollback_provider(provider_id, version)` | Rollback to a previous build version |
| `delete_provider(provider_id, cascade)` | Delete a provider and associated resources |
| `get_deployment_status(provider_id)` | Get deployment status for a provider |
| `list_builds(provider_id)` | List builds for a provider |
| `get_build_status(provider_id, version)` | Get status of a specific build |
| `stream_build_logs(provider_id, version)` | Stream logs from a build |

#### Dead-Letter Queue

| Method | Description |
|--------|-------------|
| `list_dead_letter_events(provider)` | List dead letter events with optional provider filter |
| `get_dead_letter_event(event_id)` | Get a dead letter event by ID |
| `retry_dead_letter_event(event_id)` | Retry a single dead letter event |
| `retry_all_dead_letter_events()` | Retry all dead letter events |
| `delete_dead_letter_event(event_id)` | Delete a single dead letter event |
| `delete_dead_letter_events(provider, all)` | Delete multiple dead letter events |

#### User & Health

| Method | Description |
|--------|-------------|
| `is_healthy()` | Check API health |
| `get_me()` | Get current authenticated user information |

### Provider Classes

| Class | Description |
|-------|-------------|
| `Provider()` | Resource grouping with `@provider.resource()` decorator |
| `Resource[ConfigT, OutputsT]` | Base class with `on_create`, `on_update`, `on_delete` |
| `Config` | Base class for resource configuration (Pydantic model) |
| `Outputs` | Base class for resource outputs (Pydantic model) |
| `Field[T]` | Type alias for `T | FieldReference` |
| `ProviderHarness` | Local testing harness |

## Development

```bash
# Run tests
task sdk:test

# Format code
task sdk:format

# Type check and lint
task sdk:check
```

## License

MIT
