"""AC #17 — iteration_no invariant.

For every ``ContextBlock`` where ``metadata["iteration_no"]`` is set AND
``metadata["group_attrs"]`` contains ``iteration_no="``, the two integers
agree. Both derive from the same ``Iteration.sequence_no`` in the same
``ContextBlock(...)`` constructor in
``recipes/goal_iteration_frame.py:_current_iteration_goal_child``, so drift
is impossible by construction. This test pins the pairing — if a future
refactor splits the construction across two sites, the test fails before
the captures do.
"""

from __future__ import annotations

import re

import pytest

from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes.goal_iteration_frame import (
    _current_iteration_goal_child,
    _goal_current_iteration_block,
    attempt_plan_blocks,
    goal_iteration_blocks,
)


class _FakeIteration:
    def __init__(self, *, id: str, sequence_no: int, goal: str, plan_spec: str | None = None, task_summary: str | None = None):
        self.id = id
        self.sequence_no = sequence_no
        self.goal = goal
        self.plan_spec = plan_spec
        self.task_summary = task_summary


class _FakeGoal:
    def __init__(self, *, id: str, goal: str):
        self.id = id
        self.goal = goal


class _FakeAttempt:
    def __init__(self, *, id: str, plan_spec: str | None, deferred_goal_for_next_iteration: str | None):
        self.id = id
        self.plan_spec = plan_spec
        self.deferred_goal_for_next_iteration = deferred_goal_for_next_iteration


def _parse_iteration_no_from_group_attrs(group_attrs: str) -> int | None:
    """Extract the integer behind ``iteration_no="N"`` in a group_attrs string."""
    match = re.search(r'iteration_no="(\d+)"', group_attrs)
    return int(match.group(1)) if match else None


def _assert_invariant(blocks: list[ContextBlock]) -> None:
    for block in blocks:
        meta_iteration_no = block.metadata.get("iteration_no")
        group_attrs = block.metadata.get("group_attrs", "")
        attr_iteration_no = _parse_iteration_no_from_group_attrs(group_attrs)
        if meta_iteration_no is None or attr_iteration_no is None:
            continue
        assert int(meta_iteration_no) == attr_iteration_no, (
            f"iteration_no drift on block kind={block.kind!r}: "
            f"metadata['iteration_no']={meta_iteration_no!r} vs "
            f"group_attrs={group_attrs!r}"
        )


def test_iteration_1_standalone_block_carries_iteration_no_metadata():
    """Iteration 1 block has metadata['iteration_no'] but no group_attrs;
    invariant doesn't apply (one operand missing) so the assertion below
    is just that the metadata key IS set."""
    block = _goal_current_iteration_block(
        _FakeIteration(id="i1", sequence_no=1, goal="goal text")
    )
    assert block.metadata["iteration_no"] == "1"
    # No group_attrs on the standalone block — invariant vacuously holds.
    _assert_invariant([block])


def test_iteration_N_current_child_pairs_metadata_and_group_attrs():
    """Iteration N≥2 ``<iteration_goal>`` child has BOTH metadata and
    group_attrs ``iteration_no``; they must agree."""
    block = _current_iteration_goal_child(
        _FakeIteration(id="i7", sequence_no=7, goal="iter 7")
    )
    assert block.metadata["iteration_no"] == "7"
    assert 'iteration_no="7"' in block.metadata["group_attrs"]
    _assert_invariant([block])


@pytest.mark.parametrize("seq_no", [1, 2, 3, 5, 12, 99])
def test_iteration_no_invariant_holds_for_every_sequence_no(seq_no: int):
    block = _current_iteration_goal_child(
        _FakeIteration(id="i", sequence_no=seq_no, goal="g")
    )
    _assert_invariant([block])


def test_invariant_catches_planted_drift():
    """A hand-mutated block with mismatched fields must trip the invariant."""
    block = _current_iteration_goal_child(
        _FakeIteration(id="i", sequence_no=3, goal="g")
    )
    # Plant the drift by rebuilding the model with a mutated metadata dict.
    bad_metadata = dict(block.metadata)
    bad_metadata["iteration_no"] = "9999"  # group_attrs still has iteration_no="3"
    bad = block.model_copy(update={"metadata": bad_metadata})
    with pytest.raises(AssertionError, match="iteration_no drift"):
        _assert_invariant([bad])


def test_goal_iteration_blocks_full_frame_invariant():
    """The full Iteration N≥2 frame from ``goal_iteration_blocks``:
    standalone ``<goal>`` + prior iteration groups + current iteration goal."""
    goal = _FakeGoal(id="g", goal="overall goal")
    prior = _FakeIteration(
        id="i1",
        sequence_no=1,
        goal="iter 1 goal",
        plan_spec="prior plan",
        task_summary="prior summary",
    )
    current = _FakeIteration(id="i2", sequence_no=2, goal="iter 2 goal")
    blocks = goal_iteration_blocks(
        goal=goal,
        current_iteration=current,
        iterations=[prior, current],
    )
    _assert_invariant(blocks)


def test_attempt_plan_with_deferred_goal_block_carries_metadata():
    """The partial-plan handoff child is the signal carrier for the
    evaluator task-guidance branch. Its presence is what flips the
    branch; the AC #17 invariant doesn't apply to has_deferred_goal_for_next_iteration (it's a
    single field, not a paired one)."""
    attempt = _FakeAttempt(
        id="a",
        plan_spec="plan body",
        deferred_goal_for_next_iteration="future work",
    )
    blocks = attempt_plan_blocks(attempt, priority=ContextPriority.REQUIRED)
    handoffs = [b for b in blocks if b.metadata.get("child_tag") == "deferred_goal_for_next_iteration"]
    assert len(handoffs) == 1
    assert handoffs[0].metadata["has_deferred_goal_for_next_iteration"] == "true"


def test_attempt_plan_without_deferred_goal_has_no_block():
    """Closes-goal attempts (no handoff) do not emit the handoff child,
    so the partial branch never fires from a packet that has only a
    plan_spec block."""
    attempt = _FakeAttempt(
        id="a",
        plan_spec="plan body",
        deferred_goal_for_next_iteration=None,
    )
    blocks = attempt_plan_blocks(attempt, priority=ContextPriority.REQUIRED)
    assert all(b.metadata.get("has_deferred_goal_for_next_iteration") != "true" for b in blocks)
