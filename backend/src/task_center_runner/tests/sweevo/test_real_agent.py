"""Real-agent live-e2e smoke test for a canonical SWE-EVO instance.

Gated off by default. Set ``EOS_SWEEVO_REAL_AGENT_TESTS=1`` to run against
real LLM credentials + a real Daytona sandbox. The test depends on the
function-scoped ``workspace`` fixture (per-test reset) rather than
session-scoped ``sweevo_sandbox`` to avoid cross-instance state leakage when
the test grows to a parameterized matrix.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.real_agent_run import run_sweevo_real_agent
from task_center_runner.stores import TaskCenterStoreBundle

pytestmark = pytest.mark.skipif(
    os.getenv("EOS_SWEEVO_REAL_AGENT_TESTS") != "1",
    reason="Real-agent live e2e gated by EOS_SWEEVO_REAL_AGENT_TESTS=1",
)


@pytest.mark.asyncio
async def test_real_agent_resolves_canonical_instance(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    max_duration_s = float(os.getenv("EOS_SWEEVO_REAL_AGENT_MAX_DURATION_S", "1800"))
    report = await run_sweevo_real_agent(
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
        max_duration_s=max_duration_s,
    )
    assert report.task_center_run_id
    assert report.run_dir.is_dir()
    assert (report.run_dir / "run.json").is_file()
    assert (report.run_dir / "sweevo_result.json").is_file()
    assert report.task_center_status in {"done", "failed", "cancelled"}
    if report.task_center_status == "done" and not report.aborted_by_timeout:
        assert report.sweevo_result.fail_to_pass_total > 0
