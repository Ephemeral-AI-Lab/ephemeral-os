"""Diagram-fidelity tests for the role-scoped context redesign.

Renders the planner / generator / reducer / handoff contexts from
production-shape task ids (``<attempt>:gen:<local_id>`` / ``:red:<local_id>``,
which exercise the local-id derivation the diagrams hinge on) and asserts the
rendered ``<context>`` body byte-for-byte.

The redesign drops ``<plan_spec>`` (the planner distributes framing into each
task spec) and the evaluator role; the dependency wrapper is now ``<needs>`` and
the reducer's own prompt renders as ``<assigned_prompt>``. The planner's failed
attempt body is the failed plan tasks' ``<task>``s + a ``<failure>`` line — no
``<evaluator_summary>``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from task_center._core.outcomes import Outcome, to_record
from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
    Iteration,
    IterationCreationReason,
    IterationStatus,
    Workflow,
    WorkflowStatus,
)
from task_center.agent_launch.composer import _wrap_context
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes._needs import needs_outcome_blocks
from task_center.context_engine.recipes._task_xml import render_task_element
from task_center.context_engine.recipes.planner import (
    _failed_attempt_blocks,
    _goal_iteration_blocks,
)
from task_center.context_engine.renderer import XmlPromptRenderer

_NOW = datetime(2026, 5, 29, tzinfo=UTC)


class _FakeTaskStore:
    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def get_task(self, task_id: str):
        return self._rows.get(task_id)


def _goal() -> Workflow:
    return Workflow(
        id="g1",
        task_center_run_id="run1",
        workflow_goal="Build a CLI todo app.",
        status=WorkflowStatus.OPEN,
        iteration_ids=(),
        parent_task_id=None,
        created_at=_NOW,
        updated_at=_NOW,
        closed_at=None,
    )


def _iteration(
    seq: int, status: IterationStatus, goal_text: str, outcomes: str | None = None
) -> Iteration:
    return Iteration(
        id=f"it{seq}",
        workflow_id="g1",
        sequence_no=seq,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal=goal_text,
        attempt_budget=2,
        status=status,
        attempt_ids=(),
        deferred_goal_for_next_iteration=None,
        created_at=_NOW,
        updated_at=_NOW,
        closed_at=_NOW if status is not IterationStatus.OPEN else None,
        outcomes=outcomes,
    )


def _attempt(
    *,
    status: AttemptStatus,
    fail_reason: AttemptFailReason | None = None,
    generator_task_ids: tuple[str, ...] = (),
    reducer_task_ids: tuple[str, ...] = (),
) -> Attempt:
    return Attempt(
        id="att1",
        iteration_id="it2",
        attempt_sequence_no=1,
        stage=AttemptStage.CLOSED,
        status=status,
        planner_task_id="att1:planner",
        generator_task_ids=generator_task_ids,
        reducer_task_ids=reducer_task_ids,
        deferred_goal_for_next_iteration=None,
        fail_reason=fail_reason,
        created_at=_NOW,
        updated_at=_NOW,
        closed_at=_NOW,
    )


def _render(blocks: list[ContextBlock], *, role: str) -> str:
    packet = ContextPacket(
        target_role=role,
        target_id="att1",
        canonical_refs=ContextRefs(workflow_id="g1", iteration_id="it2", attempt_id="att1"),
        blocks=blocks,
        source_ids=[],
    )
    return _wrap_context(XmlPromptRenderer().render_context(packet))


_PRIOR_OUTCOMES = json.dumps(
    [
        to_record(Outcome(local_id="storage", status="success", outcome="Implemented storage layer.")),
        to_record(Outcome(local_id="cli_add", status="success", outcome="Added the add command.")),
    ]
)


# ---------------------------------------------------------------------------
# Planner — prior iteration + current iteration with one failed attempt.
# ---------------------------------------------------------------------------


def test_planner_context_matches_diagram():
    """Planner: <goal> + <iteration position="prior"> of <task> + current
    <iteration> whose <iteration_goal> precedes an <attempt attempt_no="1"> body
    of <task>s + <failure>."""
    it1 = _iteration(1, IterationStatus.SUCCEEDED, "iteration 1 goal", _PRIOR_OUTCOMES)
    it2 = _iteration(2, IterationStatus.OPEN, "Add list and done commands.")
    failed = _attempt(
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.TASK_FAILED,
        generator_task_ids=("att1:gen:cli_list", "att1:gen:cli_done"),
    )
    store = _FakeTaskStore(
        {
            "att1:gen:cli_list": {"status": "done", "outcomes": [{"outcome": "Implemented list command."}]},
            "att1:gen:cli_done": {
                "status": "failed",
                "outcomes": [{"outcome": "done command crashed on empty store."}],
            },
        }
    )
    blocks = _goal_iteration_blocks(workflow=_goal(), current_iteration=it2, iterations=[it1, it2])
    blocks += _failed_attempt_blocks(
        current_attempt_id="att2", iteration=it2, attempts=[failed], task_store=store
    )

    expected = (
        "<context>\n"
        "<goal>\n"
        "Build a CLI todo app.\n"
        "</goal>\n"
        "\n"
        '<iteration iteration_no="1" position="prior">\n'
        '<task id="storage" status="success">\n'
        "Implemented storage layer.\n"
        "</task>\n"
        '<task id="cli_add" status="success">\n'
        "Added the add command.\n"
        "</task>\n"
        "</iteration>\n"
        "\n"
        '<iteration iteration_no="2" position="current">\n'
        "<iteration_goal>\n"
        "Add list and done commands.\n"
        "</iteration_goal>\n"
        '<attempt attempt_no="1">\n'
        '<task id="cli_list" status="success">\n'
        "Implemented list command.\n"
        "</task>\n"
        '<task id="cli_done" status="failure">\n'
        "done command crashed on empty store.\n"
        "</task>\n"
        "<failure>\n"
        "generator cli_done: done command crashed on empty store.\n"
        "</failure>\n"
        "</attempt>\n"
        "</iteration>\n"
        "</context>\n"
    )
    assert _render(blocks, role="planner") == expected


# ---------------------------------------------------------------------------
# Generator — <needs> wrapper of <task> + assigned_task (no plan_spec).
# ---------------------------------------------------------------------------


def test_generator_context_matches_diagram():
    store = _FakeTaskStore(
        {
            "att1:gen:storage": {"status": "done", "outcomes": [{"outcome": "Implemented storage layer."}]},
            "att1:gen:cli_add": {"status": "done", "outcomes": [{"outcome": "Added the add command."}]},
        }
    )
    blocks: list[ContextBlock] = list(
        needs_outcome_blocks(
            needs=("att1:gen:storage", "att1:gen:cli_add"), task_store=store
        )
    )
    blocks.append(
        ContextBlock(
            kind=ContextBlockKind.PLANNED_TASK_SPEC,
            priority=ContextPriority.REQUIRED,
            text="Implement the done command.",
            source_id="att1:gen:cli_done",
            source_kind="task_center_task",
            metadata={"tag": "assigned_task", "attrs": 'task_id="cli_done"'},
        )
    )
    expected = (
        "<context>\n"
        "<needs>\n"
        '<task id="storage" status="success">\n'
        "Implemented storage layer.\n"
        "</task>\n"
        '<task id="cli_add" status="success">\n'
        "Added the add command.\n"
        "</task>\n"
        "</needs>\n"
        "\n"
        '<assigned_task task_id="cli_done">\n'
        "Implement the done command.\n"
        "</assigned_task>\n"
        "</context>\n"
    )
    assert _render(blocks, role="generator") == expected


# ---------------------------------------------------------------------------
# Reducer — <needs> wrapper of <task> + assigned_prompt (its own prompt only).
# ---------------------------------------------------------------------------


def test_reducer_context_matches_diagram():
    store = _FakeTaskStore(
        {
            "att1:gen:storage": {"status": "done", "outcomes": [{"outcome": "Implemented storage layer."}]},
            "att1:gen:cli_add": {"status": "done", "outcomes": [{"outcome": "Added the add command."}]},
        }
    )
    blocks: list[ContextBlock] = list(
        needs_outcome_blocks(
            needs=("att1:gen:storage", "att1:gen:cli_add"), task_store=store
        )
    )
    blocks.append(
        ContextBlock(
            kind=ContextBlockKind.PLANNED_TASK_SPEC,
            priority=ContextPriority.REQUIRED,
            text="Confirm every command works end to end.",
            source_id="att1:red:gate",
            source_kind="task_center_task",
            metadata={"tag": "assigned_prompt", "attrs": 'task_id="att1:red:gate"'},
        )
    )
    expected = (
        "<context>\n"
        "<needs>\n"
        '<task id="storage" status="success">\n'
        "Implemented storage layer.\n"
        "</task>\n"
        '<task id="cli_add" status="success">\n'
        "Added the add command.\n"
        "</task>\n"
        "</needs>\n"
        "\n"
        '<assigned_prompt task_id="att1:red:gate">\n'
        "Confirm every command works end to end.\n"
        "</assigned_prompt>\n"
        "</context>\n"
    )
    assert _render(blocks, role="reducer") == expected


# ---------------------------------------------------------------------------
# Handoff — nested <task> roll-up (success + failure).
# ---------------------------------------------------------------------------


def test_handoff_success_nested_task_matches_diagram():
    parent = Outcome(
        local_id="implement_auth",
        status="success",
        outcome=None,
        children=(
            Outcome(local_id="schema", status="success", outcome="Designed the schema."),
            Outcome(local_id="login_api", status="success", outcome="Built the login API."),
            Outcome(local_id="session_mw", status="success", outcome="Added session middleware."),
        ),
    )
    assert render_task_element(parent) == (
        '<task id="implement_auth" status="success">\n'
        '<task id="schema" status="success">\n'
        "Designed the schema.\n"
        "</task>\n"
        '<task id="login_api" status="success">\n'
        "Built the login API.\n"
        "</task>\n"
        '<task id="session_mw" status="success">\n'
        "Added session middleware.\n"
        "</task>\n"
        "</task>"
    )


def test_handoff_failure_nested_task_matches_diagram():
    parent = Outcome(
        local_id="implement_auth",
        status="failure",
        outcome=None,
        children=(
            Outcome(local_id="schema", status="success", outcome="Designed the schema."),
            Outcome(local_id="login_api", status="failure", outcome="Login API failed on token refresh."),
        ),
        failure="generator login_api: token refresh raised.",
    )
    assert render_task_element(parent) == (
        '<task id="implement_auth" status="failure">\n'
        '<task id="schema" status="success">\n'
        "Designed the schema.\n"
        "</task>\n"
        '<task id="login_api" status="failure">\n'
        "Login API failed on token refresh.\n"
        "</task>\n"
        "<failure>\n"
        "generator login_api: token refresh raised.\n"
        "</failure>\n"
        "</task>"
    )
