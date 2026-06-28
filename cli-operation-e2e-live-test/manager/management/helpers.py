"""manager · management family helpers (wrap ``sandbox-cli manager <op>``)."""

from core.cli import manager
from core.config import IMAGE, WORKSPACE_ROOT


def create_sandbox(image=IMAGE, workspace_root=WORKSPACE_ROOT):
    return manager(
        "create_sandbox", "--image", image, "--workspace-root", workspace_root
    )


def inspect_sandbox(sandbox_id):
    return manager("inspect_sandbox", "--sandbox-id", sandbox_id)


def list_sandboxes():
    return manager("list_sandboxes")


def destroy_sandbox(sandbox_id):
    return manager("destroy_sandbox", "--sandbox-id", sandbox_id)


def get_observability_tree(sandbox_id=None):
    """Manager-side observability aggregate (untested this round; see
    observability/README.md)."""
    if sandbox_id:
        return manager("get_observability_tree", "--sandbox-id", sandbox_id)
    return manager("get_observability_tree")
