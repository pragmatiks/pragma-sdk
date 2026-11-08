# CLAUDE.md

## Project

**pragma-sdk**: Python SDK for the Pragmatiks platform. Provides typed clients for API consumers and provider authors.

## Shared rules

Delivery lives in pragma-kit `project-instructions.md`. Evidence-based development, the solution preference order, the new-dependency proposal, the 13 engineering principles and the reviewer and developer protocols live in the pragma-kit plugin's `pragma-kit:engineering-principles` skill: load it before planning, writing or reviewing a change here. This file holds only what is specific to this repository.

## Architecture

```
pragma-sdk/
└── src/pragma_sdk/
    ├── client.py          # PragmaClient (sync) and AsyncPragmaClient
    ├── models/            # Pydantic models (shared with API)
    ├── resources/         # Resource-specific client methods
    ├── provider/          # Provider authoring (Provider, Resource, Config, Outputs)
    ├── protocol/          # Host <-> worker protocol: frames, codec, handshake, introspection report
    ├── declaration/       # Provider declared by a distribution's `pragma.provider` entry point
    ├── diagnostics/       # Log output and secret-free error descriptions for worker and introspection
    ├── worker/            # `python -m pragma_sdk.worker --socket <path>`: runs lifecycle methods
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

`wheel.py`, `wheel_central_directory.py`, `wheel_check.py`, `wheel_names.py`, `provider_entry_point.py` and the `WheelExpansionLimitError`, `WheelCentralDirectoryRefusedError` and `build_wheel_too_large_message` parts of `exceptions.py` are the wheel contract the hub's admission imports: changing them changes what the platform admits. `open_wheel_archive` refuses, through `wheel_central_directory.require_bounded_central_directory`, a central directory past `MAXIMUM_CENTRAL_DIRECTORY_BYTES` (1 MiB), or a zip64 layout `zipfile` releases could read differently, with `WheelCentralDirectoryRefusedError` (a `BadZipFile`) before any member is listed.

`protocol/` imports nothing from the SDK but `types`; the Pragmatiks host speaks only it. Changing a frame shape, the introspection report, or the worker's method set is a new `PROTOCOL_VERSION`.

Protocol 7 is the only version the worker offers. A request for a method the worker does not serve is answered with `METHOD_NOT_FOUND_CODE` (logged `request_method_unknown`); `discover_resources` refuses two distinct classes of one package registering the same resource name (`DuplicateResourceTypeError`, reported by introspection as `import_failed`). The handshake result carries `distribution_catalog_names`, the catalog name (`prefix/name`, validated) of the served distribution and of every provider distribution it embeds, keyed by distribution name. Within the handshake the worker normalizes the keys, refuses a result without the served distribution (`distribution_not_cataloged`), maps each distribution's `pragma.provider` package to its catalog name (`declaration.provider.load_package_catalog_names`) and refuses a distribution it cannot load (`distribution_not_loadable`), all before it serves; the session then binds the map (`context.package_catalog_names_scope`). `Resource.provider` is the catalog name of the longest bound package holding the class's module (`context.compute_defining_provider`), so identities and applied children name the provider defining the type. `DispatchParams`, `ObserveParams` and `ResourceQueryParams` carry no `provider`; request log fields name the defining provider of the target's class. `ProviderHarness` binds the tested class's top-level package to its `provider_name`, merged over its `package_catalog_names` argument.

Protocol 3 adds the `observe` request (`OBSERVE_METHOD`, `ObserveParams`, `ObserveResult`), which the host sends before it acts on a non-computed type, so `Provider.resource` refuses a non-computed type without `on_observe` (`MissingObserveError`) or without an `Outputs` class (`MissingOutputsError`, since only outputs report the object present); the introspection report gives them the `missing_observe` and `missing_outputs` categories, each naming the `resource`, and `AdmissionFailureCategory` carries both; one in a module the declared package imports from outside itself is a `ProviderDeclarationError` naming the module and the missing part, reported as `import_failed`. For an `observe` the worker runs `on_observe` with no runtime context bound, so platform callbacks raise, and answers `outputs` or `None` when the object is absent. The outputs of `observe`, and of a create or update `dispatch`, are normalized the same way (`provider.outputs.normalize_outputs`: read as plain data, each property by name or alias, so an Outputs subclass instance and a dict give the same result, validated through the type's `Outputs` class, undeclared top-level keys dropped and logged as `outputs_keys_ignored`; a class declaring no `Outputs` class that returns outputs raises `ValueError`); the codec then encodes them with secrets revealed, so unencodable outputs still answer `RESULT_NOT_SERIALIZABLE_MESSAGE`, and the host compares the encoded dicts. `ProviderHarness` results carry the same encoded form as `normalized_outputs`. An `AlreadyExistsError` raised from `on_create` in a create `dispatch`, or from `on_observe` in an `observe`, alone or inside an exception group, is answered with `ALREADY_EXISTS_CODE`; raised anywhere else it is a plain `INTERNAL_ERROR_CODE` failure. `on_observe` makes no write and reports only an object this resource created: it raises `AlreadyExistsError` for an object at the resource's identity that its create's conflict path would refuse, such as one without this resource's marker; the worker answers that observe with `ALREADY_EXISTS_CODE`, and the host never writes or deletes such an object. The code adds no frame shape or method, so it needs no new `PROTOCOL_VERSION`. `ProviderResponse.failure_code` (`ProviderFailureCode`: `already_exists`, `conformance`) marks a failure whose reason the platform words itself. `ProviderResponse.provider_version` is the version of the provider that ran the operation; the Pragmatiks host fills it, and no frame carries it.

Protocol 4 adds the operation deadline. `ResourceTypeCapabilities.operation_deadline_seconds` carries the class's `Resource.operation_deadline_seconds` (`None`, or 1 to `MAXIMUM_OPERATION_DEADLINE_SECONDS` = 3600) to the handshake and the introspection report; `Provider.resource` refuses any other value, and an annotated field of that name, with `OperationDeadlineOutOfRangeError`, which introspection reports as `operation_deadline_out_of_range` naming the `resource` (`AdmissionFailureCategory` carries it). `HandshakeResult.heartbeat_interval_seconds` sets how often the worker sends the bare `heartbeat` notification (`HEARTBEAT_METHOD`), from the accepted handshake until the session ends; a heartbeat that cannot be written ends the session. The host's `cancel` notification (`CANCEL_METHOD`, `CancelParams(correlation_id)`) cancels that request's task, logged `request_cancel_received`; a task that has not started skips its handler. Wherever the cancel lands, and however the handler ends (`CancelledError`, a return or another error, the latter two logged `request_cancel_swallowed` and their response dropped), the worker consumes the cancellation, waits once for the work the request had recorded in the loop's default executor when its handler ended (work submitted later is not waited for), then answers `CANCELLED_CODE` (-32003, `The request was cancelled at its deadline.`, logged `request_cancelled` with `executor_work_items`, work cancelled before it ran not counted). `serve_host` installs `worker.executor.RequestScopedExecutor` as the default executor; it runs each piece of work in a copy of the submitting context, as `asyncio.to_thread` does, so `run_in_executor(None, ...)` work sees the request's context variables, and records the work each request submits under `request_scope(correlation_id)`, which `run_request` enters, so `asyncio.to_thread`, `run_in_executor(None, ...)` and the loop's `getaddrinfo` are waited for, and threads, processes and executors a handler starts itself, and asyncio tasks it spawns or shields, are not. A cancel for a request with no task, a done task or one already cancelled is ignored (`cancel_ignored` with its `reason`); a cancel at session end still writes no response. `NotificationFrame` carries `params` and `JsonRpcCodec.build_notification` takes them; a bare notification is written without a `params` member.

Protocol 5 composes resources through owned children. `ChildResourcePayload` names no project and no owner, and `DispatchParams` no owner: the host applies every child (`APPLY_RESOURCE_METHOD`) in the dispatched resource's project and names the dispatched resource as its owner, fenced on the dispatched resource's running operation. `Resource.apply()` returns `None` and never waits. The `read_resource` callback (`READ_RESOURCE_METHOD`, `ReadResourceParams(dispatch_id, provider, resource, name)`, `ReadResourceResult(resource: ChildResourceStatePayload | None)`, `ChildResourceStatePayload(lifecycle_state, config, outputs)`, the config as stored, references unresolved) serves only resources the dispatched resource owns, under the same fence, and `None` when no live resource has that identity; `Resource.read()` calls it through `context.read_resource` and builds a new instance of its own class from the stored config, unflattened since it holds no resolved values. `on_create` and `on_update` may return `ChildrenPending` (`models.children`, a frozen model with no fields) instead of outputs: `RequestHandlers.run_dispatch` answers every dispatch with `DispatchResult(outputs, children_pending)`, whose validator refuses both together, and logs `dispatch_children_pending` for it; `on_observe` and `on_delete` returning it fail with `ValueError` (`provider.outputs.refuse_children_pending`, an `INTERNAL_ERROR_CODE` failure naming the method). `ProviderResponse.children_pending` carries it to the platform, valid only on a `SUCCESS` with no outputs; the host skips the observe comparison for it. A callback the platform refused is answered with `CALLBACK_REFUSED_CODE` (-32004), which `RuntimeContextProxy.call` logs as `callback_refused` and raises as `CallbackRefusedError` (a `RuntimeError`) whose text is the host's message alone, so the operation's failure reason is the platform's text; every other failure code stays a plain `RuntimeError`. Protocol 5 carries no callback that waits for a resource's state and no owner surface: a handler never waits for a child in process, and never names an owner. `ProviderHarness` records `LifecycleResult.children_pending` and brokers `apply()` and `read()` in create, update and delete (`provider.harness_children`): a new or changed child starts PENDING, an unchanged one keeps its state, a foreign or unowned live resource is refused with the hub's reason, and a `ChildrenPending` pass that applied no child that was not READY and unchanged fails with the hub's nothing-pending reason; the test settles a child with `set_child_state` and runs the owner's next pass with `resume()` (`on_update` with the last config that returned outputs; a non-computed type is observed first and gets `on_create` when absent). The refusal texts are copied from the hub's `models/resource_failure.py`. `Resource.resolved(value)` (`models.references.require_resolved`) types a `Field[T]` config value as `T`, raising `UnresolvedReferenceError` for a top-level `FieldReference`.

Protocol 6 carries the queried resource's applied config and its signed file URLs on a logs or health query: `ResourceQueryParams.resolved_files` (signed download URLs keyed by Pragmatiks file reference, the shape `ObserveParams` carries) beside a `target` whose config is the resolved config the host last applied. `RequestHandlers.build_queried_resource` builds the resource from both, so a config holding a required field or a `pragma://files` reference builds as it does for a dispatch.

