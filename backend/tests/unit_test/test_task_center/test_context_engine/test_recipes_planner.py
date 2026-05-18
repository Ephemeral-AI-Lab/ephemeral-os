"""US-010: planner block taxonomy and conditional logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextPriority,
)
from task_center.context_engine.recipes.planner import (
    _planner_build,
)
from task_center.context_engine.renderer import XmlPromptRenderer
from task_center.context_engine.scope import ContextScope
from task_center.attempt import (
    AttemptFailReason,
    AttemptStatus,
)
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
)


@pytest.fixture
def deps_with_stores(
    goal_store, iteration_store, attempt_store, task_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )


def _seed_goal(goal_store, task_center_run_id, goal="goal"):
    return goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t-entry",
        goal=goal,
    )


def _seed_iteration(
    iteration_store,
    *,
    goal_id: str,
    sequence_no: int,
    goal: str = "g",
):
    return iteration_store.insert(
        goal_id=goal_id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        goal=goal,
        attempt_budget=2,
    )


def _close_iteration_succeeded(
    iteration_store, iteration_id, *, spec: str, summary: str
):
    return iteration_store.close_succeeded(
        iteration_id,
        plan_spec=spec,
        task_summary=summary,
        closed_at=datetime.now(UTC),
    )


def _seed_failed_attempt(attempt_store, iteration_id, *, sequence_no: int):
    g = attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )
    attempt_store.set_plan_contract(
        g.id,
        plan_spec=f"spec-{sequence_no}",
        evaluation_criteria=[f"crit-{sequence_no}-a", f"crit-{sequence_no}-b"],
        next_iteration_handoff_goal=None,
    )
    return attempt_store.close(
        g.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.GENERATOR_FAILED,
        closed_at=datetime.now(UTC),
    )


def _seed_running_attempt(attempt_store, iteration_id, *, sequence_no: int):
    return attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )


# ---------------------------------------------------------------------------
# iteration-1 branch
# ---------------------------------------------------------------------------


def test_iteration1_emits_one_merged_goal_iteration_block(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id, goal="overall")
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="overall"
    )
    g = _seed_running_attempt(attempt_store, iteration.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=iteration.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["iteration_statement"]
    iteration_goal = packet.blocks[0]
    assert iteration_goal.metadata["tag"] == "goal_current_iteration"
    assert iteration_goal.metadata["iteration_no"] == "1"
    assert packet.target_id == g.id


# ---------------------------------------------------------------------------
# iteration-2 / iteration-N branch
# ---------------------------------------------------------------------------


def test_iteration2_emits_goal_prior_results_and_current_iteration(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id, goal="overall")
    iteration1 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="iteration1 goal"
    )
    _close_iteration_succeeded(
        iteration_store, iteration1.id, spec="iteration1 spec", summary="iteration1 summary"
    )
    iteration2 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=2, goal="iteration2 goal"
    )
    g = _seed_running_attempt(attempt_store, iteration2.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=iteration2.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == [
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
    ]
    assert packet.blocks[0].metadata["tag"] == "goal"
    prior_spec = packet.blocks[1]
    assert prior_spec.priority == ContextPriority.HIGH
    assert prior_spec.metadata["child_tag"] == "accepted_plan"
    assert prior_spec.metadata["group_tag"] == "iteration"
    assert prior_spec.metadata["group_attrs"] == 'iteration_no="1" status="prior"'
    assert prior_spec.text == "iteration1 spec"
    iteration_goal = packet.blocks[3]
    assert iteration_goal.metadata["child_tag"] == "iteration_goal"
    assert iteration_goal.metadata["group_tag"] == "iteration"
    assert iteration_goal.metadata["group_attrs"] == 'iteration_no="2" status="current"'
    assert iteration_goal.metadata["iteration_no"] == "2"


def test_iteration3_emits_two_pairs_with_priority_split(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id, goal="overall")
    iteration1 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="g1"
    )
    _close_iteration_succeeded(iteration_store, iteration1.id, spec="s1", summary="sum1")
    iteration2 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=2, goal="g2"
    )
    _close_iteration_succeeded(iteration_store, iteration2.id, spec="s2", summary="sum2")
    iteration3 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=3, goal="g3"
    )
    g = _seed_running_attempt(attempt_store, iteration3.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=iteration3.id, attempt_id=g.id
        ),
        deps_with_stores,
    )
    # Two prior iterations in sequence order; immediate prior is HIGH.
    prior_specs = [
        b for b in packet.blocks if b.kind == "prior_iteration_specification"
    ]
    assert len(prior_specs) == 2
    assert prior_specs[0].metadata["group_attrs"] == 'iteration_no="1" status="prior"'
    assert prior_specs[0].priority == ContextPriority.MEDIUM
    assert prior_specs[1].metadata["group_attrs"] == 'iteration_no="2" status="prior"'
    assert prior_specs[1].priority == ContextPriority.HIGH


def test_missing_prior_spec_raises_context_engine_error(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    """Closed iteration-1 with plan_spec still null is an invariant
    violation; recipe must raise."""
    request = _seed_goal(goal_store, task_center_run_id)
    iteration1 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="g1"
    )
    # Close via legacy set_status (does not write denormalized fields).
    iteration_store.set_status(
        iteration1.id, status=IterationStatus.SUCCEEDED, closed_at=datetime.now(UTC)
    )
    iteration2 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=2, goal="g2"
    )
    g = _seed_running_attempt(attempt_store, iteration2.id, sequence_no=1)

    with pytest.raises(ContextEngineError):
        _planner_build(
            ContextScope(
                goal_id=request.id, iteration_id=iteration2.id, attempt_id=g.id
            ),
            deps_with_stores,
        )


# ---------------------------------------------------------------------------
# Failed-attempt landscape blocks (current iteration retries)
# ---------------------------------------------------------------------------


def test_three_failed_attempts_emit_three_high_priority_blocks(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="g"
    )
    for n in (1, 2, 3):
        _seed_failed_attempt(attempt_store, iteration.id, sequence_no=n)
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=4)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt_landscape"
    ]
    assert len(failed_blocks) == 3
    for block in failed_blocks:
        assert block.priority == ContextPriority.HIGH
    assert [b.metadata["attrs"] for b in failed_blocks] == [
        'attempt_no="1" status="failed"',
        'attempt_no="2" status="failed"',
        'attempt_no="3" status="failed"',
    ]


def test_failed_attempt_landscape_includes_plan_type_statuses_and_summaries(
    deps_with_stores, goal_store, iteration_store, attempt_store, task_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="g"
    )
    failed = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    attempt_store.set_plan_contract(
        failed.id,
        plan_spec="partial failed spec",
        evaluation_criteria=["criterion"],
        next_iteration_handoff_goal="continue with later slice",
    )
    attempt_store.set_generator_task_ids(failed.id, ["gen-a", "gen-b"])
    task_store.upsert_task(
        task_id="gen-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="a",
        status="done",
        summaries=[{"summary": "implemented A"}],
        needs=[],
        task_center_attempt_id=failed.id,
        spawn_reason="attempt_generator",
    )
    task_store.upsert_task(
        task_id="gen-b",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="b",
        status="failed",
        summaries=[{"summary": "B failed after creating fixture"}],
        needs=[],
        task_center_attempt_id=failed.id,
        spawn_reason="attempt_generator",
    )
    attempt_store.close(
        failed.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.EVALUATOR_FAILED,
        closed_at=datetime.now(UTC),
    )
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=2)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )

    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt_landscape"
    ]
    assert len(failed_blocks) == 1
    text = failed_blocks[0].text
    assert "<attempt_plan>" in text
    assert "<plan_spec>\npartial failed spec\n</plan_spec>" in text
    assert (
        "<next_iteration_handoff_goal>\ncontinue with later slice\n"
        "</next_iteration_handoff_goal>"
    ) in text
    assert "<generator_outcomes>" in text
    assert "gen-a: done" in text
    assert "gen-b: failed" in text
    assert '<task id="gen-a" status="done">\nimplemented A\n</task>' in text
    assert "fail_reason" not in text
    # Generator failure bypasses evaluator regardless of evaluator_task_id.
    assert (
        '<evaluator_judgment status="bypassed" reason="generator_failed">'
    ) in text


def test_all_failed_attempts_render_as_high_priority_blocks(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    request = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="g"
    )
    total = 8
    for n in range(1, total + 1):
        _seed_failed_attempt(attempt_store, iteration.id, sequence_no=n)
    current_attempt = _seed_running_attempt(
        attempt_store, iteration.id, sequence_no=total + 1
    )

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_attempt_landscape"
    ]
    assert len(failed_blocks) == total
    assert [b.metadata["attrs"] for b in failed_blocks] == [
        f'attempt_no="{n}" status="failed"' for n in range(1, total + 1)
    ]
    assert all(block.priority == ContextPriority.HIGH for block in failed_blocks)
    assert all("truncated_count" not in block.metadata for block in failed_blocks)


# ---------------------------------------------------------------------------
# Reading-A structural acceptance test (iteration 2+)
# ---------------------------------------------------------------------------


def test_iteration_2_plus_reading_a_structure(
    deps_with_stores, goal_store, iteration_store, attempt_store,
    task_center_run_id,
):
    """Structural lock for the planner Reading-A reframing (§4, Principle 4).

    Asserts block-kind order and rendered heading structure for a scenario with
    2 prior closed iterations and a current iteration (sequence_no=3).  Uses
    structural assertions (not full-text snapshots) so the test survives a
    future Reading-B rewrite.
    """
    request = _seed_goal(goal_store, task_center_run_id, goal="overall goal")
    iteration1 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1, goal="iteration 1 goal"
    )
    _close_iteration_succeeded(
        iteration_store, iteration1.id, spec="iter1 spec", summary="iter1 summary"
    )
    iteration2 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=2, goal="iteration 2 goal"
    )
    _close_iteration_succeeded(
        iteration_store, iteration2.id, spec="iter2 spec", summary="iter2 summary"
    )
    iteration3 = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=3, goal="iteration 3 goal"
    )
    current_attempt = _seed_running_attempt(attempt_store, iteration3.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration3.id,
            attempt_id=current_attempt.id,
        ),
        deps_with_stores,
    )

    # 1. Block-kind order (structural lock; survives Reading B):
    tier_kinds = {
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
    }
    assert [b.kind for b in packet.blocks if b.kind in tier_kinds] == [
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
    ]

    # 2. Renderer output structure (XML tags, not markdown headings):
    renderer = XmlPromptRenderer()
    rendered = renderer.render_context(packet)
    assert rendered.startswith("<goal>\n")
    assert "</goal>" in rendered
    # Current iteration's <iteration_goal> child wrapped under status="current".
    assert '<iteration iteration_no="3" status="current">' in rendered
    assert "<iteration_goal>\niteration 3 goal\n</iteration_goal>" in rendered
    # Two prior iterations wrap their plan + summary children.
    for n in (1, 2):
        assert f'<iteration iteration_no="{n}" status="prior">' in rendered
        assert f"<accepted_plan>\niter{n} spec\n</accepted_plan>" in rendered
        assert f"<summary>\niter{n} summary\n</summary>" in rendered

    # 3. Each prior iteration shares a group_id; the standalone <goal> block
    # carries metadata['tag'] without a group_id.
    assert packet.blocks[0].metadata["tag"] == "goal"
    assert "group_id" not in packet.blocks[0].metadata
    group_ids = {
        b.metadata.get("group_id")
        for b in packet.blocks
        if b.kind in {"prior_iteration_specification", "prior_iteration_summary"}
    }
    assert group_ids == {"iteration_1_prior", "iteration_2_prior"}
