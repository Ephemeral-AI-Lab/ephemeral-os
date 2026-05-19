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
        creation_reason=IterationCreationReason.DEFERRED_GOAL_CONTINUATION,
        goal="g2",
        attempt_budget=2,
    )


# ---------------------------------------------------------------------------
# generator — emits <plan_spec> (no wrapper), <dependency> siblings, <assigned_task>
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
        deferred_goal_for_next_iteration=None,
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
    plan_spec_block = packet.blocks[0]
    assert plan_spec_block.metadata["tag"] == "plan_spec"
    # No <attempt_plan> group wrapper.
    assert "group_tag" not in plan_spec_block.metadata
    assert packet.blocks[-1].kind == "planned_task_spec"
    assert packet.blocks[-1].priority == ContextPriority.REQUIRED
    assert packet.blocks[-1].text == "do thing X"
    assert packet.blocks[-1].metadata["tag"] == "assigned_task"


def test_generator_drops_deferred_goal_from_executor_packet(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """Continues-goal attempt emits only ``<plan_spec>`` to the executor — the
    ``<deferred_goal_for_next_iteration>`` is a planner / evaluator concern."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="attempt spec framing",
        evaluation_criteria=["c1"],
        deferred_goal_for_next_iteration="future iteration work",
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
    assert kinds == ["task_specification", "planned_task_spec"]
    plan_spec_block = packet.blocks[0]
    assert plan_spec_block.metadata["tag"] == "plan_spec"
    # No deferred-goal block survives in the executor packet.
    assert all(
        b.metadata.get("child_tag") != "deferred_goal_for_next_iteration"
        for b in packet.blocks
    )
    assert all(
        b.metadata.get("has_deferred_goal_for_next_iteration") != "true"
        for b in packet.blocks
    )


def test_generator_dependency_blocks_are_flat_siblings(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
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
    dep = dep_blocks[0]
    assert dep.metadata["tag"] == "dependency"
    # No <dependency_results> group wrapper.
    assert "group_tag" not in dep.metadata
    assert dep.metadata["attrs"] == 'id="t-up"'
    assert "produced X" in dep.text
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
# evaluator — emits goal + iteration framing + current_attempt block
# ---------------------------------------------------------------------------


def test_evaluator_emits_current_attempt_block_with_plan_and_criteria_inline(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=["c1", "c2"],
        deferred_goal_for_next_iteration=None,
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
        "goal_statement",
        "iteration_statement",
        "failed_attempt_landscape",
    ]
    goal_block, iteration_goal_block, current_attempt = packet.blocks
    assert goal_block.metadata["tag"] == "goal"
    assert iteration_goal_block.metadata["child_tag"] == "iteration_goal"
    assert iteration_goal_block.metadata["group_tag"] == "iteration"
    assert current_attempt.metadata["child_tag"] == "attempt"
    assert current_attempt.metadata["attrs"].endswith('status="current"')
    assert current_attempt.metadata["pre_rendered_xml"] == "true"
    # All evaluator-visible content is inlined in the attempt body.
    body = current_attempt.text
    assert "<plan_spec>\nevaluator spec\n</plan_spec>" in body
    assert "<evaluation_criteria>" in body
    assert "c1" in body and "c2" in body
    assert '<task id="t-a" status="done">' in body
    assert "good output" in body


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
        deferred_goal_for_next_iteration=None,
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

    current_attempt_blocks = [
        b for b in packet.blocks
        if b.kind == "failed_attempt_landscape"
        and 'status="current"' in b.metadata.get("attrs", "")
    ]
    assert len(current_attempt_blocks) == 1
    body = current_attempt_blocks[0].text
    # Every generator task surfaces in order in the attempt body.
    for task_id in task_ids:
        assert f'<task id="{task_id}" status="done">' in body
        assert f"summary for {task_id}" in body
    # Tasks appear in submitted order.
    indices = [body.index(f'id="{task_id}"') for task_id in task_ids]
    assert indices == sorted(indices)


def test_evaluator_missing_generator_task_does_not_raise(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """The evaluator recipe now consults ``_generator_outcomes`` rather than
    rejecting outright; a missing task surfaces as ``status="missing task row"``
    in the body. The harness-level invariant violation surfaces via the planner
    submission accept path; the recipe is read-only."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=["all work passes"],
        deferred_goal_for_next_iteration=None,
    )
    attempt_store.set_generator_task_ids(attempt.id, ["t-missing"])

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
        ),
        deps,
    )
    current = [
        b for b in packet.blocks
        if b.kind == "failed_attempt_landscape"
        and 'status="current"' in b.metadata.get("attrs", "")
    ]
    assert current
    assert 'status="missing task row"' in current[0].text


def test_evaluator_defers_goal_inlines_deferred_child_in_attempt_body(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="partial attempt spec",
        evaluation_criteria=["current slice passes"],
        deferred_goal_for_next_iteration="build admin tools next",
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

    current = [
        b for b in packet.blocks
        if b.kind == "failed_attempt_landscape"
        and 'status="current"' in b.metadata.get("attrs", "")
    ][0]
    assert current.metadata["has_deferred_goal_for_next_iteration"] == "true"
    assert (
        "<deferred_goal_for_next_iteration>\nbuild admin tools next\n"
        "</deferred_goal_for_next_iteration>"
    ) in current.text


def test_evaluator_iteration2_frame_then_current_attempt(
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
        deferred_goal_for_next_iteration=None,
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
        "failed_attempt_landscape",
    ]
    assert packet.blocks[0].metadata["tag"] == "goal"
    assert packet.blocks[1].metadata["child_tag"] == "accepted_plan"
    assert packet.blocks[1].metadata["group_tag"] == "iteration"
    assert packet.blocks[3].metadata["child_tag"] == "iteration_goal"
    assert packet.blocks[3].metadata["group_tag"] == "iteration"
    assert packet.blocks[-1].metadata["child_tag"] == "attempt"
    assert 'status="current"' in packet.blocks[-1].metadata["attrs"]


def test_evaluator_with_empty_criteria_omits_criteria_block(
    deps, goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    """When evaluation_criteria is empty the criteria block does not appear
    in the attempt body. The packet still emits the active iteration plus
    the attempt block."""
    req = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, goal_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="evaluator spec",
        evaluation_criteria=[],
        deferred_goal_for_next_iteration=None,
    )

    packet = _evaluator_build(
        ContextScope(
            goal_id=req.id, iteration_id=iteration.id, attempt_id=attempt.id
        ),
        deps,
    )

    kinds = [b.kind for b in packet.blocks]
    assert "evaluation_criteria" not in kinds
    current = [
        b for b in packet.blocks
        if b.kind == "failed_attempt_landscape"
        and 'status="current"' in b.metadata.get("attrs", "")
    ]
    assert current
    assert "<evaluation_criteria>" not in current[0].text


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
    assert block.metadata["tag"] == "entry_request"
    assert block.priority == ContextPriority.REQUIRED
    assert block.text == "user prompt"


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
            "planner_full_only",
            "generator",
            "evaluator",
            "entry_executor",
        }.issubset(first)
    finally:
        RecipeRegistry.clear()
        RecipeRegistry._registry.update(saved)
