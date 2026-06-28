"""runtime · workspace_session family helpers.

Runtime ops route to a sandbox via ``runtime --sandbox-id <id>`` (the flag
precedes the operation name).
"""

from core.cli import runtime
from core.config import NETWORK_PROFILE


def create(sandbox_id, profile=NETWORK_PROFILE):
    return runtime(
        sandbox_id, "create_workspace_session", "--network-profile", profile
    )


def destroy(sandbox_id, workspace_session_id, grace_s=None):
    args = ["destroy_workspace_session", "--workspace-session-id", workspace_session_id]
    if grace_s is not None:
        args += ["--grace-s", str(grace_s)]
    return runtime(sandbox_id, *args)
