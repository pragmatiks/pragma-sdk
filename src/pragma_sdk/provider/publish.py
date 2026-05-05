"""Helpers for the wheel-based provider publish flow.

The pragma-os API exposes ``POST /provider-versions`` which accepts a
JSON payload referencing a wheel hosted in Artifact Registry. The
publishing client (``PragmaClient.publish_provider``) is responsible
for:

1. Building the provider wheel locally with ``uv build --wheel``.
2. Discovering resource schemas from the provider package using
   :func:`pragma_sdk.provider.extract_schemas`.
3. Uploading the wheel to GCP Artifact Registry via ``twine`` (with
   ``keyrings.google-artifactregistry-auth`` providing credentials).
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
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pragma_sdk.provider.extract_schemas import extract_schemas


DEFAULT_ARTIFACT_REPO = "europe-west4-python.pkg.dev/pragmatiks-prod/pragma-providers"


@dataclass(frozen=True)
class WheelPublishPayload:
    """Result of preparing a provider for wheel-based publishing.

    Attributes:
        name: Provider short name (without ``org/`` prefix).
        version: Semver string read from the provider's pyproject.toml
            (or supplied explicitly by the caller).
        wheel_url: Canonical ``https://...whl`` URL of the uploaded
            wheel in Artifact Registry.
        config_schema: Top-level provider config schema, or ``None``
            when the provider exposes none.
        outputs_schema: Top-level provider outputs schema, or ``None``
            when the provider exposes none.
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
    config_schema: dict[str, Any] | None
    outputs_schema: dict[str, Any] | None
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
    pyproject = provider_dir / "pyproject.toml"

    if not pyproject.exists():
        raise FileNotFoundError(f"No pyproject.toml found in {provider_dir}")

    with pyproject.open("rb") as f:
        return tomllib.load(f)


def _detect_provider_package(pyproject: dict[str, Any]) -> str:
    """Resolve the importable Python package name for a provider.

    Mirrors :func:`pragma_sdk.provider.extract_schemas.detect_provider_package`
    but reads from a parsed pyproject in any directory rather than the
    current working directory.

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        Importable package name.

    Raises:
        ValueError: If neither ``[tool.pragma].package`` nor a
            ``-provider`` distribution name is present.
    """
    pragma_package = pyproject.get("tool", {}).get("pragma", {}).get("package")

    if pragma_package:
        return pragma_package

    name = pyproject.get("project", {}).get("name", "")

    if name and name.endswith("-provider"):
        return name.replace("-", "_")

    raise ValueError(
        "Could not determine provider package name. Set [tool.pragma].package or "
        "name the distribution '<x>-provider' in pyproject.toml."
    )


def _detect_provider_version(pyproject: dict[str, Any]) -> str | None:
    """Read ``[project].version`` from a parsed pyproject.

    Args:
        pyproject: Parsed pyproject.toml data.

    Returns:
        The declared version, or ``None`` if not present.
    """
    version = pyproject.get("project", {}).get("version")
    return version if isinstance(version, str) else None


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


def _build_wheel(provider_dir: Path, out_dir: Path) -> Path:
    """Run ``uv build --wheel`` for a provider directory.

    Args:
        provider_dir: Path to the provider source tree.
        out_dir: Directory the built wheel should be written into.

    Returns:
        Path to the produced ``.whl`` file.

    Raises:
        RuntimeError: If ``uv build`` exits non-zero or fails to emit a
            single wheel artifact.
        FileNotFoundError: If the ``uv`` binary is not on ``PATH``.
    """
    uv = shutil.which("uv")

    if uv is None:
        raise FileNotFoundError("'uv' binary not found on PATH; install uv to publish providers")

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
    without it being installed in the publisher's environment. The
    addition is reverted on exit, including any modules that were
    imported in the meantime so a re-run starts clean.

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


def _upload_wheel_to_artifact_registry(wheel: Path, artifact_repo: str) -> str:
    """Upload a wheel to GCP Artifact Registry via ``twine``.

    Relies on ``keyrings.google-artifactregistry-auth`` being installed
    so ``twine`` can pick up Application Default Credentials.

    Args:
        wheel: Path to the ``.whl`` file to upload.
        artifact_repo: Repository host and path of the form
            ``<region>-python.pkg.dev/<project>/<repo>``.

    Returns:
        Canonical ``https://<repo>/<wheel-name>`` URL of the uploaded
        wheel.

    Raises:
        FileNotFoundError: If the ``twine`` binary is not on ``PATH``.
        RuntimeError: If ``twine upload`` exits non-zero.
    """
    twine = shutil.which("twine")

    if twine is None:
        raise FileNotFoundError(
            "'twine' binary not found on PATH; install 'twine' and "
            "'keyrings.google-artifactregistry-auth' to publish providers"
        )

    repository_url = f"https://{artifact_repo}/"

    completed = subprocess.run(
        [twine, "upload", "--repository-url", repository_url, str(wheel)],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"'twine upload' failed (exit {completed.returncode}):\n{completed.stderr}")

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
    version: str | None = None,
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

    Args:
        provider_dir: Path to the provider source tree.
        name: Provider short name (without ``org/`` prefix). Defaults
            to ``[tool.pragma].name`` or the ``-provider`` distribution
            stem.
        version: Semver string. Defaults to ``[project].version``.
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
        FileNotFoundError: If ``provider_dir`` is missing or
            ``pyproject.toml`` is absent, or required tools (``uv``,
            ``twine``) are not on ``PATH``.
        ValueError: If the provider name or version cannot be
            determined from pyproject.toml and no override was passed.
        RuntimeError: If the build or upload step fails.
    """  # noqa: DOC502
    provider_path = Path(provider_dir).expanduser().resolve()

    if not provider_path.is_dir():
        raise FileNotFoundError(f"Provider directory does not exist: {provider_path}")

    pyproject = _load_provider_pyproject(provider_path)
    package_name = _detect_provider_package(pyproject)

    resolved_name = name or _detect_provider_short_name(pyproject)
    resolved_version = version or _detect_provider_version(pyproject)

    if not resolved_version:
        raise ValueError(
            f"Provider version not specified and [project].version is missing in {provider_path / 'pyproject.toml'}"
        )

    pyproject_runtime_image, pyproject_entrypoint = _detect_runtime_overrides(pyproject)
    final_runtime_image = runtime_image if runtime_image is not None else pyproject_runtime_image
    final_entrypoint = entrypoint if entrypoint is not None else pyproject_entrypoint

    repo = _normalize_artifact_repo(artifact_repo)
    catalog_name = f"{catalog_prefix}/{resolved_name}"

    with tempfile.TemporaryDirectory(prefix="pragma-publish-") as tmp:
        wheel = _build_wheel(provider_path, Path(tmp))
        schemas = _extract_schemas_from_dir(provider_path, package_name, catalog_name)
        wheel_url = _upload_wheel_to_artifact_registry(wheel, repo)

    return WheelPublishPayload(
        name=resolved_name,
        version=resolved_version,
        wheel_url=wheel_url,
        config_schema=None,
        outputs_schema=None,
        resource_schemas=_to_resource_schemas_dict(schemas),
        runtime_image=final_runtime_image,
        entrypoint=final_entrypoint,
    )
