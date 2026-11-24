"""Test-session configuration shared by the runner and direct pytest calls."""

import sys
from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform != "win32" or config.getoption("basetemp") is not None:
        return

    # Pytest otherwise cleans a per-user pool, which can contain a
    # pytest-current link left by a run with a different Windows token.
    workspace = TemporaryDirectory(prefix="mark-pytest-", ignore_cleanup_errors=True)
    environment = pytest.MonkeyPatch()
    environment.setenv("PYTEST_DEBUG_TEMPROOT", workspace.name)
    config.add_cleanup(workspace.cleanup)
    config.add_cleanup(environment.undo)
