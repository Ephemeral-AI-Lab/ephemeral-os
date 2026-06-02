"""3.4.6 mixed-op concurrent PTY background tasks (correctness).

Scenario-1 genuine gap: the suite already exercises a single foreground vs.
background same-path conflict and N small background writes, but not a single
run that (a) drives heterogeneous background ops to a terminal status, (b)
proves overlapping same-file PTY writes converge to one complete payload, and
(c) proves disjoint background edits all land.

Location note (plan §6 open question): this lives in the mock test-runner
suite rather than ``integration_test/`` because it reuses the proven
``run_background_shell_scenario`` harness and needs the real typed-PTY publish
path, which ``integration_test/test_sandbox`` has no harness for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.benchmarks.sweevo.models import SWEEvoInstance
from test_runner.agent.mock.background_shell_probe import (
    MIXED_OP_CONCURRENT_SUMMARY,
    MIXED_OP_DISJOINT_WRITERS,
    MIXED_OP_OVERLAP_WRITERS,
)
from test_runner.core.stores import TaskStoreBundle
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.background_tool._background_shell_invariants import (
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

@pytest.mark.timeout(720)
async def test_background_mixed_op_concurrent(
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskStoreBundle,
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

    # (b) overlapping same-file edits: all PTY commands reach terminal success,
    # and the final content is one complete writer payload.
    overlap = summary["overlap"]
    writers = overlap["writers"]
    assert len(writers) == MIXED_OP_OVERLAP_WRITERS, summary
    assert overlap["accepted_count"] == MIXED_OP_OVERLAP_WRITERS, summary
    assert overlap["aborted_count"] == 0, summary
    assert overlap["accepted_count"] + overlap["aborted_count"] == len(writers), summary

    final = overlap["final_content"]
    assert any(f"writer-{w['index']}" in final for w in writers), (final, writers)
    for writer in writers:
        assert writer["accepted"], writer
        assert not writer["is_error"], writer

    # (c) disjoint edits all land and read back their own content.
    disjoint = summary["disjoint"]
    disjoint_writers = disjoint["writers"]
    assert len(disjoint_writers) == MIXED_OP_DISJOINT_WRITERS, summary
    assert disjoint["accepted_count"] == len(disjoint_writers), summary
    for writer in disjoint_writers:
        assert writer["accepted"], writer
        path = writer["path"]
        index = path.rsplit("-", 1)[-1].split(".", 1)[0]
        assert f"disjoint-{index}" in disjoint["readbacks"][path], (writer, disjoint)

    assert_background_performance_artifacts(report)
