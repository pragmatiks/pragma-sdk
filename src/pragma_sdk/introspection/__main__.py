"""Entry point of ``python -m pragma_sdk.introspection --output <path>``.

Loads the provider ``PRAGMA_PROVIDER_DISTRIBUTION`` names and writes one JSON
introspection report to the output path; nothing is printed on stdout. Exits
0 once the report is written, whether it reports success or failure, and 1
when it cannot be written.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

import structlog

from pragma_sdk.declaration.provider import load_declared_provider, normalize_distribution_name
from pragma_sdk.declaration.settings import load_provider_settings
from pragma_sdk.diagnostics.structured_logging import configure_logging
from pragma_sdk.introspection.report import build_introspection_failure, build_introspection_success
from pragma_sdk.protocol.introspection import IntrospectionFailure, IntrospectionSuccess


logger = structlog.get_logger()


def parse_output_path() -> Path:
    """Read the report path from the command line.

    Returns:
        Path the report is written to.
    """
    parser = argparse.ArgumentParser(description="Pragmatiks provider introspection")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    return arguments.output


def introspect_provider() -> IntrospectionSuccess | IntrospectionFailure:
    """Load the provider ``PRAGMA_PROVIDER_DISTRIBUTION`` names and build its report.

    The normalized distribution name is bound to the structlog context as
    soon as the settings name it. A failure is logged as
    ``introspection_failed`` with a redacted exception trace, and with the
    distribution once the settings name it.

    Returns:
        A success report, or a failure report when the settings are invalid,
        the provider cannot be loaded or exits while it loads, or a resource
        type's schema cannot be built.
    """
    try:
        settings = load_provider_settings()
        structlog.contextvars.bind_contextvars(distribution=normalize_distribution_name(settings.distribution))

        return build_introspection_success(load_declared_provider(settings.distribution))
    except (Exception, SystemExit) as error:
        logger.exception("introspection_failed")

        return build_introspection_failure(error)


def main() -> None:
    """Write the introspection report of the provider the environment names, as UTF-8 JSON.

    Anything the provider package prints while it loads goes to stderr, and
    log events go to stderr as JSON lines.

    Raises:
        SystemExit: With status 1 when the report cannot be written, logged
            as ``introspection_report_write_failed``.
    """
    configure_logging()
    output_path = parse_output_path()

    with contextlib.redirect_stdout(sys.stderr):
        report = introspect_provider()

    try:
        output_path.write_text(report.model_dump_json(), encoding="utf-8")
    except OSError as error:
        logger.exception("introspection_report_write_failed", output=str(output_path))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
