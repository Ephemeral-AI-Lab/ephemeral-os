"""US-010: generator and reducer recipe happy paths.

Both roles are symmetric: a ``<needs>`` group of upstream ``<task>`` children
followed by an assigned block (``<assigned_task>`` for the generator,
``<assigned_prompt>`` for the reducer). There is no ``<plan_spec>`` — the
planner distributes framing into each task spec — and there is no evaluator
recipe (the role is gone).
"""

from __future__ import annotations

import pytest

from task_center.context_engine.engine import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.generator import build_generator_context
from task_center.context_engine.recipes.reducer import build_reducer_context
from task_center.context_engine.renderer import XmlPromptRenderer
from task_center.context_engine.scope import ContextScope
from task_center._core.state import IterationCreationReason


@pytest.fixture
def deps(
    workflow_store, iteration_store, attempt_store, task_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _seed_workflow(workflow_store, task_center_run_id):
    return workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="parent-task",
        workflow_goal="overall",
    )


def _seed_iteration(iteration_store, *, workflow_id):
    return iteration_store.insert(
        workflow_id=workflow_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="g",
        attempt_budget=2,
    )


# ---------------------------------------------------------------------------
# generator — emits <needs> group + <assigned_task> (no <plan_spec>)
# ---------------------------------------------------------------------------


def test_generator_emits_planned_task_spec_required_block(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    task_id = "t-1"
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="do thing X",
        status="pending",
        outcomes=[],
        needs=[],
    )
    packet = build_generator_context(
        ContextScope(
            workflow_id=req.id,
            attempt_id=attempt.id,
            task_id=task_id,
        ),
        deps,
    )
    # No <plan_spec>; a generator with no needs emits just its assigned task.
    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["planned_task_spec"]
    assert packet.blocks[-1].priority == ContextPriority.REQUIRED
    assert packet.blocks[-1].text == "do thing X"
    assert packet.blocks[-1].metadata["tag"] == "assigned_task"


def test_generator_needs_blocks_are_a_needs_group(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    task_store.upsert_task(
        task_id="t-up",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="parent",
        status="done",
        outcomes=[{"outcome": "produced X"}],
        needs=[],
    )
    task_store.upsert_task(
        task_id="t-down",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="downstream",
        status="pending",
        outcomes=[],
        needs=["t-up"],
    )

    packet = build_generator_context(
        ContextScope(
            workflow_id=req.id, attempt_id=attempt.id, task_id="t-down"
        ),
        deps,
    )
    dep_blocks = [b for b in packet.blocks if b.kind == "dependency_summary"]
    assert len(dep_blocks) == 1
    dep = dep_blocks[0]
    # Needs render as a <needs> GROUP with one <task> child each.
    assert dep.metadata["group_tag"] == "needs"
    assert dep.metadata["child_tag"] == "task"
    assert dep.metadata["attrs"] == 'id="t-up" status="success"'
    assert "produced X" in dep.text
    assert packet.blocks[-1].kind == "planned_task_spec"
    kinds = [b.kind for b in packet.blocks]
    dep_idx = kinds.index("dependency_summary")
    spec_idx = kinds.index("planned_task_spec")
    assert dep_idx < spec_idx


def test_generator_missing_need_task_raises_context_error(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    task_store.upsert_task(
        task_id="t-down",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="downstream",
        status="pending",
        outcomes=[],
        needs=["t-missing"],
    )

    with pytest.raises(ContextEngineError, match="Need task 't-missing'"):
        build_generator_context(
            ContextScope(
                workflow_id=req.id,
                attempt_id=attempt.id,
                task_id="t-down",
            ),
            deps,
        )


# ---------------------------------------------------------------------------
# reducer — symmetric: <needs> group + <assigned_prompt> (its own prompt only)
# ---------------------------------------------------------------------------


def test_reducer_emits_needs_group_then_assigned_prompt(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    task_store.upsert_task(
        task_id="gen-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="x",
        status="done",
        outcomes=[{"outcome": "good output"}],
        needs=[],
    )
    red_id = "att1:red:reduce"
    task_store.upsert_task(
        task_id=red_id,
        task_center_run_id=task_center_run_id,
        role="reducer",
        context_message="gate the slice against the criteria",
        status="pending",
        outcomes=[],
        needs=["gen-a"],
    )

    packet = build_reducer_context(
        ContextScope(workflow_id=req.id, attempt_id=attempt.id, task_id=red_id),
        deps,
    )

    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["dependency_summary", "planned_task_spec"]
    needs_block = packet.blocks[0]
    assert needs_block.metadata["group_tag"] == "needs"
    assert needs_block.metadata["child_tag"] == "task"
    assert needs_block.metadata["attrs"] == 'id="gen-a" status="success"'
    assert "good output" in needs_block.text
    # The reducer's own prompt is its assigned block — NOT a plan-wide view.
    prompt_block = packet.blocks[-1]
    assert prompt_block.metadata["tag"] == "assigned_prompt"
    assert prompt_block.priority == ContextPriority.REQUIRED
    assert prompt_block.text == "gate the slice against the criteria"
    assert packet.target_role == "reducer"
    # iteration is threaded into canonical refs via attempt.iteration_id.
    assert packet.canonical_refs.iteration_id == iteration.id

    # End-to-end render: <needs> group then <assigned_prompt>, no <plan_spec>.
    rendered = XmlPromptRenderer().render_context(packet)
    assert "<needs>" in rendered
    assert '<task id="gen-a" status="success">\ngood output\n</task>' in rendered
    assert (
        '<assigned_prompt task_id="att1:red:reduce">\n'
        "gate the slice against the criteria\n"
        "</assigned_prompt>"
    ) in rendered
    assert "<plan_spec>" not in rendered


def test_reducer_with_no_needs_emits_only_assigned_prompt(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    red_id = "att1:red:reduce"
    task_store.upsert_task(
        task_id=red_id,
        task_center_run_id=task_center_run_id,
        role="reducer",
        context_message="gate everything",
        status="pending",
        outcomes=[],
        needs=[],
    )

    packet = build_reducer_context(
        ContextScope(workflow_id=req.id, attempt_id=attempt.id, task_id=red_id),
        deps,
    )
    assert [b.metadata.get("tag") for b in packet.blocks] == ["assigned_prompt"]


def test_reducer_missing_need_task_raises_context_error(
    deps, workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    req = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(iteration_store, workflow_id=req.id)
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    red_id = "att1:red:reduce"
    task_store.upsert_task(
        task_id=red_id,
        task_center_run_id=task_center_run_id,
        role="reducer",
        context_message="gate",
        status="pending",
        outcomes=[],
        needs=["t-missing"],
    )

    with pytest.raises(ContextEngineError, match="Need task 't-missing'"):
        build_reducer_context(
            ContextScope(workflow_id=req.id, attempt_id=attempt.id, task_id=red_id),
            deps,
        )


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
            "reducer",
        }.issubset(first)
    finally:
        RecipeRegistry.clear()
        RecipeRegistry._registry.update(saved)
