from __future__ import annotations

from code_intelligence.editing.edit_history_ledger import EditHistoryLedger


def test_contention_hotspots_without_scope_prefixes_scans_all_records() -> None:
    ledger = EditHistoryLedger()
    ledger.record(
        team_run_id="team-a",
        file_path="src/shared.py",
        agent_run_id="run-1",
        task_id="task-1",
    )
    ledger.record(
        team_run_id="team-a",
        file_path="src/shared.py",
        agent_run_id="run-2",
        task_id="task-2",
    )

    hotspots = ledger.contention_hotspots(scope_prefixes=None, limit=5)

    assert len(hotspots) == 1
    assert hotspots[0].file_path == "src/shared.py"
    assert hotspots[0].contributor_count == 2
    assert hotspots[0].edit_count == 2


def test_contention_hotspots_filters_by_team_run_id() -> None:
    ledger = EditHistoryLedger()
    ledger.record(
        team_run_id="team-a",
        file_path="src/shared.py",
        agent_run_id="run-1",
        task_id="task-1",
    )
    ledger.record(
        team_run_id="team-a",
        file_path="src/shared.py",
        agent_run_id="run-2",
        task_id="task-2",
    )
    ledger.record(
        team_run_id="team-b",
        file_path="src/shared.py",
        agent_run_id="run-3",
        task_id="task-3",
    )
    ledger.record(
        team_run_id="team-b",
        file_path="src/shared.py",
        agent_run_id="run-4",
        task_id="task-4",
    )

    hotspots = ledger.contention_hotspots(
        scope_prefixes=None,
        limit=5,
        team_run_id="team-a",
    )

    assert len(hotspots) == 1
    assert hotspots[0].file_path == "src/shared.py"
    assert hotspots[0].contributor_count == 2
    assert hotspots[0].edit_count == 2
