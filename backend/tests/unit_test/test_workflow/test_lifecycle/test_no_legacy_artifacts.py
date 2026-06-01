"""Static guard against removed pre-workflow lifecycle/context surfaces."""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCAN_ROOTS = (
    _REPO_ROOT / "backend" / "src" / "task",
    _REPO_ROOT / "backend" / "src" / "workflow",
    _REPO_ROOT / "backend" / "src" / "runtime",
    _REPO_ROOT / "backend" / "src" / "tools" / "workflow",
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
    # Pre-workflow refactor removals.
    "TaskStatus." "WAITING_" "WORKFLOW",
    "waiting_" "workflow",
    "submit_" "workflow_handoff",
    "Planned" "GeneratorTask",
    "Planned" "ReducerTask",
    "Planner" "TaskOutcome",
    "Planned" "TaskRef",
    "Workflow" "Origin",
    "apply_child_" "workflow_outcome",
    "start_child_" "workflow",
    "run_close_" "handler",
    "on_root_" "workflow_closed",
    "child_" "workflow_id",
    "root_" "workflow",
    "child_" "workflow",
)


def test_removed_workflow_refactor_surfaces_stay_removed() -> None:
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            for token in _REMOVED_TOKENS:
                if token in text:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {token}")

    assert offenders == []
