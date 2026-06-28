"""Shared fixtures: gateway bring-up and sandbox / workspace-session lifecycle.

Lifecycle fixtures guarantee teardown even when a test fails mid-way — the main
reason this suite is in pytest rather than shell.
"""

import pytest

from core import gateway
from manager.management import helpers as mgmt
from runtime.workspace_session import helpers as ws


@pytest.fixture(scope="session", autouse=True)
def gateway_up():
    """Ensure a gateway is running before any test (reused across the session)."""
    gateway.ensure_up()


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


@pytest.fixture
def workspace_session(sandbox):
    """A persistent workspace session inside ``sandbox``, destroyed on teardown.

    Yields ``(sandbox_id, workspace_session_id)``.
    """
    created = ws.create(sandbox)
    ws_id = created.get("workspace_session_id")
    assert ws_id, f"create_workspace_session failed: {created}"
    try:
        yield sandbox, ws_id
    finally:
        ws.destroy(sandbox, ws_id)
