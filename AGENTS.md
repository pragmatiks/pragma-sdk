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

## Engineering Principles

Canonical engineering rules for all Pragmatiks code in this repository. Workers (developers and reviewers) must follow these in every dispatch. Reviewers must check each PR against this list and produce one finding per violation.

### Scope

Applies to all code in this repository. Some principles only apply to one language or stack — flagged where relevant.

This section is the ground truth for engineering principles in this repository. The same text is embedded in every Pragmatiks subrepo's `AGENTS.md` and `CLAUDE.md`. When a principle changes, every embed must be updated in lockstep and the corresponding `pragmatiks-lint` / `@pragmatiks/lint` rule versions bumped.

### Enforcement layers

| Layer | What | Where |
|---|---|---|
| 1. Style + standard smells | `ruff` (Python), `eslint` (TS) with curated rule set | per-repo `task check` / `pnpm lint` |
| 2. Complexity gating | `radon` / `xenon` (Python), `eslint-plugin-sonarjs/cognitive-complexity` (TS) | CI fail on regression |
| 3. Pragmatiks-specific rules | `semgrep` ruleset (cross-language) + custom scripts | shared via `pragmatiks-lint` (PyPI) and `@pragmatiks/lint` (npm) |

If a principle has a programmatic check, the reviewer relies on the tool. If the principle is judgment-based, the reviewer comments with `⚠️` severity.

---

### 1. YAGNI — You Aren't Gonna Need It

Do not add features, abstractions, or configuration for hypothetical future needs. No premature generalization, no speculative interfaces, no "we might need this later" code.

**Programmatic check**:
- Python: `vulture` flags unused functions and dead branches.
- TS: `knip` flags unused exports, files, and dependencies.

**Reviewer hint**: flag any new abstraction layer not justified by current callers.

### 2. KISS — Keep It Simple

Prefer the simplest implementation that works. Three similar lines beat a premature abstraction. Inline the obvious; abstract only when a third caller appears.

**Programmatic check**:
- Python: `ruff C901` (cyclomatic complexity threshold).
- TS: `eslint-plugin-sonarjs/cognitive-complexity`.

**Reviewer hint**: extract-method PR? Verify there are at least three callers in the diff or repo.

### 3. Boy Scout Rule

Leave the file better than you found it. Small adjacent cleanup (rename, move, dead-line removal) is welcome when touching a file. Do not pile in unrelated refactors.

**Programmatic check**: none — judgment.

**Reviewer hint**: if a PR touches no nearby messy code, no penalty. If it adds new mess, block.

### 4. Open–Closed Principle

Modules should be open for extension and closed for modification. New behavior added by adding code, not by modifying existing tested code paths.

**Programmatic check**: none — judgment.

**Reviewer hint**: if a PR modifies a stable public interface or stable internal contract to add a feature that could have been added via a new function/method, request an alternative.

### 5. Single Responsibility Principle

Each function, method, class, and module should have one reason to change. If you cannot describe what a unit does without saying "and" or "or", split it.

**Programmatic check**:
- Function names with `_and_`, `_or_`, `And`, `Or` flagged by `pra-srp-and-or-name` semgrep rule.
- Function size: `eslint max-lines-per-function`, `max-statements`, `max-depth`. Python: `ruff PLR0915` (too many statements), `PLR0912` (too many branches).
- Cognitive complexity from #2.

**Reviewer hint**: if a function name reads as compound, splitting is mandatory.

### 6. Always Use Dependency Injection

Pass dependencies in via constructor / function arguments. Do not instantiate concrete services inside business logic. Wire the graph at the application boundary (FastAPI lifespan, CLI entry point, Next.js server boundary, test harness).

**Programmatic check**:
- `pra-no-inline-instantiation` semgrep rule (heuristic): flags concrete-class instantiation inside non-boundary modules. False positives expected — allowlist module paths (`main.py`, `app.py`, `lifespan.py`, `entry.ts`, etc.).

**Reviewer hint**: a class that constructs an `httpx.AsyncClient` inside `__init__` is wrong; it should accept one as a constructor arg.

### 7. I/O Prefix Discipline

Function/method names starting with `get_`, `fetch_`, `retrieve_`, `load_`, `save_`, `read_`, `write_`, `query_` must perform I/O (network, disk, database, IPC). Pure-computation functions must use neutral names (`compute_*`, `build_*`, `derive_*`, `format_*`, `parse_*`).

**Programmatic check**:
- `pra-io-prefix-mismatch` semgrep rule: flags `get_*` / `fetch_*` / `retrieve_*` functions whose body contains no `await`, no httpx/requests/db client call, no file open. Heuristic; allowlist via decorator (`@no_io`) or function tag.

**Reviewer hint**: a `get_user_id_from_token(token: str) -> str` that just decodes a JWT must be renamed `parse_user_id_from_token` or `extract_user_id`.

