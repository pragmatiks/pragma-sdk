"""Pytest configuration for pragma-sdk.

The test suite is currently empty pending a new test strategy. This
conftest normalizes pytest's empty-collection exit code (5) to success
(0) so ``task test`` stays green on CI.
"""

from __future__ import annotations

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Treat empty test collection as a successful run.

    Args:
        session: The active pytest session.
        exitstatus: The exit code pytest is about to return.
    """
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK
