"""Shared assertions for focused mocked-agent integration scenarios."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from task_center_runner.audit.events import EventType
from task_center_runner.core.runner import RunReport
from task_center_runner.scenarios.base import Scenario


@dataclass(frozen=True, slots=True)
class FocusedScenarioCase:
    name: str
    expected_status: str = "done"
    min_event_counts: Mapping[EventType, int] = field(default_factory=dict)
    absent_events: Sequence[EventType] = ()
    goal_status: str = "succeeded"
    iteration_count: int | None = 1
    attempt_count: int | None = None


def assert_focused_scenario_report(
    report: RunReport,
    scenario: Scenario,
    case: FocusedScenarioCase,
) -> None:
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
    assert goal["status"] == case.goal_status
    if case.iteration_count is not None:
        assert len(goal["iterations"]) == case.iteration_count
    if case.attempt_count is not None:
        attempts = [
            attempt
            for iteration in goal["iterations"]
            for attempt in iteration["attempts"]
        ]
        assert len(attempts) == case.attempt_count
