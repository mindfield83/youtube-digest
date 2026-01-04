# tests/e2e/conftest.py
"""E2E test configuration and fixtures."""
import os

import pytest


def pytest_addoption(parser):
    """Add command line options for E2E tests."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run E2E tests with real APIs",
    )


def pytest_configure(config):
    """Configure pytest for E2E tests."""
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")

    # Set environment variable if --run-e2e is passed
    if config.getoption("--run-e2e"):
        os.environ["RUN_E2E_TESTS"] = "1"


@pytest.fixture(scope="session")
def e2e_enabled(request):
    """Check if E2E tests are enabled."""
    return request.config.getoption("--run-e2e")
