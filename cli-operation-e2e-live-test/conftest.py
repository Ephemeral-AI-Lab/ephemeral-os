"""Shared fixtures: gateway bring-up and sandbox / workspace-session lifecycle.

Lifecycle fixtures guarantee teardown even when a test fails mid-way — the main
reason this suite is in pytest rather than shell.
"""

import logging

import pytest

from core import cleanup, gateway
from manager.management import helpers as mgmt

_timing_log = logging.getLogger("e2e.timing")
_test_seconds = {}


def pytest_runtest_logreport(report):
    """Emit a live per-test total-duration line (setup + call + teardown)."""
    _test_seconds[report.nodeid] = _test_seconds.get(report.nodeid, 0.0) + report.duration
    if report.when == "teardown":
        _timing_log.info(
            "⏱  %s — %.3fs total", report.nodeid, _test_seconds.pop(report.nodeid, 0.0)
        )


@pytest.fixture(scope="session", autouse=True)
def gateway_up():
    """Ensure a gateway is running before any test (reused across the session)."""
    gateway.ensure_up()


@pytest.fixture(scope="session", autouse=True)
def _session_sandbox_cleanup(gateway_up):
    """Safety net: destroy any sandbox the suite created but a test leaked.

    Per-test fixtures already tear down their own sandboxes; this catches inline
    creates that failed before cleanup. Only suite-created ids are touched.
    """
    yield
    for sandbox_id in cleanup.drain():
        try:
            mgmt.destroy_sandbox(sandbox_id)
        except Exception:
            pass


@pytest.fixture
def sandbox():
    """A ready sandbox, destroyed on teardown. Yields the sandbox id."""
    created = mgmt.create_sandbox()
    sandbox_id = created.get("id")
    assert sandbox_id, f"create_sandbox failed: {created}"
    try:
        yield sandbox_id
    finally:
        mgmt.destroy_sandbox(sandbox_id)
