"""Helpers for the wheel-based provider publish flow.

The pragma-os API exposes ``POST /provider-versions`` which accepts a
JSON payload referencing a wheel hosted in Artifact Registry. The
publishing client (``PragmaClient.publish_provider``) is responsible
for:

1. Building the provider wheel locally with ``uv build --wheel``.
2. Discovering resource schemas from the provider package using
   :func:`pragma_sdk.provider.extract_schemas`.
3. Uploading the wheel to GCP Artifact Registry via ``uv publish``
   (with ``keyrings.google-artifactregistry-auth`` providing
   credentials through uv's ``--keyring-provider subprocess`` mode).
4. Posting a JSON metadata payload to the API.

This module groups the side-effecting steps (1-3) so the sync and
async client paths can share a single implementation. The transport
of the final POST is handled by the client itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pragma_sdk.provider.extract_schemas import (
    detect_provider_package,
    extract_schemas,
    load_pyproject,
)


DEFAULT_ARTIFACT_REPO = "europe-west4-python.pkg.dev/pragmatiks-prod/pragma-providers"
"""Pragmatiks-managed Artifact Registry Python repo used by default.

Override per-call via ``publish_provider(..., artifact_repo=...)`` when
publishing to a different GCP project or region.
"""

_GAR_KEYRING_USERNAME = "oauth2accesstoken"
_UV_KEYRING_PROVIDER = "subprocess"


@dataclass(frozen=True)
class WheelPublishPayload:
    """Result of preparing a provider for wheel-based publishing.

    Attributes:
        name: Provider short name (without ``org/`` prefix).
        version: Semver string read from the provider's pyproject.toml.
        wheel_url: Canonical ``https://...whl`` URL of the uploaded
            wheel in Artifact Registry.
        resource_schemas: Per-resource schema map keyed by resource
            type name, matching the API's
            ``dict[str, ResourceSchemaResponse]`` shape.
        runtime_image: Optional runtime base image override read from
            ``[tool.pragma].image``.
        entrypoint: Optional runtime entrypoint override read from
            ``[tool.pragma].entrypoint``.
    """

    name: str
    version: str
    wheel_url: str
    resource_schemas: dict[str, dict[str, Any]]
    runtime_image: str | None
    entrypoint: list[str] | None


def _load_provider_pyproject(provider_dir: Path) -> dict[str, Any]:
    """Read the ``pyproject.toml`` shipped inside a provider directory.

    Args:
        provider_dir: Directory containing the provider's
            ``pyproject.toml``.

    Returns:
        Parsed TOML data.

    Raises:
        FileNotFoundError: If no ``pyproject.toml`` is present.
    """
    data = load_pyproject(provider_dir / "pyproject.toml")

    if data is None:
        raise FileNotFoundError(f"No pyproject.toml found in {provider_dir}")

    return data


def _require_provider_package(pyproject: dict[str, Any]) -> str:
    """Strict wrapper around :func:`detect_provider_package`.

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        Importable package name.

    Raises:
        ValueError: If neither ``[tool.pragma].package`` nor a
            ``-provider`` distribution name is present.
    """
    package = detect_provider_package(pyproject)

    if package is None:
        raise ValueError(
            "Could not determine provider package name. Set [tool.pragma].package or "
            "name the distribution '<x>-provider' in pyproject.toml."
        )

    return package


def _detect_provider_version(pyproject: dict[str, Any]) -> str:
    """Read ``[project].version`` from a parsed pyproject.

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        The declared version.

    Raises:
        ValueError: If ``[project].version`` is missing or not a
            string. The wheel build pulls the version from this field
            and bakes it into the wheel filename, so the SDK refuses
            to publish without it rather than letting the API row
            desynchronise from the uploaded artifact.
    """
    version = pyproject.get("project", {}).get("version")

    if not isinstance(version, str) or not version:
        raise ValueError(
            "[project].version is missing from pyproject.toml — bump it before publishing. "
            "(The wheel filename is derived from this field; the SDK refuses to publish "
            "without it to keep the API record and the uploaded wheel in sync.)"
        )

    return version


def _detect_provider_short_name(pyproject: dict[str, Any]) -> str:
    """Resolve the provider's short catalog name.

    Reads ``[tool.pragma].name`` if present, otherwise derives the name
    from a ``-provider`` distribution name (``foo-provider`` →
    ``foo``).

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        Provider short name.

    Raises:
        ValueError: If no name can be determined.
    """
    pragma_name = pyproject.get("tool", {}).get("pragma", {}).get("name")

    if isinstance(pragma_name, str) and pragma_name:
        return pragma_name

    dist_name = pyproject.get("project", {}).get("name", "")

    if isinstance(dist_name, str) and dist_name.endswith("-provider"):
        return dist_name.removesuffix("-provider")

    raise ValueError(
        "Could not determine provider short name. Set [tool.pragma].name or "
        "name the distribution '<short>-provider' in pyproject.toml, or pass name= explicitly."
    )


def _detect_runtime_overrides(pyproject: dict[str, Any]) -> tuple[str | None, list[str] | None]:
    """Read optional runtime overrides from ``[tool.pragma]``.

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        ``(runtime_image, entrypoint)`` tuple. Either entry is ``None``
        when not set in pyproject.

    Raises:
        TypeError: If declared values do not have the expected shape.
    """
    pragma = pyproject.get("tool", {}).get("pragma", {})

    runtime_image = pragma.get("image")

    if runtime_image is not None and not isinstance(runtime_image, str):
        raise TypeError("[tool.pragma].image must be a string")

    entrypoint = pragma.get("entrypoint")

    if entrypoint is not None:
        if not isinstance(entrypoint, list) or not all(isinstance(x, str) for x in entrypoint):
            raise TypeError("[tool.pragma].entrypoint must be a list of strings")

    return runtime_image, entrypoint


def _resolve_uv() -> str:
    """Locate the ``uv`` binary or raise a clear error.

    Returns:
        Absolute path to the ``uv`` executable.

    Raises:
        FileNotFoundError: If ``uv`` is not on ``PATH``.
    """
    uv = shutil.which("uv")

    if uv is None:
        raise FileNotFoundError(
            "'uv' binary not found on PATH; install uv (and "
            "'keyrings.google-artifactregistry-auth' for credentials) to publish providers"
        )

    return uv


def _build_wheel(uv: str, provider_dir: Path, out_dir: Path) -> Path:
    """Run ``uv build --wheel`` for a provider directory.

    Args:
        uv: Path to the ``uv`` binary (resolved by :func:`_resolve_uv`).
        provider_dir: Path to the provider source tree.
        out_dir: Directory the built wheel should be written into.

    Returns:
        Path to the produced ``.whl`` file.

    Raises:
        RuntimeError: If ``uv build`` exits non-zero or fails to emit a
            single wheel artifact.
    """
    completed = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir), str(provider_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"'uv build --wheel' failed (exit {completed.returncode}):\n{completed.stderr}")

    wheels = sorted(out_dir.glob("*.whl"))

    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one wheel in {out_dir}, found {len(wheels)}: {wheels}")

    return wheels[0]


@contextmanager
def _provider_on_sys_path(provider_dir: Path):
    """Temporarily add the provider's ``src/`` directory to ``sys.path``.

    Used so :func:`extract_schemas` can import the provider package
    without it being installed in the publisher's environment. Only
    ``sys.path`` is mutated; ``sys.modules`` entries created by the
    import are left in place. That is fine for the one-shot CLI use
    case, but a long-lived process publishing the same provider twice
    will see cached modules from the first run.

    Args:
        provider_dir: Provider source tree root (the directory that
            contains ``pyproject.toml`` and ``src/``).

    Yields:
        None.
    """
    src = provider_dir / "src"
    candidates = [src, provider_dir]
    added: list[str] = []

    for candidate in candidates:
        if candidate.is_dir():
            entry = str(candidate.resolve())
            if entry not in sys.path:
                sys.path.insert(0, entry)
                added.append(entry)

    try:
        yield
    finally:
        for entry in added:
            if entry in sys.path:
                sys.path.remove(entry)


def _extract_schemas_from_dir(
    provider_dir: Path,
    package_name: str,
    catalog_name: str,
) -> list[dict[str, Any]]:
    """Run schema extraction against an out-of-tree provider package.

    Args:
        provider_dir: Provider source tree.
        package_name: Importable package name (e.g.
            ``postgres_provider``).
        catalog_name: Catalog identifier (``org/name``) recorded on
            each schema entry.

    Returns:
        List of schema dictionaries as emitted by
        :func:`pragma_sdk.provider.extract_schemas`.
    """
    with _provider_on_sys_path(provider_dir):
        return extract_schemas(package_name, catalog_name)


def _to_resource_schemas_dict(schemas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reshape ``extract_schemas`` output for the API request body.

    The API expects ``resource_schemas: dict[str, ResourceSchemaResponse]``
    (keyed by resource type name) while ``extract_schemas`` returns a
    flat list. Field-description entries are dropped because the API
    response model does not carry them.

    Args:
        schemas: List of schema dictionaries.

    Returns:
        Mapping of resource name to a ``ResourceSchemaResponse``-shaped
        dict.
    """
    return {
        entry["resource"]: {
            "provider": entry["provider"],
            "resource": entry["resource"],
            "description": entry.get("description"),
            "config_schema": entry.get("config_schema"),
            "outputs_schema": entry.get("outputs_schema"),
        }
        for entry in schemas
    }


