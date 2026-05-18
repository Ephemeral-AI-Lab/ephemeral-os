"""US-010: generator, evaluator, entry_executor happy-path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.entry_executor import _entry_executor_build
from task_center.context_engine.recipes.evaluator import _evaluator_build
from task_center.context_engine.recipes.generator import _generator_build
from task_center.context_engine.scope import ContextScope
from task_center.iteration.state import IterationCreationReason


@pytest.fixture
def deps(
    goal_store, iteration_store, attempt_store, task_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _seed_goal(goal_store, task_center_run_id):
    return goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t-entry",
        goal="overall",
    )


def _seed_iteration(iteration_store, *, goal_id):
    return iteration_store.insert(
        goal_id=goal_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        attempt_budget=2,
    )


def _seed_continuation_iteration(iteration_store, *, goal_id):
    return iteration_store.insert(
        goal_id=goal_id,
        sequence_no=2,
        creation_reason=IterationCreationReason.PARTIAL_CONTINUATION,
        goal="g2",
        attempt_budget=2,
    )


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------


def test_generator_emits_planned_task_spec_required_block(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="attempt spec framing",
        evaluation_criteria=["c1"],
        next_iteration_handoff_goal=None,
    )
    task_id = "t-1"
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="do thing X",
        status="pending",
        summaries=[],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )
    packet = _generator_build(
        ContextScope(
            goal_id=req.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
        deps,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["task_specification", "planned_task_spec"]
    assert packet.blocks[-1].kind == "planned_task_spec"
    assert packet.blocks[-1].priority == ContextPriority.REQUIRED
    assert packet.blocks[-1].text == "do thing X"
    assert "task_specification" in kinds


def test_generator_emits_nested_attempt_plan_with_handoff_goal_child(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """Continues-goal attempt produces two TASK_SPECIFICATION children
    (``<plan_spec>`` + ``<next_iteration_handoff_goal>``) under the same
    ``<attempt_plan>`` group — no PARTIAL_PLAN_BOUNDARY block."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="attempt spec framing",
        evaluation_criteria=["c1"],
        next_iteration_handoff_goal="future iteration work",
    )
    task_store.upsert_task(
        task_id="t-1",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="do thing X",
        status="pending",
        summaries=[],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    packet = _generator_build(
        ContextScope(
            goal_id=req.id,
            attempt_id=attempt.id,
            task_id="t-1",
        ),
        deps,
    )

    kinds = [b.kind for b in packet.blocks]
    assert kinds == [
        "task_specification",
        "task_specification",
        "planned_task_spec",
    ]
    plan_spec_block, handoff_block = packet.blocks[0], packet.blocks[1]
    assert plan_spec_block.metadata["child_tag"] == "plan_spec"
    assert handoff_block.metadata["child_tag"] == "next_iteration_handoff_goal"
    assert handoff_block.metadata["is_partial"] == "true"
    assert plan_spec_block.metadata["group_id"] == handoff_block.metadata["group_id"]
    assert plan_spec_block.metadata["group_tag"] == "attempt_plan"
    assert handoff_block.text == "future iteration work"


def test_generator_dependency_summary_blocks(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    # Upstream task with a recorded summary.
    task_store.upsert_task(
        task_id="t-up",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="parent",
        status="done",
        summaries=[{"outcome": "success", "summary": "produced X"}],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )
    task_store.upsert_task(
        task_id="t-down",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="downstream",
        status="pending",
        summaries=[],
        needs=["t-up"],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    packet = _generator_build(
        ContextScope(
            goal_id=req.id, attempt_id=attempt.id, task_id="t-down"
        ),
        deps,
    )
    dep_blocks = [b for b in packet.blocks if b.kind == "dependency_summary"]
    assert len(dep_blocks) == 1
    assert dep_blocks[0].metadata["child_tag"] == "dependency"
    assert dep_blocks[0].metadata["group_tag"] == "dependency_results"
    assert dep_blocks[0].metadata["attrs"] == 'id="t-up"'
    assert "produced X" in dep_blocks[0].text
    assert packet.blocks[-1].kind == "planned_task_spec"
    kinds = [b.kind for b in packet.blocks]
    dep_idx = kinds.index("dependency_summary")
    spec_idx = kinds.index("planned_task_spec")
    assert dep_idx < spec_idx


def test_generator_missing_dependency_task_raises_context_error(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    task_store.upsert_task(
        task_id="t-down",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="downstream",
        status="pending",
        summaries=[],
        needs=["t-missing"],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    with pytest.raises(ContextEngineError, match="Dependency task 't-missing'"):
        _generator_build(
            ContextScope(
                goal_id=req.id,
                attempt_id=attempt.id,
                task_id="t-down",
            ),
            deps,
        )


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------


def test_evaluator_emits_required_spec_and_criteria(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=["c1", "c2"],
        next_iteration_handoff_goal=None,
    )
    attempt_store.set_generator_task_ids(attempt.id, ["t-a"])
    task_store.upsert_task(
        task_id="t-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="x",
        status="done",
        summaries=[{"outcome": "success", "summary": "good output"}],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )
    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id, iteration_id=iteration.id, attempt_id=attempt.id
        ),
        deps,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == [
        "iteration_statement",
        "task_specification",
        "completed_task_summary",
        "evaluation_criteria",
    ]
    assert all(
        b.priority == ContextPriority.REQUIRED
        for b in [packet.blocks[1], packet.blocks[-1]]
    )
    assert packet.blocks[0].metadata["tag"] == "goal_current_iteration"
    assert packet.blocks[2].metadata["group_tag"] == "completed_tasks"
    assert packet.blocks[-1].kind == "evaluation_criteria"


def test_evaluator_renders_every_generator_summary_in_attempt_order(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=["all work passes"],
        next_iteration_handoff_goal=None,
    )
    task_ids = [f"t-{i}" for i in range(14)]
    attempt_store.set_generator_task_ids(attempt.id, task_ids)
    for task_id in task_ids:
        task_store.upsert_task(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            role="generator",
            agent_name="executor",
            context_message=f"work for {task_id}",
            status="done",
            summaries=[{"summary": f"summary for {task_id}"}],
            needs=[],
            task_center_attempt_id=attempt.id,
            spawn_reason="attempt_generator",
        )

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
        ),
        deps,
    )

    summary_blocks = [
        b for b in packet.blocks if b.kind == "completed_task_summary"
    ]
    assert [b.source_id for b in summary_blocks] == task_ids
    assert [b.text for b in summary_blocks] == [
        f"summary for {task_id}" for task_id in task_ids
    ]
    assert all(block.priority == ContextPriority.HIGH for block in summary_blocks)
    assert all(
        block.metadata["group_tag"] == "completed_tasks"
        for block in summary_blocks
    )
    assert all(
        block.metadata["child_tag"] == "task" for block in summary_blocks
    )
    assert packet.blocks[-1].kind == "evaluation_criteria"


def test_evaluator_missing_generator_task_raises_context_error(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=["all work passes"],
        next_iteration_handoff_goal=None,
    )
    attempt_store.set_generator_task_ids(attempt.id, ["t-missing"])

    with pytest.raises(ContextEngineError, match="Generator task 't-missing'"):
        _evaluator_build(
            ContextScope(
                goal_id=req.id,
                iteration_id=iteration.id,
                attempt_id=attempt.id,
            ),
            deps,
        )


def test_evaluator_continues_goal_emits_nested_handoff_child_no_boundary_block(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """Continues-goal attempts emit a ``<next_iteration_handoff_goal>`` child
    under ``<attempt_plan>``; the previous PARTIAL_PLAN_BOUNDARY block is gone."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="partial attempt spec",
        evaluation_criteria=["current slice passes"],
        next_iteration_handoff_goal="build admin tools next",
    )
    attempt_store.set_generator_task_ids(attempt.id, ["t-a"])
    task_store.upsert_task(
        task_id="t-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="x",
        status="done",
        summaries=[{"summary": "completed current slice"}],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id, iteration_id=iteration.id, attempt_id=attempt.id
        ),
        deps,
    )

    kinds = [b.kind for b in packet.blocks]
    assert kinds == [
        "iteration_statement",
        "task_specification",
        "task_specification",
        "completed_task_summary",
        "evaluation_criteria",
    ]
    assert "partial_plan_boundary" not in kinds, (
        "PARTIAL_PLAN_BOUNDARY enum/block removal must hold"
    )
    plan_spec_block, handoff_block = packet.blocks[1], packet.blocks[2]
    assert plan_spec_block.metadata["child_tag"] == "plan_spec"
    assert handoff_block.metadata["child_tag"] == "next_iteration_handoff_goal"
    assert handoff_block.metadata["is_partial"] == "true"
    assert plan_spec_block.metadata["group_id"] == handoff_block.metadata["group_id"]
    assert plan_spec_block.metadata["group_tag"] == "attempt_plan"
    assert handoff_block.text == "build admin tools next"


def test_evaluator_iteration2_frame_precedes_attempt_contract(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration1 = _seed_iteration(iteration_store, goal_id=req.id)
    iteration_store.close_succeeded(
        iteration1.id,
        plan_spec="accepted plan",
        task_summary="accepted summary",
        closed_at=datetime.now(UTC),
    )
    iteration2 = _seed_continuation_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration2.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="attempt plan",
        evaluation_criteria=["criterion"],
        next_iteration_handoff_goal=None,
    )

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id, iteration_id=iteration2.id, attempt_id=attempt.id
        ),
        deps,
    )

    assert [b.kind for b in packet.blocks] == [
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
        "task_specification",
        "evaluation_criteria",
    ]
    assert packet.blocks[0].metadata["tag"] == "goal"
    assert packet.blocks[1].metadata["child_tag"] == "accepted_plan"
    assert packet.blocks[1].metadata["group_tag"] == "iteration"
    assert packet.blocks[3].metadata["child_tag"] == "iteration_goal"
    assert packet.blocks[3].metadata["group_tag"] == "iteration"
    assert packet.blocks[-1].kind == "evaluation_criteria"


def test_evaluator_with_empty_criteria_omits_criteria_block(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """When evaluation_criteria is empty the recipe omits the
    ``<evaluation_criteria>`` block. Task-guidance prose is now assembled
    at launch time by ``AgentEntryComposer`` rather than emitted as a packet
    block, so the packet ends on the attempt-plan group."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=[],
        next_iteration_handoff_goal=None,
    )

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id, iteration_id=iteration.id, attempt_id=attempt.id
        ),
        deps,
    )

    kinds = [b.kind for b in packet.blocks]
    assert "evaluation_criteria" not in kinds
    assert all(b.kind != "role_instruction" for b in packet.blocks)


