"""T5 — Executor exhaustion live regression via the scenario harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sandbox.api as sandbox_api
from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from sandbox.api import ReadFileRequest, SandboxCaller
from test_runner.agent.mock.background_shell_probe import (
    EXHAUSTION_LAUNCH_COUNT,
    EXHAUSTION_SUMMARY,
)
from test_runner.core.stores import TaskStoreBundle
from test_runner.environments.sweevo_image.fixtures import (
    run_scenario_on_sweevo_image,
)
from test_runner.scenarios import SCENARIO_REGISTRY
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.background_tool._background_shell_invariants import (
    configure_default_inflight_ttl,
)


pytestmark = pytest.mark.asyncio


def _is_missing_after_cancel(record: dict[str, object]) -> bool:
    return (
        record.get("is_error") is True
        and record.get("status") == "error"
        and record.get("stderr") == "command_session_not_found"
    )


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(720)
async def test_background_shell_executor_exhaustion(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.background_shell_exhaustion"]
    sandbox_id = str(workspace["sandbox_id"])
    await configure_default_inflight_ttl(sandbox_id)
    report = await run_scenario_on_sweevo_image(
        scenario_cls(),
        instance=sweevo_image_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    assert report.request_status == "done", report

    read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=EXHAUSTION_SUMMARY,
            caller=SandboxCaller(
                agent_id="test.background_shell_exhaustion.read"
            ),
        ),
    )
    assert read.success and read.exists, read
    summary = json.loads(read.content or "{}")
    assert summary["mode"] == "exhaustion", summary
    # Allow a small number of launch/transport errors. A mass Ctrl-C can race
    # with session cleanup; command_session_not_found means the target is already
    # terminal and is counted separately from hard executor errors.
    missing_after_cancel = sum(
        1 for record in summary["cancellations"] if _is_missing_after_cancel(record)
    )
    launch_errors = sum(1 for record in summary["launches"] if record.get("is_error"))
    hard_cancel_errors = sum(
        1
        for record in summary["cancellations"]
        if record.get("is_error") and not _is_missing_after_cancel(record)
    )
    errored = launch_errors + hard_cancel_errors
    cancelled = int(summary["cancelled_count"])
    assert errored <= max(1, EXHAUSTION_LAUNCH_COUNT // 20), summary
    assert (
        cancelled + missing_after_cancel
        >= EXHAUSTION_LAUNCH_COUNT - launch_errors - 4
    ), summary

    # AC-14: post-exhaustion read_file must complete in < 1 s.
    assert not summary["post_exhaustion_read_error"], summary
    assert summary["post_exhaustion_read_s"] < 1.0, (
        f"AC-14 violation: post-exhaustion read_file took "
        f"{summary['post_exhaustion_read_s']:.3f}s (expected < 1 s)"
    )
