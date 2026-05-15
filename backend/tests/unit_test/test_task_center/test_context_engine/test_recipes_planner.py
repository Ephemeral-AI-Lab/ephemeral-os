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
from task_center.context_engine.scope import ContextScope
from task_center.trial import (
    TrialFailReason,
    TrialStatus,
)
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
)


@pytest.fixture
def deps_with_stores(
    mission_store, episode_store, attempt_store, task_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
    )


def _seed_mission(mission_store, task_center_run_id, goal="goal"):
    return mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t-entry",
        goal=goal,
    )


def _seed_episode(
    episode_store,
    *,
    goal_id: str,
    sequence_no: int,
    goal: str = "g",
):
    return episode_store.insert(
        goal_id=goal_id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        goal=goal,
        trial_budget=2,
    )


def _close_episode_succeeded(
    episode_store, episode_id, *, spec: str, summary: str
):
    return episode_store.close_succeeded(
        episode_id,
        task_specification=spec,
        task_summary=summary,
        closed_at=datetime.now(UTC),
    )


def _seed_failed_attempt(attempt_store, episode_id, *, sequence_no: int):
    g = attempt_store.insert(
        iteration_id=episode_id, trial_sequence_no=sequence_no
    )
    attempt_store.set_plan_contract(
        g.id,
        task_specification=f"spec-{sequence_no}",
        evaluation_criteria=[f"crit-{sequence_no}-a", f"crit-{sequence_no}-b"],
        continuation_goal=None,
    )
    return attempt_store.close(
        g.id,
        status=TrialStatus.FAILED,
        fail_reason=TrialFailReason.GENERATOR_FAILED,
        closed_at=datetime.now(UTC),
    )


def _seed_running_attempt(attempt_store, episode_id, *, sequence_no: int):
    return attempt_store.insert(
        iteration_id=episode_id, trial_sequence_no=sequence_no
    )


# ---------------------------------------------------------------------------
# iteration-1 branch
# ---------------------------------------------------------------------------


def test_episode1_emits_one_merged_mission_episode_block(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id, goal="overall")
    iteration = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="overall"
    )
    g = _seed_running_attempt(attempt_store, iteration.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=iteration.id, trial_id=g.id
        ),
        deps_with_stores,
    )
    kinds = [b.kind for b in packet.blocks]
    assert kinds == ["iteration_statement"]
    episode_goal = packet.blocks[0]
    assert episode_goal.metadata["heading"] == "# Goal / Current Iteration"
    assert packet.target_id == g.id


# ---------------------------------------------------------------------------
# iteration-2 / iteration-N branch
# ---------------------------------------------------------------------------


def test_episode2_emits_mission_prior_results_and_current_episode(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id, goal="overall")
    episode1 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="episode1 goal"
    )
    _close_episode_succeeded(
        episode_store, episode1.id, spec="episode1 spec", summary="episode1 summary"
    )
    episode2 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=2, goal="episode2 goal"
    )
    g = _seed_running_attempt(attempt_store, episode2.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=episode2.id, trial_id=g.id
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
    assert packet.blocks[0].metadata["group_heading"] == "# Goal"
    prior_spec = packet.blocks[1]
    assert prior_spec.priority == ContextPriority.HIGH
    assert prior_spec.metadata["iteration_sequence_no"] == "1"
    assert prior_spec.metadata["group_heading"] == "# Goal"
    assert prior_spec.text == "episode1 spec"
    episode_goal = packet.blocks[3]
    assert episode_goal.metadata["heading"] == "# Current Iteration"


def test_episode3_emits_two_pairs_with_priority_split(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id, goal="overall")
    episode1 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="g1"
    )
    _close_episode_succeeded(episode_store, episode1.id, spec="s1", summary="sum1")
    episode2 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=2, goal="g2"
    )
    _close_episode_succeeded(episode_store, episode2.id, spec="s2", summary="sum2")
    episode3 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=3, goal="g3"
    )
    g = _seed_running_attempt(attempt_store, episode3.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=episode3.id, trial_id=g.id
        ),
        deps_with_stores,
    )
    # Two prior iterations in sequence order; immediate prior is HIGH.
    prior_specs = [
        b for b in packet.blocks if b.kind == "prior_iteration_specification"
    ]
    assert len(prior_specs) == 2
    assert prior_specs[0].metadata["iteration_sequence_no"] == "1"
    assert prior_specs[0].priority == ContextPriority.MEDIUM
    assert prior_specs[1].metadata["iteration_sequence_no"] == "2"
    assert prior_specs[1].priority == ContextPriority.HIGH