Protocol 7 renames the provider-reported live check to vitals: the `resource_vitals` request (`RESOURCE_VITALS_METHOD`) runs `Resource.vitals()`, which answers `Vitals(status: VitalsStatus, message, details)` with `VitalsStatus` one of `nominal`, `impaired`, `down` (default `nominal`), and `ResourceTypeCapabilities.vitals` reports whether a type overrides it.

`Outputs` ignore undeclared properties; `Config` forbids them. A dependency reference or a child resource read whose Config forbids extras is read with the top-level config keys it does not declare dropped (`models.base.drop_undeclared_config_keys`, pure) and logged by each caller (`dependency_config_keys_ignored`, `child_config_keys_ignored`).

## Features

**HTTP Clients**: Both sync (`PragmaClient`) and async (`AsyncPragmaClient`) with identical APIs.

**Provider Authoring**: `Provider`, `Resource[Config, Outputs]`, `Field[T]`, `FieldReference` for building providers.

**Testing Harness**: `ProviderHarness` for local lifecycle testing without deployment.

**Worker and Introspection**: The Pragmatiks host runs a provider in its own venv through `python -m pragma_sdk.worker` and reads its schemas through `python -m pragma_sdk.introspection`; both find the provider from `PRAGMA_PROVIDER_DISTRIBUTION` and the distribution's `pragma.provider` entry point.

