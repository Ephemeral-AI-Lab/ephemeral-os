"""Static guard against removed TaskCenter lifecycle/context surfaces."""

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
    "generator_dag",
    "GeneratorDagSummary",
    "evaluator_task_id",
    "submit_execution_success",
    "submit_execution_blocker",
    "SUBMIT_EXECUTION_SUCCESS_TOOL_NAME",
    "SUBMIT_EXECUTION_BLOCKER_TOOL_NAME",
    "class Outcome",
    "ContextPacket",
    "ContextOutline",
    "TagDictionary",
    "recipes_registry",
    # Unified submission terminal migration removals.
    "TerminalToolRouter",
    "terminal_routing",
    "select_terminals",
    "tools.submission.executor",
    "ExecutorSubmissionContext",
    "submit_plan_closes_goal",
    "submit_plan_defers_goal",
    "submit_generator_success",
    "submit_generator_failure",
    "submit_reduction_success",
    "submit_reduction_failure",
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