# ---------------------------------------------------------------------------
# entry_executor
# ---------------------------------------------------------------------------


def test_entry_executor_emits_one_required_entry_request_block(
    deps, task_store, task_center_run_id
):
    task_store.upsert_task(
        task_id="entry",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="entry_executor",
        context_message="user prompt",
        status="running",
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason="entry_executor",
    )
    packet = _entry_executor_build(
        ContextScope(task_id="entry"),
        deps,
    )
    assert packet.canonical_refs.goal_id is None
    assert packet.canonical_refs.task_id == "entry"
    assert len(packet.blocks) == 1
    block = packet.blocks[0]
    assert block.kind == "entry_request"
    assert block.priority == ContextPriority.REQUIRED
    assert block.text == "user prompt"
    # No goal_summary in entry-time context — it ships at close.
    assert all(b.kind != "goal_summary" for b in packet.blocks)


# ---------------------------------------------------------------------------
# register_builtin_recipes
# ---------------------------------------------------------------------------


def test_register_builtin_recipes_is_idempotent():
    from task_center.context_engine.recipes import register_builtin_recipes
    from task_center.context_engine.recipes_registry import RecipeRegistry

    saved = dict(RecipeRegistry._registry)
    RecipeRegistry.clear()
    try:
        register_builtin_recipes()
        first = set(RecipeRegistry.list_ids())
        register_builtin_recipes()
        second = set(RecipeRegistry.list_ids())
        assert first == second
        assert {
            "planner",
            "generator",
            "evaluator",
            "entry_executor",
        }.issubset(first)
    finally:
        RecipeRegistry.clear()
        RecipeRegistry._registry.update(saved)