### 8. Twelve-Factor App

Configuration via environment variables only. Read environment at the application boundary, never deep in business logic. No credentials, URLs, or behavior flags hard-coded. Stateless processes. Treat backing services (DB, cache, queue) as attached resources via URLs.

**Programmatic check**:
- `pra-env-read-deep` semgrep rule: flags `os.environ` / `os.getenv` / `process.env` reads outside designated boundary modules.
- `pra-no-hardcoded-secrets` semgrep rule: flags string literals matching common credential patterns (`sk-`, `AKIA`, etc.).

**Reviewer hint**: env reads should live in a settings module (Python: `Settings` Pydantic class; TS: a single `env.ts` boundary file).

### 9. Clean Code (default)

When unsure, follow Clean Code: meaningful names, small functions, single level of abstraction per function, no flag arguments, fewer arguments over more, prefer pure functions, fail fast at boundaries.

**Programmatic check**: combination of `ruff`, `eslint`, `eslint-plugin-sonarjs`, `eslint-plugin-unicorn`.

**Reviewer hint**: if a function takes a boolean flag that switches behavior, flag (split into two functions).

### 10. No Comments

The code must be self-explanatory. Do not write comments. Exceptions:

- Public docstrings on library APIs (`pragma-sdk` public surface).
- A single-line WHY comment for a non-obvious workaround, hidden constraint, or subtle invariant. Removing it would confuse a future reader.

Forbidden: block comments restating what the code does; section dividers; commented-out code; "added for X" / "used by Y" trail comments; multi-line docstrings on private internals; planning comments left in source (`# TODO: refactor later`).

**Programmatic check**:
- `pra-no-block-comments` semgrep rule: flags multi-line `#` blocks in Python and `/* ... */` blocks in TS that are not docstrings.
- `pra-no-todo-comments` semgrep rule: flags `# TODO` / `// TODO` / `/* TODO */`.
- Existing custom script for comment ban (to migrate to semgrep).

**Reviewer hint**: every comment in the diff must be justifiable as WHY. Otherwise: delete and rename code instead.

### 11. Semantic Names — No Abbreviations

Identifiers must use full words. No `k8s`, `cfg`, `db`, `req`, `res`, `ctx`, `tmp`, `pkg`, `svc`, `mgr`, `repo`, `usr`, `pwd`, `idx`, `cnt`, `msg`, `err`, etc. Use `kubernetes`, `config`, `database`, `request`, `response`, `context`, `temporary`, `package`, `service`, `manager`, `repository`, `user`, `password`, `index`, `count`, `message`, `error`.

**Allowlist** (industry-standard exceptions):
- `id`, `url`, `uri`, `api`, `cli`, `sdk`, `os`, `io`, `ip`, `tls`, `ssl`, `jwt`, `json`, `yaml`, `html`, `css`, `dom`, `ast`, `gpu`, `cpu`, `ram`, `vm`.
- React-specific: `props`, `ref`, `e` (event handler param).
- Python-specific: `cls`, `self`, `kwargs`, `args`.

**Programmatic check**:
- `eslint-plugin-unicorn/prevent-abbreviations` (TS) — direct fit, with allowlist config.
- `pra-no-abbreviations` semgrep rule (Python) — regex matching forbidden short identifiers, with allowlist.

**Reviewer hint**: `db`, `cfg`, `k8s` in any new code = blocker.

### 12. Compound Names Violate SRP

If a function or method name contains `and`, `or`, `then`, or describes multiple actions, it violates SRP and must be split. Same applies to class names and module names. Examples to forbid: `validate_and_save_user`, `fetch_or_create_session`, `build_and_publish_wheel`.

**Programmatic check**:
- `pra-srp-and-or-name` semgrep rule (cross-language).

**Reviewer hint**: blocker — propose the split inline.

---

### Reviewer protocol

Every reviewer dispatch must:

1. Run `pragmatiks-lint check` (programmatic findings) before reading the diff.
2. Read the diff.
3. For each principle, produce findings as:

   ```
   path:line: <emoji> <severity>: <principle #N> <problem>. <fix>.
   ```

   Severities: 🚨 blocker · ⚠️ important · 💡 nit.

4. Final verdict: `APPROVE` / `APPROVE_WITH_NITS` / `REQUEST_CHANGES`.

A reviewer who fails to invoke programmatic tooling but only eyeballs the diff is incomplete and should be re-run.

### Developer protocol

Every developer dispatch must:

1. Read this `## Engineering Principles` section before starting.
2. Run `pragmatiks-lint check` locally before opening a PR.
3. Resolve all 🚨 blockers from the lint pack. ⚠️ findings: address or justify in PR body.
4. State principle compliance in the callback to the supervisor.

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
