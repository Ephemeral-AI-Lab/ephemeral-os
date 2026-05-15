"""Live regressions for the focused scenario reference suite."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.audit.events import EventType
from task_center_runner.core.runner import RunReport
from task_center_runner.scenarios import SCENARIO_REGISTRY
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True, slots=True)
class FocusedScenarioCase:
    name: str
    expected_status: str = "done"
    min_event_counts: Mapping[EventType, int] = field(default_factory=dict)
    absent_events: Sequence[EventType] = ()
    mission_status: str = "succeeded"
    episode_count: int | None = 1
    trial_count: int | None = None


_FOCUSED_CASES: tuple[FocusedScenarioCase, ...] = (
    FocusedScenarioCase(
        "pipeline.initial_mission",
        min_event_counts={
            EventType.EXECUTOR_INVOKED: 1,
            EventType.EXECUTOR_SUCCESS: 1,
            EventType.EVALUATOR_SUCCESS: 1,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.episodic_continuation",
        min_event_counts={
            EventType.PLANNER_PARTIAL_PLAN: 1,
            EventType.PLANNER_FULL_PLAN: 1,
            EventType.EXECUTOR_SUCCESS: 2,
            EventType.EVALUATOR_SUCCESS: 2,
        },
        episode_count=2,
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_evaluator_failure",
        min_event_counts={
            EventType.PLANNER_FULL_PLAN: 2,
            EventType.EXECUTOR_SUCCESS: 2,
            EventType.EVALUATOR_FAILURE: 1,
            EventType.EVALUATOR_SUCCESS: 1,
        },
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_planner_failure",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.PLANNER_FULL_PLAN: 1,
            EventType.TOOL_CALL_ERROR: 1,
            EventType.EXECUTOR_SUCCESS: 1,
            EventType.EVALUATOR_SUCCESS: 1,
        },
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.attempt_retry_generator_failure",
        min_event_counts={
            EventType.PLANNER_FULL_PLAN: 2,
            EventType.EXECUTOR_FAILURE: 1,
            EventType.EXECUTOR_SUCCESS: 1,
            EventType.EVALUATOR_SUCCESS: 1,
        },
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_serial",
        min_event_counts={
            EventType.EXECUTOR_INVOKED: 3,
            EventType.EXECUTOR_SUCCESS: 3,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_mixed",
        min_event_counts={
            EventType.EXECUTOR_INVOKED: 7,
            EventType.EXECUTOR_SUCCESS: 7,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_parallel",
        min_event_counts={
            EventType.EXECUTOR_INVOKED: 4,
            EventType.EXECUTOR_SUCCESS: 4,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_dag_diamond",
        min_event_counts={
            EventType.EXECUTOR_INVOKED: 4,
            EventType.EXECUTOR_SUCCESS: 4,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "pipeline.generator_failure_quiescence",
        min_event_counts={
            EventType.PLANNER_FULL_PLAN: 2,
            EventType.EXECUTOR_INVOKED: 7,
            EventType.EXECUTOR_SUCCESS: 6,
            EventType.EXECUTOR_FAILURE: 1,
            EventType.EVALUATOR_SUCCESS: 1,
        },
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.dependency_blocked_descendants",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_FULL_PLAN: 2,
            EventType.EXECUTOR_INVOKED: 2,
            EventType.EXECUTOR_FAILURE: 2,
        },
        absent_events=(EventType.EVALUATOR_INVOKED,),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "pipeline.trial_budget_exhausted",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_FULL_PLAN: 2,
            EventType.EXECUTOR_FAILURE: 2,
        },
        absent_events=(EventType.EVALUATOR_INVOKED,),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "sandbox.occ_concurrent_conflicts",
        min_event_counts={
            EventType.SANDBOX_BATCH_EDIT_APPLIED: 1,
            EventType.SANDBOX_CONFLICT_DETECTED: 1,
            EventType.EXECUTOR_SUCCESS: 1,
        },
        trial_count=1,
    ),
    FocusedScenarioCase(
        "planner_validation.duplicate_local_id",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.unknown_dep",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.cycle_in_deps",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.partial_without_continuation_goal",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.PLANNER_PARTIAL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.unknown_agent_name",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
    FocusedScenarioCase(
        "planner_validation.empty_tasks",
        expected_status="failed",
        min_event_counts={
            EventType.PLANNER_INVOKED: 2,
            EventType.TOOL_CALL_ERROR: 2,
        },
        absent_events=(
            EventType.PLANNER_FULL_PLAN,
            EventType.EXECUTOR_INVOKED,
            EventType.EVALUATOR_INVOKED,
        ),
        mission_status="failed",
        trial_count=2,
    ),
)


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
@pytest.mark.parametrize("case", _FOCUSED_CASES, ids=[case.name for case in _FOCUSED_CASES])
async def test_focused_reference_scenario_runs(
    case: FocusedScenarioCase,
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY[case.name]()
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    assert report.task_center_status == case.expected_status, report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]
    assert (report.run_dir / "run.json").exists()
    assert (report.run_dir / "metrics.json").exists()
    _assert_ordered_subsequence(
        scenario.expected_event_sequence,
        report.seen_event_types,
    )
    _assert_event_counts(report, case)
    _assert_graph_shape(report, case)


def _assert_ordered_subsequence(
    expected: Sequence[EventType],
    actual: Sequence[EventType],
) -> None:
    position = 0
    for event_type in actual:
        if position < len(expected) and event_type == expected[position]:
            position += 1
    assert position == len(expected), (
        "expected_event_sequence was not observed in order: "
        f"expected={[event.value for event in expected]} "
        f"actual={[event.value for event in actual]}"
    )


def _assert_event_counts(report: RunReport, case: FocusedScenarioCase) -> None:
    counts = Counter(event.type for event in report.events)
    for event_type, minimum in case.min_event_counts.items():
        assert counts[event_type] >= minimum, (
            f"{case.name}: expected at least {minimum} {event_type.value} events, "
            f"saw {counts[event_type]}"
        )
    for event_type in case.absent_events:
        assert counts[event_type] == 0, (
            f"{case.name}: did not expect {event_type.value}, saw "
            f"{counts[event_type]}"
        )


def _assert_graph_shape(report: RunReport, case: FocusedScenarioCase) -> None:
    goals = report.graph_summary["goals"]
    assert len(goals) == 1, report.graph_summary
    goal = goals[0]
    assert goal["status"] == case.mission_status
    if case.episode_count is not None:
        assert len(goal["iterations"]) == case.episode_count
    if case.trial_count is not None:
        trials = [
            trial
            for iteration in goal["iterations"]
            for trial in iteration["trials"]
        ]
        assert len(trials) == case.trial_count
