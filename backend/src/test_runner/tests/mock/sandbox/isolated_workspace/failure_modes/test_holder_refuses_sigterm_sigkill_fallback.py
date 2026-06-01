"""SIGSTOPped holder ignores SIGTERM; exit() escalates to SIGKILL after grace.

Host-side ``kill -STOP`` on the holder PID prevents it from handling
SIGTERM. The exit() path's ``kill_holder`` waits only the configured short
``grace_s`` window, then sends SIGKILL — netns/mntns/pidns are reaped along
with the kernel-killed PID.
"""

from __future__ import annotations

import time

import pytest

from sandbox.api import raw_exec
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
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
@pytest.mark.timeout(360)
async def test_holder_refuses_sigterm_sigkill_fallback(
    iws_clean_sandbox, iws_audit_jsonl
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    enter = await _iws_rpc.enter(
        sandbox_id, "agent-A", layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert enter.get("success") is True, enter

    # Find the holder PID via /proc and SIGSTOP it host-side. ``pgrep -f``
    # with the bare pattern matches the calling shell too (its cmdline
    # contains the literal pattern), and SIGSTOPping the shell deadlocks
    # the docker-exec channel. Filter by ``comm`` to only stop the
    # ``unshare`` parent + the ``python`` grandchild — both have ns_holder
    # in their cmdline; the shell does not match either prefix.
    await raw_exec(
        sandbox_id,
        "pgrep -lf 'sandbox\\.isolated_workspace\\.scripts\\.ns_holder' "
        "| awk '$2 == \"unshare\" || $2 ~ /^python/ {print $1}' "
        "| xargs -r kill -STOP 2>/dev/null || true",
        cwd="/", timeout=10,
    )

    t0 = time.monotonic()
    exit_resp = await _iws_rpc.exit_(sandbox_id, "agent-A", timeout=30)
    elapsed = time.monotonic() - t0
    assert exit_resp.get("success") is True, exit_resp
    kill_holder_ms = float(
        (exit_resp.get("phases_ms") or {}).get("kill_holder") or 0.0
    )
    assert kill_holder_ms >= 150.0, (
        "exit must wait briefly before SIGKILL fallback",
        kill_holder_ms,
        exit_resp,
    )
    assert elapsed < 2.0, (
        "SIGKILL fallback should not pay the old 5s holder grace",
        elapsed,
        exit_resp,
    )

    jsonl = await iws_audit_jsonl()
    _iws_invariants.assert_audit_sequence(
        jsonl,
        [
            "sandbox_isolated_workspace_enter",
            "sandbox_isolated_workspace_exit",
        ],
    )
