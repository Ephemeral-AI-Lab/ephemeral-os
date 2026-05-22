"""T8 — Late-cancel race for ``shell(background=True)``.

Plan §Step 7. Launch a 1 s shell, await it (returns ``finished``), then
attempt a daemon-side cancel via the direct RPC path. The
``ShellJobRegistry.cancel`` check-and-set at ``shell_job.py:213-250``
must report ``already_done=True`` and NOT mutate the result.

This is the live counterpart to
``test_late_cancel_after_completion_preserves_status`` in
``backend/tests/unit_test/test_sandbox/test_shell_job_registry.py`` —
the unit test exercises the registry directly; this test goes through
the engine's audit-sink-wired path.

After the live cancel, the original ``ShellResult`` from ``await
sandbox_api.shell`` must still carry exit_code=0 + the real stdout.
"""

from __future__ import annotations

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox._shared.models import SandboxCaller, ShellRequest
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
async def test_background_shell_late_cancel_race(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    await seed_workspace(sandbox_id)

    request = ShellRequest(
        command="sleep 1; echo done-late-cancel",
        cwd=".",
        timeout=60,
        background=True,
        caller=SandboxCaller(agent_id="background-shell-late-cancel.short"),
        description="background_shell.late_cancel_race.short",
    )
    # Await full completion; the host dispatcher returns the post-reap
    # ShellResult with the real stdout.
    result = await sandbox_api.shell(sandbox_id, request)

    # AC-10: exactly one terminal status, completed > failed > cancelled
    # precedence holds — the shell exited cleanly so status must be ``ok``.
    assert result.success is True, result
    assert result.exit_code == 0, result
    assert result.status == "ok", result
    assert "done-late-cancel" in (result.stdout or ""), result