**Auto-discovery**: Credentials resolved from env vars, context-specific tokens, or `~/.config/pragma/credentials`.

## Dependencies

- `httpx` - Async HTTP client
- `packaging` - Wheel filename, version and distribution-name rules when reading and renaming provider wheels
- `pydantic` - Data validation and serialization
- `pydantic-settings` - Worker and introspection settings from `PRAGMA_PROVIDER_*` environment variables
- `pyyaml` - YAML parsing for resource definitions

Every downstream Pragmatiks package resolves this SDK's dependencies, so a new top-level dependency here has a high bar: keep the surface lean.

## Development

mise is the only toolchain source: `mise.toml` pins python, uv, pre-commit, commitizen and yq, and `mise.lock` plus `.mise/locks/` lock them for macOS arm64 and Linux x64.

`[tool.uv] required-version` in `pyproject.toml` equals the uv pin in `mise.toml`; uv refuses to run at any other version, and the `Toolchain` workflow fails a pull request where they differ.

On a fresh clone run `mise trust`, then `mise run install`.

Use mise tasks (`mise tasks` lists them all):

| Command | Purpose |
|---------|---------|
| `mise run install` | Install the locked toolchain, dependencies and git hooks |
| `mise run lock:toolchain` | Re-lock the toolchain after editing `mise.toml` (needs mise at the `min_version` in `mise.toml`) |
| `mise run check:mise-pins` | Fail when a `jdx/mise-action` step's `version:` or `sha256:` differs from `min_version` and its runner's mise binary checksum |
| `mise run check:toolchain-scripts` | Fail when a shared toolchain script under `scripts/tasks/` differs from the public repositories' copies (same-named branch, else `main`) |
| `mise run format` | Format with ruff |
| `mise run check` | Lint + type check |

