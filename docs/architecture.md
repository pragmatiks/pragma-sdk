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
    ├── storage_upload.py  # Streamed `PUT` of declared bytes to a signed storage address, and storage's answer
    ├── file_upload.py     # A project file's source, MD5, streamed upload and wait until stored
    └── blocking.py        # Cancel-shielded worker-thread runner for the async client
```

## Wheel contract

`wheel.py`, `wheel_central_directory.py`, `wheel_check.py`, `wheel_names.py`, `provider_entry_point.py` and the `WheelExpansionLimitError`, `WheelCentralDirectoryRefusedError` and `build_wheel_too_large_message` parts of `exceptions.py` are the wheel contract the hub's admission imports: changing them changes what the platform admits. `open_wheel_archive` refuses, through `wheel_central_directory.require_bounded_central_directory`, a central directory past `MAXIMUM_CENTRAL_DIRECTORY_BYTES` (1 MiB), or a zip64 layout `zipfile` releases could read differently, with `WheelCentralDirectoryRefusedError` (a `BadZipFile`) before any member is listed.

## Features

**HTTP Clients**: Both sync (`PragmaClient`) and async (`AsyncPragmaClient`) with identical APIs. A project's `resources` handle reads a resource's recent logs with `logs(provider, resource, name, since=, tail=)` and streams them with `follow_logs(...)`, which yields `LogFollowEntry` events, then one `LogFollowEnd` or `LogFollowError`, from the API's server-sent events (`client_log_follow.parse_log_follow_stream`, keep-alive `:` lines ignored), all exported from `pragma_sdk`; `since` is inclusive, and a naive `since` raises `ValueError`. A refused stream raises `httpx.HTTPStatusError` with its body read, and a connection that breaks, goes silent past the keep-alive interval or closes without a terminal event raises `LogFollowInterruptedError`. Callers that stop early close the generator (`contextlib.closing` or `aclosing`).

**File uploads**: A project's `resources` handle uploads a file with `upload_file(name, source, content_type)`, where `source` is `bytes`, a `Path` or a regular file opened for binary reading. It reads the source once in chunks for its MD5 (off the event loop in the async client), declares the upload (`declare_file_upload`), streams exactly that many bytes in one `PUT` straight to storage through the upload transport, and polls `get_file_upload` until the file is stored, returning its `FileMetadata`. Memory holds one chunk whatever the size. A `409 file_upload_in_progress` (another upload being stored) or `409 file_delete_in_progress` (the file of a deleted `pragma/file` resource being deleted) is waited out for its `Retry-After`, never less than the one-second poll, for up to three minutes, then raises `FileUploadNotStoredError` (`busy`). An upload that ends unstored raises `FileUploadNotStoredError` with its reason (`changed` for content that changes between the two reads, `expired` for an address that expires before the whole file reaches storage, `superseded`, `failed`, `deleted`) and its `operation_id`, and one that shows no progress for three minutes raises `FileUploadPendingError`, after which uploading the same content again keeps waiting without sending the bytes again. The declare and wait loops live in `file_upload.py`, so the handle's file surface is `declare_file_upload`, `get_file_upload` and `upload_file`.

**Provider Authoring**: `Provider`, `Resource[Config, Outputs]`, `Field[T]`, `FieldReference` for building providers.

**Secrets**: A provider declares a secret as `SecretStr` or `SecretBytes`: `Field[SecretStr]` in config, `SecretStr` in outputs. Its published schema node carries `"format": "password"`, which is what the platform masks on every client surface and what makes a literal in that field a refusal, so config takes a reference to a secret resource. The SDK has no other secret marker. The worker path reveals every secret it sends (`protocol.codec.build_json_payload`), and a provider reads one with `.get_secret_value()`: `str()` and a JSON-mode `model_dump` give asterisks. A provider that writes a model into a plain-text carrier, such as an environment variable, encodes it with `pragma_sdk.provider.build_revealed_json`, which delegates to the codec, so the carrier holds the same revealed form the worker sends.

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
