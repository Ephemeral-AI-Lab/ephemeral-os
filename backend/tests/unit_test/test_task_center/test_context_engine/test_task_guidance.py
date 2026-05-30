"""Registry-driven ``<Task Guidance>`` builder behaviour.

The single :func:`build_task_guidance` composes its two labelled sections
deterministically:

* ``What's in context:`` — outline produced by :func:`render_context_outline`
  from the packet alone (no per-role branching).
* ``What to do:`` — one line lifted from :data:`AGENT_DIRECTIVES` by exact
  agent name.

The explorer builder remains a standalone helper for subagent-launch paths
that bypass the composer; it takes no arguments.
"""

from __future__ import annotations

import inspect

import pytest

from agents import AgentDefinition, AgentRole
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.agent_directives import AGENT_DIRECTIVES
from task_center.context_engine.task_guidance import (
    build_explorer_task_guidance,
    build_task_guidance,
)


def _agent_def(name: str, kind: AgentRole = AgentRole.PLANNER) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description=name,
        role=kind,
        terminals=["submit_x"],
        tool_call_limit=10,
    )


def _goal_block() -> ContextBlock:
    return ContextBlock(
        kind="goal_statement",
        priority=ContextPriority.REQUIRED,
        text="goal body",
        metadata={"tag": "goal"},
    )


def _iteration_goal_block(seq_no: int) -> ContextBlock:
    return ContextBlock(
        kind="iteration_statement",
        priority=ContextPriority.REQUIRED,
        text="(identical to &lt;goal&gt;)",
        metadata={
            "group_id": f"iteration_{seq_no}_current",
            "group_tag": "iteration",
            "group_attrs": f'iteration_no="{seq_no}" position="current"',
            "child_tag": "iteration_goal",
            "iteration_no": str(seq_no),
        },
    )


def _prior_attempt_block() -> ContextBlock:
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


def _needs_block(dep_id: str = "dep-a") -> ContextBlock:
    return ContextBlock(
        kind="dependency_summary",
        priority=ContextPriority.MEDIUM,
        text="dep output",
        metadata={
            "group_id": "needs",
            "group_tag": "needs",
            "child_tag": "task",
            "attrs": f'id="{dep_id}" status="success"',
        },
    )


def _assigned_task_block() -> ContextBlock:
    return ContextBlock(
        kind="planned_task_spec",
        priority=ContextPriority.REQUIRED,
        text="task body",
        metadata={"tag": "assigned_task", "attrs": 'task_id="t1"'},
    )


def _assigned_prompt_block() -> ContextBlock:
    return ContextBlock(
        kind="planned_task_spec",
        priority=ContextPriority.REQUIRED,
        text="reduce body",
        metadata={"tag": "assigned_prompt", "attrs": 'task_id="t1"'},
    )


def _packet(blocks: list[ContextBlock]) -> ContextPacket:
    return ContextPacket(
        target_role="planner",
        canonical_refs=ContextRefs(),
        blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Section composition.
# ---------------------------------------------------------------------------


def test_planner_iter1_fresh_outline():
    prose = build_task_guidance(
        agent_def=_agent_def("planner"),
        packet=_packet([_goal_block(), _iteration_goal_block(1)]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "What's in context:" in prose
    assert "- <goal> — user's request" in prose
    assert '- <iteration position="current"> — active iteration' in prose
    assert "  - <iteration_goal> — active iteration's scope" in prose
    assert "What to do:\n- Plan for <iteration_goal>." in prose


def test_planner_iter1_after_failure_outline():
    prose = build_task_guidance(
        agent_def=_agent_def("planner"),
        packet=_packet(
            [_goal_block(), _iteration_goal_block(1), _prior_attempt_block()]
        ),
        scope=None,  # type: ignore[arg-type]
    )
    assert "  - <attempt> — failed prior attempt" in prose


def test_executor_outline_with_needs():
    prose = build_task_guidance(
        agent_def=_agent_def("executor", AgentRole.GENERATOR),
        packet=_packet([_needs_block(), _assigned_task_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    # The generator drops <plan_spec>; it opens on its <needs> group and ends
    # on its <assigned_task>.
    assert "- <needs> — upstream needs output" in prose
    assert "- <assigned_task> — your assigned task" in prose
    assert "Complete <assigned_task>." in prose


def test_planner_directive_is_terminal_agnostic():
    prose = build_task_guidance(
        agent_def=_agent_def("planner"),
        packet=_packet([_goal_block(), _iteration_goal_block(1)]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "What to do:\n- Plan for <iteration_goal>." in prose


def test_reducer_outline_is_needs_then_assigned_prompt():
    """The reducer outline is its <needs> group followed by its
    <assigned_prompt> — no goal/iteration frame, no attempt-wide plan."""
    prose = build_task_guidance(
        agent_def=_agent_def("reducer", AgentRole.REDUCER),
        packet=_packet([_needs_block(), _assigned_prompt_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "- <needs> — upstream needs output" in prose
    assert "- <assigned_prompt> — your reducer prompt" in prose
    assert "<attempt" not in prose
    assert "Digest your <needs> and gate against <assigned_prompt>." in prose


def test_unknown_agent_raises():
    with pytest.raises(KeyError, match="AGENT_DIRECTIVES"):
        build_task_guidance(
            agent_def=_agent_def("nonexistent"),
            packet=_packet([_goal_block()]),
            scope=None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Explorer subagent — static prose, no inputs, no branches.
# ---------------------------------------------------------------------------


def test_explorer_static_prose_uses_role_directive():
    prose = build_explorer_task_guidance()
    assert AGENT_DIRECTIVES["explorer"] in prose
    assert "submit_exploration_result" in prose


def test_explorer_takes_no_arguments():
    sig = inspect.signature(build_explorer_task_guidance)
    assert list(sig.parameters) == []


# ---------------------------------------------------------------------------
# Dispatch signature contract.
# ---------------------------------------------------------------------------


def test_composer_dispatch_signature():
    sig = inspect.signature(build_task_guidance)
    params = list(sig.parameters)
    assert params == ["agent_def", "packet", "scope"], params
    for name, param in sig.parameters.items():
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"build_task_guidance.{name} must be keyword-only"
        )
