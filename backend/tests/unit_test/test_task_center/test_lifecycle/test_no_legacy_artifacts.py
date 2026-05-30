"""Static guard against removed TaskCenter submission-era surfaces."""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCAN_ROOTS = (
    _REPO_ROOT / "backend" / "src" / "task_center",
    _REPO_ROOT / "backend" / "src" / "tools" / "submission",
)
_REMOVED_TOKENS = (
    "submit_task_plan",
    "declare_blocker",
    "DeclareBlockerTool",
    "conductor",
    "submit_request_plan",
    "RETRY_ON_FAILURE",
    "retry_after_partial",
    # Reducers + unified-outcomes redesign removals.
    "WorkflowClosureReport",
    "closure_report_router",
    "apply_workflow_closure_report",
    "apply_evaluator_submission",
    "set_plan_contract",
    "TaskOutcome",
    "generator_dag",
    "GeneratorDagSummary",
    "evaluator_task_id",
)


def test_removed_task_center_submission_surfaces_stay_removed() -> None:
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            for token in _REMOVED_TOKENS:
                if token in text:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {token}")

    assert offenders == []
