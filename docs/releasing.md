# Releasing

How `pragmatiks-sdk` is versioned, published and recovered.

Package: `pragmatiks-sdk` on [PyPI](https://pypi.org/project/pragmatiks-sdk/).

Publishing is fully automated by `.github/workflows/publish.yaml`. Humans change `main` only through pull requests; pragma-ci-bot pushes the bump commit. The workflow runs on every merge to `main` and on a manual dispatch from `main`. Runs queue one at a time (concurrency group `pragma-sdk-release`), and each run releases the latest `main`, not the commit that triggered it. Jobs:

1. **bump** (no environment, read-only): `cz bump --yes --changelog` inspects the conventional commits since the last `v$version` tag, computes the next version (PATCH for `fix:`, MINOR for `feat:`, MAJOR for `BREAKING CHANGE:` or `!`), updates `[project] version`, `[tool.commitizen] version`, `CHANGELOG.md` and the `uv.lock` self-entry, commits, tags `v$version`, and hands the commit and tag to the next job as a git bundle. The bump commit message carries `[skip ci]` (`bump_message`), so it starts no workflow.
2. **push** (`release` environment, only when a bump happened): checks that the bundle holds one commit on top of `main` that changes only `pyproject.toml`, `CHANGELOG.md` and `uv.lock`, then pushes `main` and the tag atomically with a pragma-ci-bot GitHub App token. If `main` moved in the meantime, the job starts a new run of `publish.yaml` on `main` and ends without releasing; that run bumps from the newer `main`. Any other rejection fails the job.
3. **build**: checks out the pushed commit, verifies the tag resolves to it, and builds via `uv build`.
4. **publish** (`pypi` environment): uploads the distributions via `pypa/gh-action-pypi-publish` (PyPI OIDC trusted publisher bound to `publish.yaml` + the `pypi` environment); files already on PyPI are skipped.
5. **dispatch** (`release` environment): triggers `update-sdk.yaml` in `pragma-cli`, `pragma-os` and `pragma-providers` to bump lockfiles; a failed dispatch does not stop the others.
6. **release**: creates the GitHub Release with the distributions, or re-uploads them and publishes it if it already exists. The Release is the last step, so its existence marks a complete release.

## Recovering a failed release

If any job after **push** fails, the tag is on `main` but the release is incomplete. Any later run completes it: when **bump** finds nothing to bump and the current version's `v$version` tag (anywhere on `main`) has no published GitHub Release, the run skips **push** and runs **build**, **publish**, **dispatch** and **release** for the tag's commit. Merges that commitizen does not bump on (`docs:`, `chore:`) do not block this. Recover with "Re-run all jobs" or "Re-run failed jobs" on the failed run, or `gh workflow run publish.yaml --ref main`; every step is safe to repeat. A releasable merge that lands first bumps past the stranded version instead.

## Do NOT run `cz bump` locally

Pre-bumping `pyproject.toml` in a feature branch is forbidden:

- The local `cz bump` creates a `v$next` tag pointing at the branch commit. The rebase-merge recreates that commit on `main`, so the tag becomes orphan (points to an unreachable commit).
- `pyproject.toml` is now ahead of the latest git tag on origin. The next CI `cz bump` gets confused — depending on the commit history it can detect the wrong increment (e.g. MAJOR instead of MINOR) and fail on incremental-changelog generation.
- Recovery requires reverting `pyproject.toml` and `CHANGELOG.md` back to the previous version in a `chore:` pull request and merging it, so CI can re-anchor.

The correct developer flow:

1. Make your changes on a feature branch.
2. Use conventional commits (`feat:`, `fix:`, `refactor:`, etc.). Do not touch the `version` field in `pyproject.toml` and do not edit `CHANGELOG.md`.
3. Open a PR.
4. Rebase-merge to main per Delivery.
5. CI bumps (pushed by pragma-ci-bot with `[skip ci]`), tags, builds, publishes, and cascades.

## Tag format

`v{version}` (e.g. `v4.2.0`).
