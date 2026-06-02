"""Offline regressions for scenario dispatchers that now read instruction.

These assertions pin the recent ScenarioContext rename at the scenario seam:
executor/verifier helpers must consult ``ctx.instruction`` directly rather
than the removed ``ctx.rendered_prompt`` field.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_runner.scenarios.base import ScenarioContext
from test_runner.scenarios.full_case_user_input import FullCaseUserInput
from test_runner.scenarios.full_stack_adversarial import (
    FullStackAdversarial,
)
from test_runner.scenarios.pipeline.generator_failure_quiescence import (
    GeneratorFailureQuiescence,
)
from test_runner.scenarios.pipeline.nested_workflow import (
    NestedWorkflow,
    NestedWorkflowFailure,
)
from test_runner.scenarios.sandbox.high_concurrency_layerstack_overlay_occ import (
    HighConcurrencyLayerstackOverlayOcc,
    MAX_CONCURRENT_WORKERS,
    WORKER_COUNT,
)


def _ctx(
    *,
    instruction: str,
    prompt: str = "",
    attempt_no: int = 1,
    parent_task_id: str = "parent-task-id",
) -> ScenarioContext:
    return ScenarioContext(
        attempt=SimpleNamespace(
            attempt_sequence_no=attempt_no,
            id=f"attempt-{attempt_no}",
        ),
        iteration=SimpleNamespace(sequence_no=1, workflow_id="workflow-id"),
        workflow=SimpleNamespace(parent_task_id=parent_task_id),
        prompt=prompt,
        metadata={},
        audit_recorder=None,
        task_id="task-id",
        agent_name="executor",
        instruction=instruction,
    )


def test_full_case_executor_actions_use_instruction() -> None:
    scenario = FullCaseUserInput()
    ctx = _ctx(
        instruction="ACTION delegate_workflow package=pkg_42",
        prompt="this prompt should be ignored",
    )

    assert scenario.executor_actions(ctx) == ("delegate_workflow:pkg_42",)


def test_full_stack_executor_actions_use_instruction() -> None:
    scenario = FullStackAdversarial()
    ctx = _ctx(
        instruction="ACTION delegate_workflow_matrix package=matrix_pkg",
        prompt="fallback prompt",
    )

    assert scenario.executor_actions(ctx) == (
        "delegate_workflow_matrix:matrix_pkg",
    )


def test_nested_workflow_dispatch_uses_instruction() -> None:
    success = NestedWorkflow()
    failure = NestedWorkflowFailure()

    assert success.executor_actions(
        _ctx(instruction="ACTION delegate_workflow package=child_success")
    ) == ("delegate_workflow:child_success",)
    assert failure.executor_actions(
        _ctx(instruction="ACTION child_failure reason=nested_workflow")
    ) == ("fail:Intentional delegated workflow failure.",)


def test_generator_failure_quiescence_uses_instruction_on_attempt_one() -> None:
    scenario = GeneratorFailureQuiescence()
    ctx = _ctx(
        instruction="Run preflight ACTION fail_on_attempt=1 tag=quiescence_b",
        attempt_no=1,
    )

    assert scenario.executor_actions(ctx) == (
        "fail:Intentional generator failure on attempt 1 (quiescence_b).",
    )


def test_high_concurrency_dispatch_uses_instruction_index() -> None:
    scenario = HighConcurrencyLayerstackOverlayOcc()

    assert scenario.executor_actions(
        _ctx(instruction="ACTION high_concurrency_seed")
    ) == ("high_concurrency_seed",)
    assert scenario.executor_actions(
        _ctx(instruction="ACTION high_concurrency_worker index=07")
    ) == ("high_concurrency_worker:7",)
    assert scenario.executor_actions(
        _ctx(instruction="ACTION high_concurrency_reconcile")
    ) == ("high_concurrency_reconcile",)


def test_high_concurrency_plan_honors_configured_worker_overlap() -> None:
    scenario = HighConcurrencyLayerstackOverlayOcc()

    plan = scenario.planner_response(_ctx(instruction="")).args
    needs_by_id = {
        str(task["id"]): tuple(task.get("needs") or ()) for task in plan["tasks"]
    }

    for index in range(WORKER_COUNT):
        worker_id = f"concurrent_worker_{index:02d}"
        if index < MAX_CONCURRENT_WORKERS:
            assert needs_by_id[worker_id] == ("concurrency_seed",)
        else:
            assert needs_by_id[worker_id] == (
                f"concurrent_worker_{index - MAX_CONCURRENT_WORKERS:02d}",
            )
    assert needs_by_id["concurrency_reconcile"] == tuple(
        f"concurrent_worker_{index:02d}" for index in range(WORKER_COUNT)
    )
    assert any(
        f"{MAX_CONCURRENT_WORKERS} active sandbox tool calls" in reducer["prompt"]
        for reducer in plan["reducers"]
    )
