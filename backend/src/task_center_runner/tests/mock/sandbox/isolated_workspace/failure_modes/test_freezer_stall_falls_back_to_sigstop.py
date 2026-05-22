"""R11: cgroup.freeze EACCES → per-PID SIGSTOP fallback + ``freezer_degraded``.

We chmod ``cgroup.freeze`` to ``000`` after enter; the next tool call's
freeze must fall back to walking ``cgroup.procs`` and SIGSTOPping each
PID. The handle's ``freezer_degraded`` flag flips to True (visible via
``status``).
"""

from __future__ import annotations

import pytest

from sandbox.api import raw_exec
from benchmarks.sweevo.models import _REPO_DIR
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace import _iws_rpc


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(240)
async def test_freezer_stall_falls_back_to_sigstop(iws_clean_sandbox) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    enter = await _iws_rpc.enter(
        sandbox_id, "agent-A", layer_stack_root=_REPO_DIR,
    )
    assert enter.get("success") is True, enter
    try:
        # Take away write permission on the freeze knob across all iws cgroups.
        await raw_exec(
            sandbox_id,
            "for f in /sys/fs/cgroup/eos-iws-*/cgroup.freeze; do "
            "chmod 000 \"$f\" 2>/dev/null || true; done",
            cwd="/", timeout=10,
        )

        # A tool call now exercises freeze → fallback path.
        result = await _iws_rpc.shell(sandbox_id, "agent-A", "true")
        assert result.get("success") is True, result

        status = await _iws_rpc.status(sandbox_id, "agent-A")
        assert status.get("freezer_degraded") is True, (
            "R11: freezer_degraded must be set after SIGSTOP fallback", status,
        )
    finally:
        # Restore permissions so cleanup paths can rmdir the cgroup.
        await raw_exec(
            sandbox_id,
            "for f in /sys/fs/cgroup/eos-iws-*/cgroup.freeze; do "
            "chmod 644 \"$f\" 2>/dev/null || true; done",
            cwd="/", timeout=10,
        )
        await _iws_rpc.exit_(sandbox_id, "agent-A")
