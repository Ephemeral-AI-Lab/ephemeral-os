"""Live regressions for the focused scenario reference suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from task_center_runner.benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.audit.events import EventType
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.environments.sweevo_image.fixtures import run_scenario_on_sweevo_image
from task_center_runner.tests._live_config import database_configured
from task_center_runner.tests.mock._focused_scenario_contracts import (
    FocusedScenarioCase,
    assert_focused_scenario_report,
)

pytestmark = pytest.mark.asyncio


_FOCUSED_CASES: tuple[FocusedScenarioCase, ...] = (
    FocusedScenarioCase(
        "pipeline.initial_workflow",
        min_role_tasks={"executor": 1},
        min_done_role_tasks={"executor": 1, "reducer": 1},
        attempt_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.iterative_deferral",
        min_done_role_tasks={"planner": 1, "executor": 2, "reducer": 2},
        min_deferred_attempts=1,
        iteration_count=2,
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_reducer_failure",
        min_done_role_tasks={"planner": 2, "executor": 2, "reducer": 1},
        min_failed_role_tasks={"reducer": 1},
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_planner_failure",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 1,
        },
        min_role_tasks={"planner": 2},
        min_done_role_tasks={"planner": 1, "executor": 1, "reducer": 1},
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_generator_failure",
        min_done_role_tasks={"planner": 2, "executor": 1, "reducer": 1},
        min_failed_role_tasks={"executor": 1},
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_serial",
        min_role_tasks={"executor": 3},
        min_done_role_tasks={"executor": 3},
        attempt_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_mixed",
        min_role_tasks={"executor": 7},
        min_done_role_tasks={"executor": 7},
        attempt_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_parallel",
        min_role_tasks={"executor": 4},
        min_done_role_tasks={"executor": 4},
        attempt_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_diamond",
        min_role_tasks={"executor": 4},
        min_done_role_tasks={"executor": 4},
        attempt_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.generator_failure_quiescence",
        min_role_tasks={"executor": 7},
        min_done_role_tasks={"planner": 2, "executor": 6, "reducer": 1},
        min_failed_role_tasks={"executor": 1},
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_blocked_descendants",
        expected_status="failed",
        min_role_tasks={"executor": 2},
        min_done_role_tasks={"planner": 2},
        min_failed_role_tasks={"executor": 2},
        # The reducer task row is created PENDING at plan-apply but never
        # reaches DONE: the blocked root fails the attempt before its needs
        # are satisfied.
        absent_done_role_tasks=("reducer",),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_budget_exhausted",
        expected_status="failed",
        min_done_role_tasks={"planner": 2},
        min_failed_role_tasks={"executor": 2},
        # The reducer never becomes ready (its only generator always fails),
        # so no reducer task ever reaches DONE.
        absent_done_role_tasks=("reducer",),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.duplicate_local_id",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.unknown_dep",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.cycle_in_deps",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.defers_without_deferred_goal",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        max_deferred_attempts=0,
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.unknown_agent_name",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        workflow_status="failed",
        attempt_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.empty_tasks",
        expected_status="failed",
        min_event_counts={
            EventType.TOOL_CALL_ERROR: 2,
        },
        min_role_tasks={"planner": 2},
        absent_done_role_tasks=("planner",),
        absent_role_tasks=("executor", "reducer"),
        workflow_status="failed",
        attempt_count=2,
    ),
)


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.parametrize("case", _FOCUSED_CASES, ids=[case.name for case in _FOCUSED_CASES])
async def test_focused_reference_scenario_runs(
    case: FocusedScenarioCase,
    sweevo_image_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY[case.name]()
    report = await run_scenario_on_sweevo_image(
        scenario,
        instance=sweevo_image_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    assert_focused_scenario_report(report, case)
