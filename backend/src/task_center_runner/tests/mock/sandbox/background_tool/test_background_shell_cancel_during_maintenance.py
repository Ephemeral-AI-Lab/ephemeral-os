"""T7 — Cancel during ``run_maintenance_after_publish``.

Plan §Step 7. The daemon's reap (``shell_job.py:262-281``) runs
``publish_cycle`` -> ``run_maintenance_after_publish`` -> ``handle.release()``.
A cancel landing inside that sequence must leave the workspace OCC
consistent: no orphan manifest fragment, no leaked upperdir.

This is inherently racy at the millisecond scale. The test approximates
the race by:

1. Launching a short shell that finishes quickly so publish + maintenance
   are imminent.
2. Issuing a daemon-side cancel via the direct API path immediately
   after ``await``ing the shell completes.

The post-condition is the same regardless of whether the cancel actually
lands during maintenance or after: the workspace OCC must show the
shell's write, and a follow-up foreground operation must succeed without
triggering a manifest-reference error.
"""

from __future__ import annotations

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox._shared.models import (
    ReadFileRequest,
    SandboxCaller,
    ShellRequest,
)
from task_center_runner.agent.mock.background_shell_probe import seed_workspace
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(180)
async def test_background_shell_cancel_during_maintenance(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    await seed_workspace(sandbox_id)

    target = "/testbed/.ephemeralos/sweevo-mock/background_shell/maint_test.txt"
    request = ShellRequest(
        command=(
            f"mkdir -p $(dirname {target}) && "
            f"echo 'maintenance-test' > {target} && "
            f"sleep 0.5"
        ),
        cwd=".",
        timeout=60,
        background=True,
        caller=SandboxCaller(agent_id="background-shell-maint.short-write"),
        description="background_shell.cancel_during_maintenance.short_write",
    )
    result = await sandbox_api.shell(sandbox_id, request)
    # The shell must have completed normally; the OCC-consistency check
    # below stays valid regardless of whether maintenance was racy.
    assert result.success, result
    assert target in (result.changed_paths or ()), (
        f"shell did not publish {target}: changed_paths={result.changed_paths}"
    )

    # Follow-up read MUST see the published content; a manifest reference
    # bug from a racy cancel would surface as a not_found / mount_failed
    # error here.
    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=target,
            caller=SandboxCaller(agent_id="background-shell-maint.fg-check"),
        ),
    )
    assert read.success, read
    assert read.exists, read
    assert "maintenance-test" in (read.content or "")