## Patterns

- All API methods are async in `AsyncPragmaClient`, sync wrappers in `PragmaClient`
- Pydantic models for request/response types
- httpx for HTTP calls
- Type hints on all public interfaces

## Testing

**Do not write unit tests in this repository.** No `tests/` tree, no
`pytest` files, no test-only fixtures. If a change feels like it
needs a test, surface that to the user instead of adding one — they
will decide where the coverage should live (often in pragma-os e2e
suites or downstream consumers).

## Publishing to PyPI

Package: `pragmatiks-sdk` on [PyPI](https://pypi.org/project/pragmatiks-sdk/).

Publishing is fully automated by `.github/workflows/publish.yaml`. Humans change `main` only through pull requests; pragma-ci-bot pushes the bump commit. The workflow runs on every merge to `main` and on a manual dispatch from `main`. Runs queue one at a time (concurrency group `pragma-sdk-release`), and each run releases the latest `main`, not the commit that triggered it. Jobs:

1. **bump** (no environment, read-only): `cz bump --yes --changelog` inspects the conventional commits since the last `v$version` tag, computes the next version (PATCH for `fix:`, MINOR for `feat:`, MAJOR for `BREAKING CHANGE:` or `!`), updates `[project] version`, `[tool.commitizen] version`, `CHANGELOG.md` and the `uv.lock` self-entry, commits, tags `v$version`, and hands the commit and tag to the next job as a git bundle. The bump commit message carries `[skip ci]` (`bump_message`), so it starts no workflow.
2. **push** (`release` environment, only when a bump happened): checks that the bundle holds one commit on top of `main` that changes only `pyproject.toml`, `CHANGELOG.md` and `uv.lock`, then pushes `main` and the tag atomically with a pragma-ci-bot GitHub App token. If `main` moved in the meantime, the job starts a new run of `publish.yaml` on `main` and ends without releasing; that run bumps from the newer `main`. Any other rejection fails the job.
3. **build**: checks out the pushed commit, verifies the tag resolves to it, and builds via `uv build`.
4. **publish** (`pypi` environment): uploads the distributions via `pypa/gh-action-pypi-publish` (PyPI OIDC trusted publisher bound to `publish.yaml` + the `pypi` environment); files already on PyPI are skipped.
5. **dispatch** (`release` environment): triggers `update-sdk.yaml` in `pragma-cli`, `pragma-os` and `pragma-providers` to bump lockfiles; a failed dispatch does not stop the others.
6. **release**: creates the GitHub Release with the distributions, or re-uploads them and publishes it if it already exists. The Release is the last step, so its existence marks a complete release.

### Recovering a failed release

If any job after **push** fails, the tag is on `main` but the release is incomplete. Any later run completes it: when **bump** finds nothing to bump and the current version's `v$version` tag (anywhere on `main`) has no published GitHub Release, the run skips **push** and runs **build**, **publish**, **dispatch** and **release** for the tag's commit. Merges that commitizen does not bump on (`docs:`, `chore:`) do not block this. Recover with "Re-run all jobs" or "Re-run failed jobs" on the failed run, or `gh workflow run publish.yaml --ref main`; every step is safe to repeat. A releasable merge that lands first bumps past the stranded version instead.

### Do NOT run `cz bump` locally

Pre-bumping `pyproject.toml` in a feature branch is forbidden:

- The local `cz bump` creates a `v$next` tag pointing at the branch commit. After squash-merge, that tag becomes orphan (points to an unreachable commit).
- `pyproject.toml` is now ahead of the latest git tag on origin. The next CI `cz bump` gets confused — depending on the commit history it can detect the wrong increment (e.g. MAJOR instead of MINOR) and fail on incremental-changelog generation.
- Recovery requires reverting `pyproject.toml` and `CHANGELOG.md` back to the previous version in a `chore:` pull request and merging it, so CI can re-anchor.

The correct developer flow:

1. Make your changes on a feature branch.
2. Use conventional commits (`feat:`, `fix:`, `refactor:`, etc.). Do not touch the `version` field in `pyproject.toml` and do not edit `CHANGELOG.md`.
3. Open a PR.
4. Rebase-merge to main per Delivery.
5. CI bumps (pushed by pragma-ci-bot with `[skip ci]`), tags, builds, publishes, and cascades.

### Tag format

`v{version}` (e.g. `v4.2.0`).
