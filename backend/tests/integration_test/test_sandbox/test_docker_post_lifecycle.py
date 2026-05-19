"""Docker provider full setup_post_lifecycle integration test.

Gated by EOS_HAVE_DOCKER=1, EOS_SANDBOX_PROVIDER=docker, AND a Linux host.
Auto-skips on darwin / when docker is not available, so this file is a no-op
on developer laptops but runs in Linux CI.

Verifies the full create→ensure_runtime_uploaded→ensure_git→ensure_workspace_base
chain against a live docker daemon. See PLAN_v4 §5.2 / §7.3.
"""

from __future__ import annotations

import os
import sys

import pytest

_DOCKER_GATE = pytest.mark.skipif(
    not (
        sys.platform.startswith("linux")
        and os.environ.get("EOS_HAVE_DOCKER") == "1"
        and os.environ.get("EOS_SANDBOX_PROVIDER") == "docker"
    ),
    reason=(
        "Requires Linux + EOS_HAVE_DOCKER=1 + EOS_SANDBOX_PROVIDER=docker "
        "(PLAN_v4 §5.2)."
    ),
)


@_DOCKER_GATE
@pytest.mark.asyncio
async def test_docker_post_lifecycle_end_to_end() -> None:
    """Full setup_post_lifecycle against a real local docker container."""
    from sandbox.host import lifecycle as host_lifecycle
    from sandbox.provider.bootstrap import bootstrap_sandbox_provider

    bootstrap_sandbox_provider()

    image = os.environ.get("EOS_DOCKER_TEST_IMAGE")
    assert image, "set EOS_DOCKER_TEST_IMAGE to a sweevo-compatible image"

    sandbox = host_lifecycle.create_sandbox(
        name="post-lifecycle-test",
        snapshot=None,
        image=image,
        language="python",
        labels={"project_dir": "/repo"},
    )
    sandbox_id = sandbox["id"]
    try:
        result = await host_lifecycle.setup_post_lifecycle(
            sandbox_id, mode="create"
        )
        assert result.get("ready") is True, result
    finally:
        host_lifecycle.delete_sandbox(sandbox_id)
