# Pragmatiks SDK Codex Instructions

## Project

`pragma-sdk` is the Python SDK for the Pragmatiks platform. It provides typed clients for API consumers and provider authors, and is distributed on PyPI as `pragmatiks-sdk`.

User-facing product surfaces must say **Pragmatiks**. Do not use "pragma-os", "OS", or operating-system framing in user-facing strings, docstrings, README content, API errors, Pydantic descriptions, or docs unless referring specifically to repository names or infrastructure paths that already depend on those names.

## Architecture

The high-level package layout is:

```text
src/pragma_sdk/
├── client.py          # PragmaClient (sync) + AsyncPragmaClient
├── models/            # Pydantic models shared with API
├── resources/         # Resource-specific client methods
└── provider/          # Provider authoring: Provider, Resource[Config, Outputs]
```

Core principles:

- Both sync `PragmaClient` and async `AsyncPragmaClient` expose equivalent APIs.
- Pydantic models define request and response types shared with the API.
- `httpx` handles HTTP transport.
- Public interfaces should keep precise type hints.
- The SDK is the source of truth for the `Provider` / `Resource[Config, Outputs]` provider authoring model.

## Repository Layout

```text
src/pragma_sdk/client.py      Sync and async API clients
src/pragma_sdk/models/        Pydantic models shared with the API
src/pragma_sdk/resources/     Resource-specific client methods
src/pragma_sdk/provider/      Provider authoring primitives and helpers
CLAUDE.md                    Claude Code instructions that must be preserved
Taskfile.yaml                Supported local task interface
pyproject.toml               Package metadata, dependencies, and version
uv.lock                      Locked dependency graph
```

## Commands

Use `task` as the project interface for routine development. Do not run `uv`, `pytest`, `ruff`, or other package tooling directly unless the user explicitly asks or the publishing flow below applies.

| Command | Purpose |
|---|---|
| `task install` | Sync dependencies |
| `task format` | Format with ruff |
| `task check` | Lint + type check |

There is no `task test` because this repo has no tests. There is no separate `task build`; package build and publish commands are documented in Publishing to PyPI.

## Testing Policy

This repo has no test suite at all. There is no `tests/` directory, no pytest files, and no `conftest.py`.

If a change feels like it needs a test, surface that to the user. They decide where coverage lives, usually in `pragma-os` e2e tests or downstream consumers. Do not add a `tests/` tree.

## Dependency Policy

Dependencies should resolve from registries in committed configuration.

Do not commit sibling-repo path overrides such as:

- `pragmatiks-sdk = { path = "../../../pragma-sdk", editable = true }`
- `extra-paths = ["../pragma-sdk/src"]`

Those paths break in agent worktrees. For local iteration against `pragma-os` or other consumers, use an ad hoc editable install against the consumer worktree's `.venv`, such as `uv pip install -e .`. Never commit the override.

## Secrets And Local Files

The SDK normally needs no local environment file.

If iterating against a local API, credential resolution order is:

- Environment variables: `PRAGMA_API_TOKEN`, `PRAGMA_API_URL`
- Context-specific tokens
- `~/.config/pragma/credentials`

Never commit token values, local secrets, personal Codex config, MCP auth, hooks, or machine-specific files. No `mise.local.toml` is required for routine SDK work.

## Claude Code Compatibility

Keep `CLAUDE.md` as-is. `AGENTS.md` carries Codex-specific durable instructions. Do not replace one with the other; keep both files consistent where rules are shared.

## Git And Worktrees

Codex typically works inside a per-thread worktree. Treat the current worktree as the working area for the current thread. Do not create additional worktrees beyond the current thread's unless asked.

Before editing, inspect relevant files and current git status. Never revert user changes or unrelated generated changes.

## Linear And Issue Work

Use Linear as the source of truth for planned work and follow-ups. Prefer the `linear`/`linearis` CLI when MCP tools are unavailable or the user asks for CLI usage; the two commands are equivalent on this machine and return JSON-friendly output.

Autonomy expectations:

- When the user asks to work on a Linear issue, read it first with comments and attachments, then map the issue to repo files and validation commands.
- If the user clearly starts work on a specific issue, move it to an active status when appropriate.
- If a bug, follow-up, cleanup, or feature is deferred for later and the scope is clear, create a Linear issue instead of leaving only chat context.
- Add obvious relationships when creating or updating issues: `--blocked-by`, `--blocks`, `--relates-to`, `--duplicate-of`, or `--parent-ticket`.
- When a PR is merged, identify associated Linear issues from branch names, commits, PR title/body, or user context, then mark fully completed issues Done and add a concise validation/merge comment.
- If merged work only partially addresses an issue, leave it open, comment with current status, and create/link follow-up issues as needed.

Escalate to the user before changing Linear when there is a real product or planning choice: ambiguous team/project, unclear priority, competing dependency direction, uncertain status, or whether a partial fix should close an issue.

Useful commands:

- `linear issues read PRA-123 --with-comments --with-attachments`
- `linear issues search "<query>" --limit 20`
- `linear issues create "<title>" --team PRA --description "<markdown>" --priority 3`
- `linear issues update PRA-123 --status "In Progress"`
- `linear issues update PRA-123 --status "Done"`
- `linear issues discuss PRA-123 --body "<markdown>"`

Do not mark issues Done for local-only work. Completion means the requested outcome is implemented, validated, and merged or explicitly accepted by the user.

## Publishing to PyPI

Package: `pragmatiks-sdk`.

Versioning uses Commitizen and conventional commits:

```bash
cz bump
cz bump --patch
cz bump --minor
```

Publishing uses:

```bash
uv build
uv publish
```

Publishing requires `PYPI_TOKEN`. Version files are managed through `pyproject.toml`, and tags use the format `v{version}`.

SDK changes ripple downstream. Publish flow can trigger the `update-sdk.yaml` cascade, lockfile bumps in `pragma-providers` and `pragma-cli`, and provider adaptations. Keep provider updates one commit per provider, never bundled.
