"""US-010: planner block taxonomy and conditional logic.

The planner recipe has two history paths: the **relay** (prior iterations'
canonical outcomes, read from ``iteration.outcomes`` via
``parse_outcomes_record``) and the **retry** (the current iteration's failed
attempts). There is no ``<plan_spec>`` / ``<evaluation_criteria>`` /
``<evaluator_summary>`` — the evaluator role is gone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from task_center._core.outcomes import Outcome, to_record
from task_center._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationCreationReason,
    IterationStatus,
)
from task_center.context_engine.engine import ContextEngineDeps
from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.planner import build_planner_context
from task_center.context_engine.renderer import XmlPromptRenderer
from task_center.context_engine.scope import ContextScope


@pytest.fixture
def deps_with_stores(
    workflow_store, iteration_store, attempt_store, task_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _seed_workflow(workflow_store, task_center_run_id, goal="goal"):
    return workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="parent-task",
        workflow_goal=goal,
    )


def _seed_iteration(
    iteration_store,
    *,
    workflow_id: str,
    sequence_no: int,
    goal: str = "g",
):
    return iteration_store.insert(
        workflow_id=workflow_id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=goal,
        attempt_budget=2,
    )


def _outcomes_record(*outcomes: tuple[str, str]) -> str:
    """Build a denormalized ``iteration.outcomes`` record (JSON list).

    Each ``(local_id, text)`` becomes one ``status="success"`` entry; prior
    iterations render one ``<task id status>`` child per entry.
    """
    return json.dumps(
        [
            to_record(Outcome(local_id=local_id, status="success", outcome=text))
            for local_id, text in outcomes
        ]
    )


def _close_iteration_succeeded(iteration_store, iteration_id, *, summary: str):
    """Close a prior iteration with a JSON outcomes record (local_id ``"t"``)."""
    return iteration_store.close_succeeded(
        iteration_id,
        outcomes=_outcomes_record(("t", summary)),
        closed_at=datetime.now(UTC),
    )


def _seed_failed_attempt(attempt_store, iteration_id, *, sequence_no: int):
    g = attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )
    return attempt_store.close(
        g.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.TASK_FAILED,
        closed_at=datetime.now(UTC),
    )


def _seed_running_attempt(attempt_store, iteration_id, *, sequence_no: int):
    return attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )


# ---------------------------------------------------------------------------
# iteration-1 branch
# ---------------------------------------------------------------------------


def test_iteration1_emits_goal_then_current_iteration_child(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    """Iteration 1 emits standalone ``<goal>`` plus a current-iteration group
    whose ``<iteration_goal>`` body is the identity marker."""
    request = _seed_workflow(workflow_store, task_center_run_id, goal="overall")
    iteration = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="overall"
    )
    g = _seed_running_attempt(attempt_store, iteration.id, sequence_no=1)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id, iteration_id=iteration.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["goal_statement", "iteration_statement"]
    goal_block, iteration_goal = packet.blocks
    assert goal_block.metadata["tag"] == "goal"
    assert iteration_goal.metadata["child_tag"] == "iteration_goal"
    assert iteration_goal.metadata["group_tag"] == "iteration"
    assert iteration_goal.metadata["group_attrs"] == (
        'iteration_no="1" position="current"'
    )
    assert iteration_goal.metadata["iteration_no"] == "1"
    assert iteration_goal.text == "(identical to &lt;goal&gt;)"
    assert packet.target_id == g.id


# ---------------------------------------------------------------------------
# iteration-2 / iteration-N branch (relay)
# ---------------------------------------------------------------------------


def test_iteration2_emits_goal_prior_results_and_current_iteration(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_workflow(workflow_store, task_center_run_id, goal="overall")
    iteration1 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="iteration1 goal"
    )
    _close_iteration_succeeded(
        iteration_store, iteration1.id, summary="iteration1 summary"
    )
    iteration2 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=2, goal="iteration2 goal"
    )
    g = _seed_running_attempt(attempt_store, iteration2.id, sequence_no=1)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id, iteration_id=iteration2.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    # Each prior-iteration outcome entry is one <task> child under a single
    # prior_iteration_summary block.
    kinds = [b.kind for b in packet.blocks]
    assert kinds == [
        "goal_statement",
        "prior_iteration_summary",
        "iteration_statement",
    ]
    assert packet.blocks[0].metadata["tag"] == "goal"
    prior_task = packet.blocks[1]
    assert prior_task.priority == ContextPriority.HIGH
    assert prior_task.metadata["child_tag"] == "task"
    assert prior_task.metadata["group_tag"] == "iteration"
    assert prior_task.metadata["group_attrs"] == 'iteration_no="1" position="prior"'
    assert prior_task.metadata["attrs"] == 'id="t" status="success"'
    assert prior_task.text == "iteration1 summary"
    iteration_goal = packet.blocks[2]
    assert iteration_goal.metadata["child_tag"] == "iteration_goal"
    assert iteration_goal.metadata["group_tag"] == "iteration"
    assert iteration_goal.metadata["group_attrs"] == 'iteration_no="2" position="current"'
    assert iteration_goal.metadata["iteration_no"] == "2"


def test_iteration3_emits_two_pairs_with_priority_split(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_workflow(workflow_store, task_center_run_id, goal="overall")
    iteration1 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="g1"
    )
    _close_iteration_succeeded(iteration_store, iteration1.id, summary="sum1")
    iteration2 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=2, goal="g2"
    )
    _close_iteration_succeeded(iteration_store, iteration2.id, summary="sum2")
    iteration3 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=3, goal="g3"
    )
    g = _seed_running_attempt(attempt_store, iteration3.id, sequence_no=1)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id, iteration_id=iteration3.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    # Two prior iterations in sequence order; immediate prior is HIGH.
    priors = [
        b for b in packet.blocks if b.kind == "prior_iteration_summary"
    ]
    assert len(priors) == 2
    assert priors[0].metadata["group_attrs"] == 'iteration_no="1" position="prior"'
    assert priors[0].priority == ContextPriority.MEDIUM
    assert priors[1].metadata["group_attrs"] == 'iteration_no="2" position="prior"'
    assert priors[1].priority == ContextPriority.HIGH


def test_prior_iteration_without_outcomes_emits_no_prior_block(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    """A closed iteration with null ``outcomes`` contributes no prior block.

    The recipe reads ``iteration.outcomes`` via ``parse_outcomes_record``; a
    null value degrades to an empty list (no raise), so the relay simply omits
    that prior iteration rather than failing.
    """
    request = _seed_workflow(workflow_store, task_center_run_id)
    iteration1 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="g1"
    )
    # Close via set_status (does not write denormalized outcomes).
    iteration_store.set_status(
        iteration1.id, status=IterationStatus.SUCCEEDED, closed_at=datetime.now(UTC)
    )
    iteration2 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=2, goal="g2"
    )
    g = _seed_running_attempt(attempt_store, iteration2.id, sequence_no=1)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id, iteration_id=iteration2.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    assert [b.kind for b in packet.blocks] == ["goal_statement", "iteration_statement"]


# ---------------------------------------------------------------------------
# Failed-attempt landscape blocks (retry — current iteration retries)
# ---------------------------------------------------------------------------


def test_three_failed_attempts_emit_three_high_priority_blocks(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="g"
    )
    for n in (1, 2, 3):
        _seed_failed_attempt(attempt_store, iteration.id, sequence_no=n)
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=4)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt"
    ]
    assert len(failed_blocks) == 3
    for block in failed_blocks:
        assert block.priority == ContextPriority.HIGH
    # Failed-attempt attrs are attempt_no only (no status/verdict): the attempt
    # is a prior attempt OF the current iteration, so a verdict would mislead.
    assert [b.metadata["attrs"] for b in failed_blocks] == [
        'attempt_no="1"',
        'attempt_no="2"',
        'attempt_no="3"',
    ]


def test_failed_attempt_includes_plan_type_statuses_and_summaries(
    deps_with_stores, workflow_store, iteration_store, attempt_store, task_store,
    task_center_run_id,
):
    request = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="g"
    )
    failed = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_generator_task_ids(failed.id, ["gen-a", "gen-b"])
    task_store.upsert_task(
        task_id="gen-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="a",
        status="done",
        outcomes=[{"outcome": "implemented A"}],
        needs=[],
    )
    task_store.upsert_task(
        task_id="gen-b",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="b",
        status="failed",
        outcomes=[{"outcome": "B failed after creating fixture"}],
        needs=[],
    )
    attempt_store.close(
        failed.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.TASK_FAILED,
        closed_at=datetime.now(UTC),
    )
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=2)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )

    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt"
    ]
    assert len(failed_blocks) == 1
    text = failed_blocks[0].text
    # The failed-attempt body is one <task id status> per terminal plan task
    # (status-vocab: done->success, failed->failure) followed by a <failure>
    # line. Wrappers and evaluator/plan_spec elements are dropped.
    assert "<attempt_plan>" not in text
    assert "<plan_spec>" not in text
    assert "<evaluator_summary>" not in text
    # Generator statuses + texts render as <task> children.
    assert '<task id="gen-a" status="success">\nimplemented A\n</task>' in text
    assert (
        '<task id="gen-b" status="failure">\nB failed after creating fixture\n</task>'
    ) in text
    # The failure line names the failed task (any role).
    assert (
        "<failure>\ngenerator gen-b: B failed after creating fixture\n</failure>"
    ) in text


def test_all_failed_attempts_render_as_high_priority_blocks(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_workflow(workflow_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="g"
    )
    total = 8
    for n in range(1, total + 1):
        _seed_failed_attempt(attempt_store, iteration.id, sequence_no=n)
    current_attempt = _seed_running_attempt(
        attempt_store, iteration.id, sequence_no=total + 1
    )

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt"
    ]
    assert len(failed_blocks) == total
    assert [b.metadata["attrs"] for b in failed_blocks] == [
        f'attempt_no="{n}"' for n in range(1, total + 1)
    ]
    assert all(block.priority == ContextPriority.HIGH for block in failed_blocks)
    assert all("truncated_count" not in block.metadata for block in failed_blocks)


# ---------------------------------------------------------------------------
# Reading-A structural acceptance test (iteration 2+)
# ---------------------------------------------------------------------------


def test_iteration_2_plus_reading_a_structure(
    deps_with_stores, workflow_store, iteration_store, attempt_store,
    task_center_run_id,
):
    """Structural lock for the planner relay reframing.

    Asserts block-kind order and rendered XML structure for a scenario with 2
    prior closed iterations and a current iteration (sequence_no=3).
    """
    request = _seed_workflow(workflow_store, task_center_run_id, goal="overall goal")
    iteration1 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=1, goal="iteration 1 goal"
    )
    _close_iteration_succeeded(iteration_store, iteration1.id, summary="iter1 summary")
    iteration2 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=2, goal="iteration 2 goal"
    )
    _close_iteration_succeeded(iteration_store, iteration2.id, summary="iter2 summary")
    iteration3 = _seed_iteration(
        iteration_store, workflow_id=request.id, sequence_no=3, goal="iteration 3 goal"
    )
    current_attempt = _seed_running_attempt(attempt_store, iteration3.id, sequence_no=1)

    packet = build_planner_context(
        ContextScope(
            workflow_id=request.id,
            iteration_id=iteration3.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )

    # 1. Block-kind order. Each prior iteration is a single prior_iteration_summary
    # block (one <task> per outcomes entry).
    tier_kinds = {
        "goal_statement",
        "prior_iteration_summary",
        "iteration_statement",
    }
    assert [b.kind for b in packet.blocks if b.kind in tier_kinds] == [
        "goal_statement",
        "prior_iteration_summary",
        "prior_iteration_summary",
        "iteration_statement",
    ]

    # 2. Renderer output structure (XML tags):
    renderer = XmlPromptRenderer()
    rendered = renderer.render_context(packet)
    assert rendered.startswith("<goal>\n")
    assert "</goal>" in rendered
    assert '<iteration iteration_no="3" position="current">' in rendered
    assert "<iteration_goal>\niteration 3 goal\n</iteration_goal>" in rendered
    assert "<accepted_plan>" not in rendered
    for n in (1, 2):
        assert f'<iteration iteration_no="{n}" position="prior">' in rendered
        assert f'<task id="t" status="success">\niter{n} summary\n</task>' in rendered

    # 3. Each prior iteration shares a group_id; the standalone <goal> block
    # carries metadata['tag'] without a group_id.
    assert packet.blocks[0].metadata["tag"] == "goal"
    assert "group_id" not in packet.blocks[0].metadata
    group_ids = {
        b.metadata.get("group_id")
        for b in packet.blocks
        if b.kind == "prior_iteration_summary"
    }
    assert group_ids == {"iteration_1_prior", "iteration_2_prior"}
