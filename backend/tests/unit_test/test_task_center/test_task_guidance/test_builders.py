"""Task-guidance builder branch matrix (AC #13).

The composer dispatches by exact agent name and hands the builder
``(agent_def, packet, scope)``. Each builder branches on discrete
``block.kind`` / ``block.metadata`` signals embedded in the packet by
the recipe layer:

* planner: ``iteration_no`` × ``has_failed_attempts`` → 4 branches.
* generator: ``has_deps`` → 2 branches.
* evaluator: ``is_partial`` → 2 branches.
* explorer: static prose (no branches; no packet input either).

Builders never accept kwargs other than the dispatch trio. Branches
are tested via canned packets so the builders stay decoupled from the
real recipe machinery.
"""

from __future__ import annotations

import pytest

from agents import AgentDefinition, AgentKind
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.task_guidance.builders import (
    build_evaluator_task_guidance,
    build_explorer_task_guidance,
    build_generator_task_guidance,
    build_planner_task_guidance,
)


def _planner_def() -> AgentDefinition:
    return AgentDefinition(
        name="planner",
        description="planner",
        agent_kind=AgentKind.PLANNER,
    )


def _executor_def() -> AgentDefinition:
    return AgentDefinition(
        name="executor",
        description="executor",
        agent_kind=AgentKind.EXECUTOR,
    )


def _evaluator_def() -> AgentDefinition:
    return AgentDefinition(
        name="evaluator",
        description="evaluator",
        agent_kind=AgentKind.EVALUATOR,
    )


def _iteration_block(seq_no: int) -> ContextBlock:
    return ContextBlock(
        kind="iteration_statement",
        priority=ContextPriority.REQUIRED,
        text=f"iteration {seq_no} goal",
        metadata={"iteration_no": str(seq_no), "tag": "goal_current_iteration"},
    )


def _failed_attempt_block() -> ContextBlock:
    return ContextBlock(
        kind="failed_attempt_landscape",
        priority=ContextPriority.HIGH,
        text="(failed body)",
        metadata={
            "group_id": "iter_1_current",
            "group_tag": "iteration",
            "group_attrs": 'iteration_no="1" status="current"',
            "child_tag": "attempt",
            "attrs": 'attempt_no="1" status="failed"',
            "pre_rendered_xml": "true",
        },
    )


def _dep_block() -> ContextBlock:
    return ContextBlock(
        kind="dependency_summary",
        priority=ContextPriority.MEDIUM,
        text="dep output",
        metadata={
            "group_id": "deps",
            "group_tag": "dependency_results",
            "child_tag": "dependency",
            "attrs": 'id="dep-a"',
        },
    )


def _partial_handoff_block() -> ContextBlock:
    return ContextBlock(
        kind="task_specification",
        priority=ContextPriority.REQUIRED,
        text="future iteration work",
        metadata={
            "group_id": "attempt_plan_x",
            "group_tag": "attempt_plan",
            "child_tag": "next_iteration_handoff_goal",
            "is_partial": "true",
        },
    )


def _packet(blocks: list[ContextBlock]) -> ContextPacket:
    return ContextPacket(
        target_role="planner",
        canonical_refs=ContextRefs(),
        blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Planner — 4-branch matrix on (iteration_no == 1 vs >= 2) × has_failed_attempts.
# ---------------------------------------------------------------------------


def test_planner_iter1_no_failed_attempts():
    prose = build_planner_task_guidance(
        agent_def=_planner_def(),
        packet=_packet([_iteration_block(1)]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "planning the first attempt" in prose
    assert "No prior attempts exist in this iteration" in prose


def test_planner_iter1_with_failed_attempts():
    prose = build_planner_task_guidance(
        agent_def=_planner_def(),
        packet=_packet([_iteration_block(1), _failed_attempt_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "follow-up attempt for this iteration's goal" in prose
    assert "do not repeat a failing" in prose


def test_planner_iter2_no_failed_attempts():
    prose = build_planner_task_guidance(
        agent_def=_planner_def(),
        packet=_packet([_iteration_block(2)]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "first attempt for a later iteration" in prose
    assert "prior iteration produced" in prose


def test_planner_iter2_with_failed_attempts():
    prose = build_planner_task_guidance(
        agent_def=_planner_def(),
        packet=_packet([_iteration_block(2), _failed_attempt_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "follow-up attempt for a later iteration" in prose
    assert "Earlier iterations produced results" in prose


# ---------------------------------------------------------------------------
# Generator — 2-branch matrix on has_deps.
# ---------------------------------------------------------------------------


def test_generator_no_deps():
    prose = build_generator_task_guidance(
        agent_def=_executor_def(),
        packet=_packet([]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "no dependencies" in prose
    assert "<dependency_results>" not in prose


def test_generator_with_deps():
    prose = build_generator_task_guidance(
        agent_def=_executor_def(),
        packet=_packet([_dep_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "<dependency_results>" in prose
    assert "fixed inputs" in prose


# ---------------------------------------------------------------------------
# Evaluator — 2-branch matrix on is_partial.
# ---------------------------------------------------------------------------


def test_evaluator_complete_attempt():
    prose = build_evaluator_task_guidance(
        agent_def=_evaluator_def(),
        packet=_packet([]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "evaluating a complete attempt" in prose
    assert "intentionally partial" not in prose


def test_evaluator_partial_attempt():
    prose = build_evaluator_task_guidance(
        agent_def=_evaluator_def(),
        packet=_packet([_partial_handoff_block()]),
        scope=None,  # type: ignore[arg-type]
    )
    assert "intentionally partial attempt" in prose
    assert "next_iteration_handoff_goal" in prose


# ---------------------------------------------------------------------------
# Explorer — static prose, no inputs, no branches.
# ---------------------------------------------------------------------------


def test_explorer_static_prose():
    prose = build_explorer_task_guidance()
    assert "explorer subagent" in prose
    assert "submit_exploration_result" in prose


def test_explorer_takes_no_arguments():
    """The explorer builder is the only one that bypasses the composer
    (subagents have no scope), so it accepts no kwargs."""
    import inspect

    sig = inspect.signature(build_explorer_task_guidance)
    assert list(sig.parameters) == []


# ---------------------------------------------------------------------------
# Dispatch signature contract: builders accept (agent_def, packet, scope)
# via keyword only, no positional. The composer always passes kwargs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "builder",
    [
        build_planner_task_guidance,
        build_generator_task_guidance,
        build_evaluator_task_guidance,
    ],
)
def test_composer_dispatch_signature(builder):
    import inspect

    sig = inspect.signature(builder)
    params = list(sig.parameters)
    assert params == ["agent_def", "packet", "scope"], (
        f"{builder.__name__} signature drifted: {params}"
    )
    for name, param in sig.parameters.items():
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{builder.__name__}.{name} must be keyword-only"
        )
