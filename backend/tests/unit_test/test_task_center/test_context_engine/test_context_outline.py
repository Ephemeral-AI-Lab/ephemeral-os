"""Outline behaviour of :func:`render_context_outline`."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.context_outline import render_context_outline


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
            "group_attrs": f'iteration_no="{seq_no}" position="current"',
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
            "group_attrs": 'iteration_no="1" position="current"',
            "child_tag": "attempt",
            "attrs": 'attempt_no="1"',
            "pre_rendered_xml": "true",
        },
    )


def _prior_iteration_pair(seq_no: int) -> list[ContextBlock]:
    """Prior iteration's denormalized achieved record: one ``prior_iteration_summary``
    block per entry, each a ``<task id status>`` child (contract §4)."""
    return [
        ContextBlock(
            kind="prior_iteration_summary",
            priority=ContextPriority.HIGH,
            text="Implemented storage layer.",
            metadata={
                "group_id": f"iteration_{seq_no}_prior",
                "group_tag": "iteration",
                "group_attrs": f'iteration_no="{seq_no}" position="prior"',
                "child_tag": "task",
                "attrs": 'id="storage" status="success"',
            },
        ),
        ContextBlock(
            kind="prior_iteration_summary",
            priority=ContextPriority.HIGH,
            text="Added the add command.",
            metadata={
                "group_id": f"iteration_{seq_no}_prior",
                "group_tag": "iteration",
                "group_attrs": f'iteration_no="{seq_no}" position="prior"',
                "child_tag": "task",
                "attrs": 'id="cli_add" status="success"',
            },
        ),
    ]


def test_case02_planner_iter1_fresh_outline():
    outline = render_context_outline(_packet([_goal(), _iter_current_goal(1)]))
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration position=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope"
    )


def test_case03_planner_iter1_with_prior_attempt():
    outline = render_context_outline(
        _packet([_goal(), _iter_current_goal(1), _prior_attempt()])
    )
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration position=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope\n"
        "  - <attempt> — failed prior attempt"
    )


# Removed test_case07_evaluator_iter1_two_attempts_collapses_correctly: its
# premise (two attempts render distinct descriptors and do NOT collapse) was
# invalidated by contract §5 — failed <attempt> now carries only attempt_no, so
# both attempts share one descriptor and collapse to a single bullet. Attempt
# collapse is covered by test_consecutive_same_descriptor_siblings_collapse.


def test_case04_planner_iter2_prior_and_current():
    blocks = [_goal()] + _prior_iteration_pair(1) + [_iter_current_goal(2)]
    outline = render_context_outline(_packet(blocks))
    # Prior iteration's two success <task> children share one descriptor and
    # collapse to a single bullet (contract §4: <task id status> children).
    assert outline == (
        "- <goal> — user's request\n"
        "- <iteration position=\"prior\"> — previous iteration's work\n"
        "  - <task status=\"success\"> — generator task outcome\n"
        "- <iteration position=\"current\"> — active iteration\n"
        "  - <iteration_goal> — active iteration's scope"
    )


def test_consecutive_same_descriptor_siblings_collapse():
    """Two prior failed attempts under the same iteration collapse to one bullet."""
    a1 = _prior_attempt()
    a2 = _prior_attempt().model_copy(
        update={
            "metadata": {
                **_prior_attempt().metadata,
                "attrs": 'attempt_no="2"',
            },
            "source_id": "att-2",
        }
    )
    outline = render_context_outline(
        _packet([_goal(), _iter_current_goal(1), a1, a2])
    )
    # Only one bullet for the two prior-failed attempts.
    assert outline.count("<attempt>") == 1


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
    outline = render_context_outline(_packet(blocks))
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
    outline = render_context_outline(_packet([block]))
    assert outline == "- <entry_request> — root delegation envelope"


def test_unknown_tag_is_skipped():
    block = ContextBlock(
        kind="custom_kind",
        priority=ContextPriority.LOW,
        text="x",
        metadata={"tag": "nonexistent_tag"},
    )
    assert render_context_outline(_packet([block])) == ""


def test_attempt_does_not_recurse_into_body():
    """<attempt> is NOT in RECURSE_THROUGH; its body details stay in XML."""
    outline = render_context_outline(
        _packet([_goal(), _iter_current_goal(1), _prior_attempt()])
    )
    # No child bullets for <plan_spec>, <status_summary>, etc.
    assert "plan_spec" not in outline
    assert "status_summary" not in outline
