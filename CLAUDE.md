# CLAUDE.md

## Project

**pragma-sdk**: Python SDK for the Pragmatiks platform. Provides typed clients for API consumers and provider authors.

## Architecture

```
pragma-sdk/
└── src/pragma_sdk/
    ├── client.py          # PragmaClient (sync) and AsyncPragmaClient
    ├── models/            # Pydantic models (shared with API)
    ├── resources/         # Resource-specific client methods
    └── provider/          # Provider authoring (Provider, Resource, Config, Outputs)
```

## Features

**HTTP Clients**: Both sync (`PragmaClient`) and async (`AsyncPragmaClient`) with identical APIs.

**Provider Authoring**: `Provider`, `Resource[Config, Outputs]`, `Field[T]`, `FieldReference` for building providers.

**Testing Harness**: `ProviderHarness` for local lifecycle testing without deployment.

**Auto-discovery**: Credentials resolved from env vars, context-specific tokens, or `~/.config/pragma/credentials`.

## Dependencies

- `httpx` - Async HTTP client
- `pydantic` - Data validation and serialization
- `pyyaml` - YAML parsing for resource definitions

## Development

Always use `task` commands:

| Command | Purpose |
|---------|---------|
| `task format` | Format with ruff |
| `task check` | Lint + type check |

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

Package: `pragmatiks-sdk` on [PyPI](https://pypi.org/project/pragmatiks-sdk/)

**Versioning** (commitizen):
```bash
cz bump              # Bump version based on conventional commits
cz bump --patch      # Force patch bump
cz bump --minor      # Force minor bump
```

**Publishing**:
```bash
uv build             # Build wheel and sdist
uv publish           # Publish to PyPI (requires PYPI_TOKEN)
```

**Version files**: `pyproject.toml` (version field updated by commitizen)

**Tag format**: `v{version}` (e.g., `v0.15.1`)
