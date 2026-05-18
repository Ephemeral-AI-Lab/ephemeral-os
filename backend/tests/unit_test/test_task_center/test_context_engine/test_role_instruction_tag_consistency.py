"""Parameterized tag-consistency invariant per ralplan §7.2.

For each role × each branch B of ``role_instruction.py``, build a packet that
triggers B's context conditions, render it, parse XML tag names from BOTH the
role-instruction text and the rendered context, and assert every tag the role
text mentions appears in the rendered output under B's matching conditions.

A naive "appears in at least one branch" check would pass spuriously when, for
example, the no-deps generator branch references ``<dependency_results>``
(which the recipe only emits when ``needs`` is truthy at generator.py:60-65).
The parameterization here is the same branching axes as ``role_instruction.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from task_center.context_engine.core import ContextEngineDeps
from task_center.context_engine.recipes.evaluator import _evaluator_build
from task_center.context_engine.recipes.generator import _generator_build
from task_center.context_engine.recipes.planner import _planner_build
from task_center.context_engine.recipes.role_instruction import (
    evaluator_instruction,
    generator_instruction,
    planner_instruction,
)
from task_center.context_engine.renderer import XmlPromptRenderer
from task_center.context_engine.scope import ContextScope
from task_center.attempt.state import (
    AttemptFailReason,
    AttemptStatus,
)
from task_center.iteration.state import IterationCreationReason


# Regex matches an XML tag mention like ``<attempt_plan>`` or ``<iteration status="prior">``
# anywhere inside the role-instruction text. Backtick-wrapped parameter names
# such as ``next_iteration_handoff_goal`` do NOT match (no leading ``<``).
_TAG_MENTION_RE = re.compile(r"<([a-z_]+)(?:\s[^>]*)?>")


def _mentioned_tags(text: str) -> set[str]:
    return set(_TAG_MENTION_RE.findall(text))


def _rendered_tags(rendered: str) -> set[str]:
    # Both openers (with optional attrs) and closers.
    opener_re = re.compile(r"<([a-z_]+)(?:\s[^>]*)?>")
    return set(opener_re.findall(rendered))


def _assert_text_tags_present_in_render(
    role_text: str, rendered: str, branch_label: str
) -> None:
    missing = _mentioned_tags(role_text) - _rendered_tags(rendered)
    assert not missing, (
        f"{branch_label}: role-instruction mentions tags not in rendered "
        f"context: {sorted(missing)}.\n--- role text ---\n{role_text}\n"
        f"--- rendered ---\n{rendered}"
    )


# ---------------------------------------------------------------------------
# Planner — 4 branches: (iter_no in {1, ≥2}) × (has_failed_attempts in {T, F})
# ---------------------------------------------------------------------------


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


def _seed_goal(goal_store, task_center_run_id, goal: str = "overall"):
    return goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t-entry",
        goal=goal,
    )


def _seed_iteration(iteration_store, *, goal_id: str, sequence_no: int, goal: str = "g"):
    return iteration_store.insert(
        goal_id=goal_id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        goal=goal,
        attempt_budget=4,
    )


def _close_iteration_succeeded(iteration_store, iteration_id, *, spec: str, summary: str):
    return iteration_store.close_succeeded(
        iteration_id,
        plan_spec=spec,
        task_summary=summary,
        closed_at=datetime.now(UTC),
    )


def _seed_failed_attempt(attempt_store, iteration_id, *, sequence_no: int):
    attempt = attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec=f"failed-{sequence_no}-spec",
        evaluation_criteria=[f"crit-{sequence_no}"],
        next_iteration_handoff_goal=None,
    )
    return attempt_store.close(
        attempt.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.PLANNER_FAILED,
        closed_at=datetime.now(UTC),
    )


def _seed_running_attempt(attempt_store, iteration_id, *, sequence_no: int):
    return attempt_store.insert(
        iteration_id=iteration_id, attempt_sequence_no=sequence_no
    )


@pytest.mark.parametrize(
    "iteration_no,has_failed_attempts,branch_label",
    [
        (1, False, "planner_iter1_no_failed"),
        (1, True, "planner_iter1_with_failed"),
        (3, False, "planner_iter3_no_failed"),
        (3, True, "planner_iter3_with_failed"),
    ],
)
def test_planner_branch_tags_match_rendered_context(
    deps,
    goal_store,
    iteration_store,
    attempt_store,
    task_center_run_id,
    iteration_no: int,
    has_failed_attempts: bool,
    branch_label: str,
):
    request = _seed_goal(goal_store, task_center_run_id)
    # Seed prior closed iterations for iteration_no >= 2.
    for prior_seq in range(1, iteration_no):
        prior = _seed_iteration(
            iteration_store,
            goal_id=request.id,
            sequence_no=prior_seq,
            goal=f"prior {prior_seq} goal",
        )
        _close_iteration_succeeded(
            iteration_store, prior.id, spec=f"prior-{prior_seq}-spec", summary=f"prior-{prior_seq}-sum"
        )
    current = _seed_iteration(
        iteration_store,
        goal_id=request.id,
        sequence_no=iteration_no,
        goal="current iteration goal",
    )
    if has_failed_attempts:
        _seed_failed_attempt(attempt_store, current.id, sequence_no=1)
        running = _seed_running_attempt(attempt_store, current.id, sequence_no=2)
    else:
        running = _seed_running_attempt(attempt_store, current.id, sequence_no=1)

    packet = _planner_build(
        ContextScope(
            goal_id=request.id, iteration_id=current.id, attempt_id=running.id
        ),
        deps,
    )
    rendered = XmlPromptRenderer().render_context(packet)
    role_text = planner_instruction(
        iteration_no=iteration_no, has_failed_attempts=has_failed_attempts
    ).text
    _assert_text_tags_present_in_render(role_text, rendered, branch_label)


# ---------------------------------------------------------------------------
# Generator — 2 branches: has_deps in {True, False}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "has_deps,branch_label",
    [
        (False, "generator_no_deps"),
        (True, "generator_with_deps"),
    ],
)
def test_generator_branch_tags_match_rendered_context(
    deps,
    goal_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
    has_deps: bool,
    branch_label: str,
):
    request = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1
    )
    attempt = attempt_store.insert(
        iteration_id=iteration.id, attempt_sequence_no=1
    )
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="plan",
        evaluation_criteria=["c1"],
        next_iteration_handoff_goal=None,
    )
    if has_deps:
        task_store.upsert_task(
            task_id="dep",
            task_center_run_id=task_center_run_id,
            role="generator",
            agent_name="executor",
            context_message="dep work",
            status="done",
            summaries=[{"summary": "produced something"}],
            needs=[],
            task_center_attempt_id=attempt.id,
            spawn_reason="attempt_generator",
        )
        needs = ["dep"]
    else:
        needs = []
    task_store.upsert_task(
        task_id="t-1",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="do thing",
        status="pending",
        summaries=[],
        needs=needs,
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    packet = _generator_build(
        ContextScope(
            goal_id=request.id, attempt_id=attempt.id, task_id="t-1"
        ),
        deps,
    )
    rendered = XmlPromptRenderer().render_context(packet)
    role_text = generator_instruction(has_deps=has_deps).text
    _assert_text_tags_present_in_render(role_text, rendered, branch_label)


# ---------------------------------------------------------------------------
# Evaluator — 2 branches: is_partial in {True, False}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_partial,branch_label",
    [
        (False, "evaluator_full"),
        (True, "evaluator_partial"),
    ],
)
def test_evaluator_branch_tags_match_rendered_context(
    deps,
    goal_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
    is_partial: bool,
    branch_label: str,
):
    request = _seed_goal(goal_store, task_center_run_id)
    iteration = _seed_iteration(
        iteration_store, goal_id=request.id, sequence_no=1
    )
    attempt = attempt_store.insert(
        iteration_id=iteration.id, attempt_sequence_no=1
    )
    attempt_store.set_plan_contract(
        attempt.id,
        plan_spec="plan body",
        evaluation_criteria=["c1", "c2"],
        next_iteration_handoff_goal="continue with X" if is_partial else None,
    )
    attempt_store.set_generator_task_ids(attempt.id, ["t-a"])
    task_store.upsert_task(
        task_id="t-a",
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="work",
        status="done",
        summaries=[{"summary": "done"}],
        needs=[],
        task_center_attempt_id=attempt.id,
        spawn_reason="attempt_generator",
    )

    packet = _evaluator_build(
        ContextScope(
            goal_id=request.id, iteration_id=iteration.id, attempt_id=attempt.id
        ),
        deps,
    )
    rendered = XmlPromptRenderer().render_context(packet)
    role_text = evaluator_instruction(is_partial=is_partial).text
    _assert_text_tags_present_in_render(role_text, rendered, branch_label)


# ---------------------------------------------------------------------------
# Partial-evaluator regression: two pinned sentences must survive verbatim.
# ---------------------------------------------------------------------------


def test_evaluator_partial_keeps_pinned_sentences():
    text = evaluator_instruction(is_partial=True).text
    assert (
        "make progress and hand off remaining work via "
        "`next_iteration_handoff_goal`"
    ) in text
    assert (
        "do not penalize for incomplete work that was explicitly deferred"
        in text
    )
