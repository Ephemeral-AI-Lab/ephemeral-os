"""Offline regressions for scenario dispatchers that now read context_message.

These assertions pin the recent ScenarioContext rename at the scenario seam:
executor/verifier helpers must consult ``ctx.context_message`` directly rather
than the removed ``ctx.rendered_prompt`` field.
"""

from __future__ import annotations

from types import SimpleNamespace

from task_center_runner.scenarios.base import ScenarioContext
from task_center_runner.scenarios.full_case_user_input import FullCaseUserInput
from task_center_runner.scenarios.full_stack_adversarial import (
    FullStackAdversarial,
)
from task_center_runner.scenarios.pipeline.generator_failure_quiescence import (
    GeneratorFailureQuiescence,
)
from task_center_runner.scenarios.pipeline.nested_goal import (
    NestedGoal,
    NestedGoalFailure,
)
from task_center_runner.scenarios.sandbox.high_concurrency_layerstack_overlay_occ import (
    HighConcurrencyLayerstackOverlayOcc,
)


def _ctx(
    *,
    context_message: str,
    prompt: str = "",
    attempt_no: int = 1,
    requested_by_task_id: str = "parent-task-id",
) -> ScenarioContext:
    return ScenarioContext(
        attempt=SimpleNamespace(
            attempt_sequence_no=attempt_no,
            evaluation_criteria=("criterion",),
            id=f"attempt-{attempt_no}",
        ),
        iteration=SimpleNamespace(sequence_no=1, goal_id="goal-id"),
        goal=SimpleNamespace(requested_by_task_id=requested_by_task_id),
        prompt=prompt,
        metadata={},
        audit_recorder=None,
        mutable_state=None,
        task_id="task-id",
        agent_name="executor",
        context_message=context_message,
    )


def test_full_case_executor_actions_use_context_message() -> None:
    scenario = FullCaseUserInput()
    ctx = _ctx(
        context_message="ACTION request_recursive_goal package=pkg_42",
        prompt="this prompt should be ignored",
    )

    assert scenario.executor_actions(ctx) == ("request_recursive_goal:pkg_42",)


def test_full_case_verifier_response_reads_checkpoint_from_context_message() -> None:
    scenario = FullCaseUserInput()
    ctx = _ctx(
        context_message="VERIFY checkpoint=recursive_return dependency_count=3",
    )

    result = scenario.verifier_response(ctx)

    assert result.tool.name == "submit_verification_success"
    assert result.args["checks"] == [
        "checkpoint:recursive_return",
        "dependencies:3",
    ]


def test_full_stack_executor_actions_use_context_message() -> None:
    scenario = FullStackAdversarial()
    ctx = _ctx(
        context_message="ACTION request_recursive_matrix package=matrix_pkg",
        prompt="fallback prompt",
    )

    assert scenario.executor_actions(ctx) == (
        "request_recursive_matrix:matrix_pkg",
    )


def test_nested_goal_dispatch_uses_context_message() -> None:
    success = NestedGoal()
    failure = NestedGoalFailure()

    assert success.executor_actions(
        _ctx(context_message="ACTION request_recursive_goal package=child_success")
    ) == ("request_recursive_goal:child_success",)
    assert failure.executor_actions(
        _ctx(context_message="ACTION child_failure reason=nested_goal")
    ) == ("fail:Intentional child goal failure.",)


def test_generator_failure_quiescence_uses_context_message_on_attempt_one() -> None:
    scenario = GeneratorFailureQuiescence()
    ctx = _ctx(
        context_message="Run preflight ACTION fail_on_attempt=1 tag=quiescence_b",
        attempt_no=1,
    )

    assert scenario.executor_actions(ctx) == (
        "fail:Intentional generator failure on attempt 1 (quiescence_b).",
    )


def test_high_concurrency_dispatch_uses_context_message_index() -> None:
    scenario = HighConcurrencyLayerstackOverlayOcc()

    assert scenario.executor_actions(
        _ctx(context_message="ACTION high_concurrency_seed")
    ) == ("high_concurrency_seed",)
    assert scenario.executor_actions(
        _ctx(context_message="ACTION high_concurrency_worker index=07")
    ) == ("high_concurrency_worker:7",)
    assert scenario.executor_actions(
        _ctx(context_message="ACTION high_concurrency_reconcile")
    ) == ("high_concurrency_reconcile",)