def _upload_wheel_to_artifact_registry(uv: str, wheel: Path, artifact_repo: str) -> str:
    """Upload a wheel to GCP Artifact Registry via ``uv publish``.

    Delegates auth to the system keyring so
    ``keyrings.google-artifactregistry-auth`` can resolve credentials
    from Application Default Credentials. The ``oauth2accesstoken``
    username is the conventional GAR keyring key — the keyring backend
    looks up the actual OAuth2 token under that name.

    Args:
        uv: Path to the ``uv`` binary (resolved by :func:`_resolve_uv`).
        wheel: Path to the ``.whl`` file to upload.
        artifact_repo: Repository host and path of the form
            ``<region>-python.pkg.dev/<project>/<repo>``.

    Returns:
        Canonical ``https://<repo>/<wheel-name>`` URL of the uploaded
        wheel. This shape is the project's documented contract (see
        ``pragma-os`` ``docs/runbooks/wheel-provider-setup.md``) and
        is what the runtime container resolves at deploy time.

    Raises:
        RuntimeError: If ``uv publish`` exits non-zero.
    """
    publish_url = f"https://{artifact_repo}/"

    completed = subprocess.run(
        [
            uv,
            "publish",
            "--publish-url",
            publish_url,
            "--keyring-provider",
            _UV_KEYRING_PROVIDER,
            "--username",
            _GAR_KEYRING_USERNAME,
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"'uv publish' failed (exit {completed.returncode}):\n{completed.stderr}")

    return f"https://{artifact_repo}/{wheel.name}"


def _normalize_artifact_repo(artifact_repo: str) -> str:
    """Strip schemes and trailing slashes from an Artifact Registry repo URL.

    Args:
        artifact_repo: Raw repo URL (with or without scheme).

    Returns:
        Bare ``host/project/repo`` form.
    """
    if "://" in artifact_repo:
        parsed = urlparse(artifact_repo)
        host = parsed.netloc
        path = parsed.path
    else:
        host, _, path = artifact_repo.partition("/")
        path = "/" + path if path else ""

    return f"{host}{path}".rstrip("/")


def prepare_wheel_publish(
    provider_dir: str | Path,
    *,
    name: str | None = None,
    catalog_prefix: str,
    artifact_repo: str = DEFAULT_ARTIFACT_REPO,
    runtime_image: str | None = None,
    entrypoint: list[str] | None = None,
) -> WheelPublishPayload:
    """Build, extract schemas for, and upload a provider wheel.

    Performs steps 1-3 of the wheel publish flow and returns the data
    needed to issue ``POST /provider-versions``. The HTTP call itself
    is left to the SDK client so sync and async paths can share this
    helper.

    The provider's ``[project].version`` is the single source of
    truth: it is read from pyproject.toml, baked into the wheel
    filename by ``uv build``, and recorded on the API row. There is
    deliberately no ``version=`` override — bump pyproject and
    republish to cut a new version.

    Args:
        provider_dir: Path to the provider source tree.
        name: Provider short name (without ``org/`` prefix). Defaults
            to ``[tool.pragma].name`` or the ``-provider`` distribution
            stem.
        catalog_prefix: Namespace token (organization slug) the
            provider will be published under. Used purely to form the
            ``provider`` field on each extracted schema entry.
        artifact_repo: Artifact Registry Python repo, of the form
            ``<region>-python.pkg.dev/<project>/<repo>``.
        runtime_image: Optional runtime base image override; defaults
            to ``[tool.pragma].image`` when present.
        entrypoint: Optional runtime entrypoint override; defaults to
            ``[tool.pragma].entrypoint`` when present.

    Returns:
        :class:`WheelPublishPayload` with the wheel URL and schema
        data.

    Raises:
        FileNotFoundError: If ``provider_dir`` is missing, its
            ``pyproject.toml`` is absent, or the ``uv`` binary
            required for build/publish is not on ``PATH``.
        ValueError: If the provider name or ``[project].version``
            cannot be determined from pyproject.toml.
        TypeError: If ``[tool.pragma].image`` or
            ``[tool.pragma].entrypoint`` is declared with the wrong
            shape.
        RuntimeError: If the build or upload step fails.
    """  # noqa: DOC502
    provider_path = Path(provider_dir).expanduser().resolve()

    if not provider_path.is_dir():
        raise FileNotFoundError(f"Provider directory does not exist: {provider_path}")

    uv = _resolve_uv()

    pyproject = _load_provider_pyproject(provider_path)
    package_name = _require_provider_package(pyproject)

    resolved_name = name or _detect_provider_short_name(pyproject)
    resolved_version = _detect_provider_version(pyproject)

    pyproject_runtime_image, pyproject_entrypoint = _detect_runtime_overrides(pyproject)
    final_runtime_image = runtime_image if runtime_image is not None else pyproject_runtime_image
    final_entrypoint = entrypoint if entrypoint is not None else pyproject_entrypoint

    repo = _normalize_artifact_repo(artifact_repo)
    catalog_name = f"{catalog_prefix}/{resolved_name}"

    with tempfile.TemporaryDirectory(prefix="pragma-publish-") as tmp:
        wheel = _build_wheel(uv, provider_path, Path(tmp))
        schemas = _extract_schemas_from_dir(provider_path, package_name, catalog_name)
        wheel_url = _upload_wheel_to_artifact_registry(uv, wheel, repo)

    return WheelPublishPayload(
        name=resolved_name,
        version=resolved_version,
        wheel_url=wheel_url,
        resource_schemas=_to_resource_schemas_dict(schemas),
        runtime_image=final_runtime_image,
        entrypoint=final_entrypoint,
    )
