"""runtime · workspace_session: create then destroy a persistent session."""

from core.cli import is_error
from core.config import NETWORK_PROFILE
from runtime.workspace_session import helpers as ws


def test_create_and_destroy(sandbox):
    created = ws.create(sandbox)
    assert not is_error(created), created
    ws_id = created["workspace_session_id"]
    assert created["network_profile"] == NETWORK_PROFILE

    destroyed = ws.destroy(sandbox, ws_id)
    assert destroyed["destroyed"] is True
