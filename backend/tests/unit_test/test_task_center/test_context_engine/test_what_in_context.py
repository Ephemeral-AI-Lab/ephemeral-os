"""Outline behaviour of :func:`render_what_in_context`."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.what_in_context import render_what_in_context


def _packet(blocks: list[ContextBlock]) -> ContextPacket:
    return ContextPacket(
        target_role="planner",
        canonical_refs=ContextRefs(),
        blocks=blocks,
    )


def _goal() -> ContextBlock:
    return ContextBlock(
        kind="goal_statement",
        priority=ContextPriority.REQUIRED,
        text="goal body",
        metadata={"tag": "goal"},
    )


def _iter_current_goal(seq_no: int = 1) -> ContextBlock:
    return ContextBlock(
        kind="iteration_statement",
        priority=ContextPriority.REQUIRED,
        text="(identical to &lt;goal&gt;)",
        metadata={
            "group_id": f"iteration_{seq_no}_current",
            "group_tag": "iteration",
            "group_attrs": f'iteration_no="{seq_no}" status="current"',
            "child_tag": "iteration_goal",
        },
    )


def _prior_attempt() -> ContextBlock:
    return ContextBlock(
        kind="failed_attempt",
        priority=ContextPriority.HIGH,
        text="(failed body)",
        metadata={
            "group_id": "iteration_1_current",
            "group_tag": "iteration",
            "group_attrs": 'iteration_no="1" status="current"',
            "child_tag": "attempt",
            "attrs": 'attempt_no="1" status="prior" verdict="fail"',
            "pre_rendered_xml": "true",
        },
    )


def _current_attempt() -> ContextBlock:
    return ContextBlock(
        kind="failed_attempt",
        priority=ContextPriority.REQUIRED,
        text="(current body)",
        metadata={
            "group_id": "iteration_1_current",
            "group_tag": "iteration",
            "group_attrs": 'iteration_no="1" status="current"',
            "child_tag": "attempt",
            "attrs": 'attempt_no="2" status="current"',
            "pre_rendered_xml": "true",
        },
    )


def _prior_iteration_pair(seq_no: int) -> list[ContextBlock]:
    return [
        ContextBlock(
            kind="prior_iteration_specification",
            priority=ContextPriority.HIGH,
            text="plan",
            metadata={
                "group_id": f"iteration_{seq_no}_prior",
                "group_tag": "iteration",
                "group_attrs": f'iteration_no="{seq_no}" status="prior"',
                "child_tag": "accepted_plan",
            },
        ),
        ContextBlock(
            kind="prior_iteration_summary",
            priority=ContextPriority.HIGH,
            text="summary",
            metadata={
                "group_id": f"iteration_{seq_no}_prior",
                "group_tag": "iteration",
                "group_attrs": f'iteration_no="{seq_no}" status="prior"',
                "child_tag": "summary",
            },
        ),
    ]


def test_case02_planner_iter1_fresh_outline():
    outline = render_what_in_context(_packet([_goal(), _iter_current_goal(1)]))
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration status=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope"
    )


def test_case03_planner_iter1_with_prior_attempt():
    outline = render_what_in_context(
        _packet([_goal(), _iter_current_goal(1), _prior_attempt()])
    )
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration status=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope\n"
        "  - <attempt status=\"prior\" verdict=\"fail\"> — failed prior attempt"
    )


def test_case07_evaluator_iter1_two_attempts_collapses_correctly():
    # Two attempts, distinct descriptors (prior/fail and current) — both
    # appear; they do NOT collapse because the descriptors differ.
    outline = render_what_in_context(
        _packet(
            [
                _goal(),
                _iter_current_goal(1),
                _prior_attempt(),
                _current_attempt(),
            ]
        )
    )
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration status=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope\n"
        "  - <attempt status=\"prior\" verdict=\"fail\"> — failed prior attempt\n"
        "  - <attempt status=\"current\"> — active attempt"
    )


def test_case04_planner_iter2_prior_and_current():
    blocks = [_goal()] + _prior_iteration_pair(1) + [_iter_current_goal(2)]
    outline = render_what_in_context(_packet(blocks))
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration status=\"prior\"> — previous iteration's work\n"
        "  - <accepted_plan> — prior iteration's accepted plan\n"
        "  - <summary> — prior iteration's summary\n"
        "- <iteration status=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope"
    )


def test_consecutive_same_descriptor_siblings_collapse():
    """Two prior failed attempts under the same iteration collapse to one bullet."""
    a1 = _prior_attempt()
    a2 = _prior_attempt().model_copy(
        update={
            "metadata": {
                **_prior_attempt().metadata,
                "attrs": 'attempt_no="2" status="prior" verdict="fail"',
            },
            "source_id": "att-2",
        }
    )
    outline = render_what_in_context(
        _packet([_goal(), _iter_current_goal(1), a1, a2])
    )
    # Only one bullet for the two prior-failed attempts.
    assert outline.count('<attempt status="prior" verdict="fail">') == 1


def test_executor_outline_with_plan_spec_dependency_and_assigned_task():
    blocks = [
        ContextBlock(
            kind="task_specification",
            priority=ContextPriority.HIGH,
            text="plan body",
            metadata={"tag": "plan_spec"},
        ),
        ContextBlock(
            kind="dependency_summary",
            priority=ContextPriority.MEDIUM,
            text="dep",
            metadata={"tag": "dependency", "attrs": 'id="t-a"'},
        ),
        ContextBlock(
            kind="planned_task_spec",
            priority=ContextPriority.REQUIRED,
            text="task",
            metadata={"tag": "assigned_task", "attrs": 'task_id="t-b"'},
        ),
    ]
    outline = render_what_in_context(_packet(blocks))
    assert outline == (
        "- <plan_spec> — attempt's plan\n"
        "- <dependency> — upstream task output\n"
        "- <assigned_task> — your assigned task"
    )


def test_entry_request_outline():
    block = ContextBlock(
        kind="entry_request",
        priority=ContextPriority.REQUIRED,
        text="request body",
        metadata={"tag": "entry_request"},
    )
    outline = render_what_in_context(_packet([block]))
    assert outline == "- <entry_request> — root delegation envelope"


def test_unknown_tag_is_skipped():
    block = ContextBlock(
        kind="custom_kind",
        priority=ContextPriority.LOW,
        text="x",
        metadata={"tag": "nonexistent_tag"},
    )
    assert render_what_in_context(_packet([block])) == ""


def test_attempt_does_not_recurse_into_body():
    """<attempt> is NOT in RECURSE_THROUGH; its body details stay in XML."""
    outline = render_what_in_context(
        _packet([_goal(), _iter_current_goal(1), _prior_attempt()])
    )
    # No child bullets for <plan_spec>, <status_summary>, etc.
    assert "plan_spec" not in outline
    assert "status_summary" not in outline
