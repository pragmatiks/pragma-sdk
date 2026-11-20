# Architecture

Module map, wheel contract, features, dependencies and code patterns of `pragmatiks-sdk`. The host protocol is in `protocol.md`.

## Module map

```
pragma-sdk/
└── src/pragma_sdk/
    ├── client.py          # PragmaClient (sync) and AsyncPragmaClient
    ├── client_log_follow.py  # Followed log stream events and the server-sent event parser behind `follow_logs`
    ├── models/            # Pydantic models (shared with API)
    ├── provider/          # Provider authoring (Provider, Resource, Config, Outputs)
    ├── protocol/          # Host <-> worker protocol: frames, codec, handshake, introspection report, config reference markers
    ├── declaration/       # Provider declared by a distribution's `pragma.provider` entry point
    ├── diagnostics/       # Log output and secret-free error descriptions for worker and introspection
    ├── worker/            # `python -m pragma_sdk.worker --socket-descriptor <n>`: runs lifecycle methods
    │   └── executor.py    # Default executor recording each request's work, and the request scope
    ├── introspection/     # `python -m pragma_sdk.introspection --output <path>`: writes the report
    ├── provider_entry_point.py  # The `pragma.provider` entry point: group, package parsing, `entry_points.txt` parsing, `ProviderEntryPoint` lookup
    ├── wheel.py           # Bounded wheel archive open and member reads, and the publish declaration read
    ├── wheel_central_directory.py  # Central directory bound from the zip end records, zip64 layouts refused
    ├── wheel_check.py     # Whole-wheel refusal read (expansion pass, then `.dist-info`) against a declaration
    ├── wheel_names.py     # Distribution, filename and `.dist-info` naming contract
    ├── wheel_rename.py    # Renaming a wheel to its organization's distribution name
    ├── wheel_upload.py    # Direct wheel upload to the signed storage address
    ├── wheel_publish.py   # Renamed wheel directory and declared-version note of the publish composite
    └── blocking.py        # Cancel-shielded worker-thread runner for the async client
```

## Wheel contract

`wheel.py`, `wheel_central_directory.py`, `wheel_check.py`, `wheel_names.py`, `provider_entry_point.py` and the `WheelExpansionLimitError`, `WheelCentralDirectoryRefusedError` and `build_wheel_too_large_message` parts of `exceptions.py` are the wheel contract the hub's admission imports: changing them changes what the platform admits. `open_wheel_archive` refuses, through `wheel_central_directory.require_bounded_central_directory`, a central directory past `MAXIMUM_CENTRAL_DIRECTORY_BYTES` (1 MiB), or a zip64 layout `zipfile` releases could read differently, with `WheelCentralDirectoryRefusedError` (a `BadZipFile`) before any member is listed.

## Features

**HTTP Clients**: Both sync (`PragmaClient`) and async (`AsyncPragmaClient`) with identical APIs. A project's `resources` handle reads a resource's recent logs with `logs(provider, resource, name, since=, tail=)` and streams them with `follow_logs(...)`, which yields `LogFollowEntry` events, then one `LogFollowEnd` or `LogFollowError`, from the API's server-sent events (`client_log_follow.parse_log_follow_stream`, keep-alive `:` lines ignored), all exported from `pragma_sdk`; `since` is inclusive, and a naive `since` raises `ValueError`. A refused stream raises `httpx.HTTPStatusError` with its body read, and a connection that breaks, goes silent past the keep-alive interval or closes without a terminal event raises `LogFollowInterruptedError`. Callers that stop early close the generator (`contextlib.closing` or `aclosing`).

**Provider Authoring**: `Provider`, `Resource[Config, Outputs]`, `Field[T]`, `FieldReference` for building providers.

**Testing Harness**: `ProviderHarness` for local lifecycle testing without deployment.

**Worker and Introspection**: The Pragmatiks host runs a provider in its own venv through `python -m pragma_sdk.worker`, on a socket descriptor it inherits, and reads its schemas through `python -m pragma_sdk.introspection`; both find the provider from `PRAGMA_PROVIDER_DISTRIBUTION` and the distribution's `pragma.provider` entry point.

**Auto-discovery**: Credentials resolved from env vars, context-specific tokens, or `~/.config/pragma/credentials`.

## Dependencies

- `httpx` - Async HTTP client
- `packaging` - Wheel filename, version and distribution-name rules when reading and renaming provider wheels
- `pydantic` - Data validation and serialization
- `pydantic-settings` - Worker and introspection settings from `PRAGMA_PROVIDER_*` environment variables
- `pyyaml` - YAML parsing for resource definitions
- `structlog` - Structured logging

Every downstream Pragmatiks package resolves this SDK's dependencies, so a new top-level dependency here has a high bar: keep the surface lean.

## Patterns

- All API methods are async in `AsyncPragmaClient`, sync wrappers in `PragmaClient`
- Pydantic models for request/response types
- httpx for HTTP calls
- Type hints on all public interfaces
