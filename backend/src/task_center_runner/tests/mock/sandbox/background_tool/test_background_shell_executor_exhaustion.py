"""T5 — Executor exhaustion live regression for ``shell(background=True)``.

Phase 2 plan §Step 6. Launch 80 background shells (above the
``DEFAULT_EXECUTOR_WORKERS = 64`` pool size; 80 saturates the queue
without saturating SWE-EVO quotas). Cancel all in parallel. Issue a
single foreground ``read_file`` and assert it completes in < 1 s (AC-14:
``ShellExecutor`` is distinct from the daemon's RPC dispatcher executor).
"""

from __future__ import annotations

import asyncio
import time

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

# Sized so we exceed DEFAULT_EXECUTOR_WORKERS (64) without saturating the
# SWE-EVO Docker quota. The plan §Step 6 picked 80 after the round-2
# 210-launch number proved infeasible for shared CI.
_LAUNCH_COUNT = 80
_BACKGROUND_SLEEP_S = 60
_CANCEL_DEADLINE_S = 2.0


async def _launch_then_cancel(sandbox_id: str, index: int) -> str:
    """Launch one background shell, immediately let asyncio.wait_for cancel.

    Wrapping ``sandbox_api.shell(... background=True)`` in
    ``asyncio.wait_for(..., timeout=_CANCEL_DEADLINE_S)`` is enough to
    drive the host-side cancel path because the underlying RPC awaits the
    daemon's reap. The host-side dispatcher catches the CancelledError
    and routes through ``_send_cancel_then_reap``.
    """
    request = ShellRequest(
        command=f"sleep {_BACKGROUND_SLEEP_S}; echo done-{index}",
        cwd=".",
        timeout=_BACKGROUND_SLEEP_S + 30,
        background=True,
        caller=SandboxCaller(agent_id=f"background-shell-exhaustion.{index}"),
        description=f"background_shell.exhaustion.{index}",
    )
    try:
        await asyncio.wait_for(
            sandbox_api.shell(sandbox_id, request),
            timeout=_CANCEL_DEADLINE_S,
        )
        return "ok"
    except asyncio.TimeoutError:
        return "cancelled"
    except Exception as exc:  # noqa: BLE001 — capture any failure mode
        return f"error:{type(exc).__name__}"


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(600)
async def test_background_shell_executor_exhaustion(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
) -> None:
    sandbox_id = str(workspace["sandbox_id"])
    await seed_workspace(sandbox_id)

    # Fire 80 launches in parallel; each cancels itself after 2 s.
    outcomes = await asyncio.gather(
        *(_launch_then_cancel(sandbox_id, i) for i in range(_LAUNCH_COUNT)),
        return_exceptions=False,
    )
    cancelled = sum(1 for o in outcomes if o == "cancelled")
    errored = sum(1 for o in outcomes if o.startswith("error:"))
    # Allow up to 5 % outright errors (SWE-EVO Docker quota or transient
    # RPC noise); the rest must be cancellations.
    assert errored <= max(1, _LAUNCH_COUNT // 20), outcomes
    assert cancelled >= _LAUNCH_COUNT - errored - 4, outcomes

    # AC-14: a follow-up foreground read_file must complete in < 1 s,
    # proving the daemon's RPC dispatcher executor is NOT the
    # ``ShellExecutor`` (Pre-mortem #3 invariant).
    read_request = ReadFileRequest(
        path="/testbed",
        caller=SandboxCaller(agent_id="background-shell-exhaustion.fg-probe"),
    )
    t0 = time.monotonic()
    read_result = await sandbox_api.read_file(sandbox_id, read_request)
    elapsed = time.monotonic() - t0
    assert read_result.success, read_result
    assert elapsed < 1.0, (
        f"AC-14 violation: post-exhaustion read_file took {elapsed:.3f}s; "
        f"expected < 1 s. Daemon RPC executor may be sharing the ShellExecutor."
    )