def test_missing_prior_spec_raises_context_engine_error(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    """Closed iteration-1 with task_specification still null is an invariant
    violation; recipe must raise."""
    request = _seed_mission(mission_store, task_center_run_id)
    episode1 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="g1"
    )
    # Close via legacy set_status (does not write denormalized fields).
    episode_store.set_status(
        episode1.id, status=IterationStatus.SUCCEEDED, closed_at=datetime.now(UTC)
    )
    episode2 = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=2, goal="g2"
    )
    g = _seed_running_attempt(attempt_store, episode2.id, sequence_no=1)

    with pytest.raises(ContextEngineError):
        _planner_build(
            ContextScope(
                goal_id=request.id, iteration_id=episode2.id, trial_id=g.id
            ),
            deps_with_stores,
        )


# ---------------------------------------------------------------------------
# Failed-attempt landscape blocks (current iteration retries)
# ---------------------------------------------------------------------------


def test_three_failed_attempts_emit_three_high_priority_blocks(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id)
    iteration = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="g"
    )
    for n in (1, 2, 3):
        _seed_failed_attempt(attempt_store, iteration.id, sequence_no=n)
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=4)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration.id,
            trial_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_trial_landscape"
    ]
    assert len(failed_blocks) == 3
    for block in failed_blocks:
        assert block.priority == ContextPriority.HIGH
    assert [b.metadata["trial_sequence_no"] for b in failed_blocks] == [
        "1",
        "2",
        "3",
    ]


def test_failed_attempt_landscape_includes_plan_type_statuses_and_summaries(
    deps_with_stores, mission_store, episode_store, attempt_store, task_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id)
    iteration = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="g"
    )
    failed = attempt_store.insert(iteration_id=iteration.id, trial_sequence_no=1)
    attempt_store.set_plan_contract(
        failed.id,
        task_specification="partial failed spec",
        evaluation_criteria=["criterion"],
        continuation_goal="continue with later slice",
    )
    attempt_store.set_generator_task_ids(failed.id, ["gen-a", "gen-b"])
    task_store.upsert_task(
        task_id="gen-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        rendered_prompt="a",
        status="done",
        summaries=[{"summary": "implemented A"}],
        needs=[],
        task_center_attempt_id=failed.id,
        spawn_reason="trial_generator",
    )
    task_store.upsert_task(
        task_id="gen-b",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        rendered_prompt="b",
        status="failed",
        summaries=[{"summary": "B failed after creating fixture"}],
        needs=[],
        task_center_attempt_id=failed.id,
        spawn_reason="trial_generator",
    )
    attempt_store.close(
        failed.id,
        status=TrialStatus.FAILED,
        fail_reason=TrialFailReason.EVALUATOR_FAILED,
        closed_at=datetime.now(UTC),
    )
    current_attempt = _seed_running_attempt(attempt_store, iteration.id, sequence_no=2)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id,
            iteration_id=iteration.id,
            trial_id=current_attempt.id,
        ),
        deps_with_stores,
    )

    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_trial_landscape"
    ]
    assert len(failed_blocks) == 1
    text = failed_blocks[0].text
    assert "Plan type: partial" in text
    assert "continue with later slice" not in text
    assert "- gen-a: done" in text
    assert "- gen-b: failed" in text
    assert "#### gen-a\n\nimplemented A" in text
    assert "#### gen-b\n\nB failed after creating fixture" in text
    assert "fail_reason" not in text


def test_all_failed_attempts_render_as_high_priority_blocks(
    deps_with_stores, mission_store, episode_store, attempt_store,
    task_center_run_id,
):
    request = _seed_mission(mission_store, task_center_run_id)
    iteration = _seed_episode(
        episode_store, goal_id=request.id, sequence_no=1, goal="g"
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
            trial_id=current_attempt.id,
        ),
        deps_with_stores,
    )
    failed_blocks = [
        b for b in packet.blocks if b.kind == "failed_trial_landscape"
    ]
    assert len(failed_blocks) == total
    assert [b.metadata["trial_sequence_no"] for b in failed_blocks] == [
        str(n) for n in range(1, total + 1)
    ]
    assert all(block.priority == ContextPriority.HIGH for block in failed_blocks)
    assert all("truncated_count" not in block.metadata for block in failed_blocks)
