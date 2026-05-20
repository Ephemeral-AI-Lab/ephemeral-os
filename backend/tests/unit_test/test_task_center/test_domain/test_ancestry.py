"""Unit tests for nested goal ancestry depth and terminal routing helpers."""

from __future__ import annotations

import pytest

from task_center._core.terminal_tool_routing import (
    ResolverContext,
    _nested_goal_depth_gt_1,
)
from task_center.attempt import AttemptStage
from task_center.context_engine.core import ContextEngineDeps
from task_center.context_engine.scope import ContextScope
from task_center.iteration.state import IterationCreationReason
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center.goal.ancestry import nested_goal_depth


def _stores(goal_store, iteration_store, attempt_store, task_store):
    return dict(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _seed_goal(
    goal_store,
    *,
    task_center_run_id: str,
    requested_by_task_id: str = "t-entry",
    goal: str = "g",
):
    return goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id=requested_by_task_id,
        goal=goal,
    )


def _seed_iteration(iteration_store, *, goal_id: str, sequence_no: int = 1):
    return iteration_store.insert(
        goal_id=goal_id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        attempt_budget=2,
    )


def _seed_attempt(
    attempt_store,
    *,
    iteration_id: str,
    sequence_no: int = 1,
):
    attempt = attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="spec",
        evaluation_criteria=["c1"],
        deferred_goal_for_next_iteration=None,
    )
    attempt_store.set_stage(attempt.id, AttemptStage.GENERATE)
    return attempt


def _seed_task(
    task_store,
    *,
    task_id: str,
    task_center_run_id: str,
    attempt_id: str | None,
    role: str = "generator",
):
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role=role,
        agent_name=role,
        context_message="input",
        status="running",
        summaries=[],
        needs=[],
        task_center_attempt_id=attempt_id,
        spawn_reason="test_seed",
    )


def _seed_nested_goal_chain(
    goal_store,
    iteration_store,
    attempt_store,
    task_store,
    *,
    task_center_run_id: str,
    depth: int,
) -> list[str]:
    assert depth >= 1
    goal_ids: list[str] = []
    requested_by_task_id = "t-entry"
    for idx in range(depth):
        goal = _seed_goal(
            goal_store,
            task_center_run_id=task_center_run_id,
            requested_by_task_id=requested_by_task_id,
        )
        goal_ids.append(goal.id)
        if idx == depth - 1:
            break
        iteration = _seed_iteration(iteration_store, goal_id=goal.id)
        attempt = _seed_attempt(attempt_store, iteration_id=iteration.id)
        task_id = f"t-{idx}"
        _seed_task(
            task_store,
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            attempt_id=attempt.id,
        )
        requested_by_task_id = task_id
    return goal_ids


def test_no_parent_task_returns_depth_1(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    goal = _seed_goal(
        goal_store, task_center_run_id=task_center_run_id
    )
    assert (
        nested_goal_depth(
            goal_id=goal.id,
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )
        == 1
    )


def test_parent_task_with_no_attempt_returns_depth_1(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    goal = _seed_goal(
        goal_store,
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t-entry",
    )
    _seed_task(
        task_store,
        task_id="t-entry",
        task_center_run_id=task_center_run_id,
        attempt_id=None,
    )
    assert (
        nested_goal_depth(
            goal_id=goal.id,
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )
        == 1
    )


def test_child_goal_returns_depth_2(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    root_id, child_id = _seed_nested_goal_chain(
        goal_store,
        iteration_store,
        attempt_store,
        task_store,
        task_center_run_id=task_center_run_id,
        depth=2,
    )
    assert (
        nested_goal_depth(
            goal_id=root_id,
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )
        == 1
    )
    assert (
        nested_goal_depth(
            goal_id=child_id,
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )
        == 2
    )


def test_grandchild_goal_returns_depth_3(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    goal_ids = _seed_nested_goal_chain(
        goal_store,
        iteration_store,
        attempt_store,
        task_store,
        task_center_run_id=task_center_run_id,
        depth=4,
    )
    assert (
        nested_goal_depth(
            goal_id=goal_ids[-1],
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )
        == 4
    )


def test_unknown_goal_id_raises(
    goal_store, iteration_store, attempt_store, task_store
):
    with pytest.raises(TaskCenterInvariantViolation):
        nested_goal_depth(
            goal_id="nonexistent",
            **_stores(goal_store, iteration_store, attempt_store, task_store),
        )


def test_terminal_router_nested_depth_helper(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    deps = ContextEngineDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )

    top_level_ctx = ResolverContext(scope=ContextScope(), deps=deps)
    assert _nested_goal_depth_gt_1(top_level_ctx) is False

    goal_ids = _seed_nested_goal_chain(
        goal_store,
        iteration_store,
        attempt_store,
        task_store,
        task_center_run_id=task_center_run_id,
        depth=3,
    )
    child_ctx = ResolverContext(
        scope=ContextScope(goal_id=goal_ids[-1]),
        deps=deps,
    )

    assert _nested_goal_depth_gt_1(child_ctx) is True
