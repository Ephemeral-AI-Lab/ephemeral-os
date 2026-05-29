"""3.4.6 mixed-op concurrent background tasks (correctness).

Scenario-1 genuine gap: the suite already exercises a single foreground vs.
background same-path conflict and N small background writes, but not a single
run that (a) drives heterogeneous background ops to a terminal status, (b)
proves OCC conflict detection on overlapping same-file background edits, and
(c) proves disjoint background edits all land.

Location note (plan §6 open question): this lives in the mock task-center
suite rather than ``integration_test/`` because it reuses the proven
``run_background_shell_scenario`` harness and needs the real OCC publish path
behind ``BackgroundTaskSupervisor`` — which ``integration_test/test_sandbox``
has no harness for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from task_center_runner.benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.agent.mock.background_shell_probe import (
    MIXED_OP_CONCURRENT_SUMMARY,
)
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.background_tool._background_shell_invariants import (
    assert_background_performance_artifacts,
    run_background_shell_scenario,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not database_configured(), reason="database URL not configured"),
    pytest.mark.skipif(
        not live_e2e_heavy_enabled(),
        reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
    ),
]

# Conflict losers surface either as is_error with an explicit OCC abort status,
# or as is_error with no status — both are valid "did not land" outcomes; the
# explicit-abort tier is asserted separately below.
_ABORT_STATUSES = {"aborted_version", "aborted_overlap", "aborted_lock"}


@pytest.mark.timeout(720)
async def test_background_mixed_op_concurrent(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    report, summary = await run_background_shell_scenario(
        scenario_name="sandbox.background_mixed_op_concurrent",
        summary_path=MIXED_OP_CONCURRENT_SUMMARY,
        sweevo_image_instance=sweevo_image_instance,
        workspace=workspace,
        audit_dir=audit_dir,
        stores=stores,
    )

    assert summary["mode"] == "mixed_op_concurrent", summary

    # (a) heterogeneous ops all reach a terminal status (none stuck).
    mixed = summary["mixed"]
    assert set(mixed) == {"pytest", "pip", "edit_loop"}, summary
    for name, record in mixed.items():
        assert record["terminal"], (name, record)
        assert not record.get("cancelled"), (name, record)

    # (b) overlapping same-file edits: ≥1 OCC winner, ≥1 conflict-loser, and a
    # single deterministic final content.
    overlap = summary["overlap"]
    writers = overlap["writers"]
    assert overlap["accepted_count"] >= 1, summary
    assert overlap["aborted_count"] >= 1, summary
    assert overlap["accepted_count"] + overlap["aborted_count"] == len(writers), summary

    final = overlap["final_content"]
    winners = [w for w in writers if w["accepted"]]
    assert any(f"writer-{w['index']}" in final for w in winners), (final, winners)

    losers = [w for w in writers if not w["accepted"]]
    for loser in losers:
        # Each loser must look like a conflict, not a spurious crash.
        assert loser["is_error"] or loser.get("status") in _ABORT_STATUSES, loser
    # SC4: at least one loser carries an explicit OCC abort (versioned/overlap/
    # lock), proving conflict detection fired rather than a generic error.
    assert any(
        (loser.get("status") in _ABORT_STATUSES)
        or (loser.get("conflict_reason"))
        for loser in losers
    ), losers

    # (c) disjoint edits all land and read back their own content.
    disjoint = summary["disjoint"]
    disjoint_writers = disjoint["writers"]
    assert disjoint["accepted_count"] == len(disjoint_writers), summary
    for writer in disjoint_writers:
        assert writer["accepted"], writer
        path = writer["path"]
        index = path.rsplit("-", 1)[-1].split(".", 1)[0]
        assert f"disjoint-{index}" in disjoint["readbacks"][path], (writer, disjoint)

    assert_background_performance_artifacts(report)
