"""No test reaches the Anthropic API: the key is removed for every test (extract_approps reads it at call time).
A test of the vision path mocks the client or replays recorded pages; a page image is never spent by a test run."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: calls an outside service (api.govinfo.gov); "
                                       "skipped unless RUN_NETWORK_TESTS=1")


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
