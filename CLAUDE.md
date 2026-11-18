# pragma-sdk

Python SDK for Pragmatiks (`pragmatiks-sdk` on PyPI): typed clients for API consumers, and the provider authoring surface, worker and introspection the Pragmatiks host runs providers through.

Delivery: pragma-kit `project-instructions.md`. Engineering rules: the `pragma-kit:engineering-principles` skill; load it before planning, writing or reviewing a change. Module map, features and dependencies: `docs/architecture.md`. Host protocol: `docs/protocol.md`. Releases: `docs/releasing.md`.

## Gotchas

- **Wheel contract.** The hub's admission imports `wheel.py`, `wheel_central_directory.py`, `wheel_check.py`, `wheel_names.py`, `provider_entry_point.py` and the wheel errors in `exceptions.py`: changing them changes what the platform admits (`docs/architecture.md`).
- **Protocol versions.** `protocol/` imports nothing from the SDK but `types`. A change to a frame shape, the introspection report or the worker's method set is a new `PROTOCOL_VERSION`; a transport change moves the host's minimum SDK version instead (`docs/protocol.md`).
- **Lean dependencies.** Every downstream Pragmatiks package resolves this SDK's dependencies, so a new top-level dependency has a high bar.
- **Async first.** Every API method is async in `AsyncPragmaClient`, with a sync wrapper in `PragmaClient`.
- **No tests.** No `tests/` tree, pytest files or test-only fixtures. When a change seems to need a test, tell the user; they decide where the coverage lives, often in downstream consumers.
- **Releases are CI's.** Never run `cz bump` or edit the version or `CHANGELOG.md`: a local bump leaves an orphan tag after the rebase merge and derails CI's next bump, and recovery takes a revert PR. Use conventional commits (`docs/releasing.md`).
- **Toolchain.** mise is the only toolchain source, locked in `mise.lock` and `.mise/locks/`; `[tool.uv] required-version` must equal the uv pin in `mise.toml`. Fresh clone: `mise trust`, then `mise run install`; `mise tasks` lists the rest.
