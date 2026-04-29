"""Stage 4 — advisor pre-hook layer.

Pinned by the roadmap as: "New tests in
backend/tests/test_task_center/test_advisor_pre_hook.py cover accept,
reject, payload-drift, intervening-tool-call, missing-accept."

Phase 1 covers accept / reject / payload-drift / missing-accept; the
intervening-tool-call check is a Phase 2 deferral (the pre-hook layer
documents this). Stage 4 also pins the advisor agent surface
(definition + lifecycle + AdvisorLaunchContext) and the
``record_accept`` / ``check_advisor_accept`` helpers.
"""

from __future__ import annotations

import pytest

from task_center.harness_agents.advisor import ADVISOR, AdvisorLaunchContext
from task_center.harness_agents.advisor.lifecycle import (
    decode_verdict,
    encode_verdict,
)
from task_center.model import Status
from task_center.runtime import TaskCenter
from task_center.runtime.pre_hooks import (
    AdvisorAccept,
    BlockedTerminal,
    check_advisor_accept,
    get_accept,
    record_accept,
)


def _new_tc() -> TaskCenter:
    return TaskCenter()


# ---- Advisor surface area ---------------------------------------------------


def test_advisor_definition_terminals_only_feedback() -> None:
    """The advisor agent has a single terminal — submit_advisor_feedback."""
    assert ADVISOR.role == "advisor"
    assert ADVISOR.terminals == ["submit_advisor_feedback"]
    assert ADVISOR.allowed_tools == []


def test_advisor_launch_context_renders_proposal_and_caller_context() -> None:
    ctx = AdvisorLaunchContext(
        caller_id="planner-1",
        proposed_terminal_tool="submit_full_plan",
        proposed_input={"task_dep_graphs": [{"id": "a"}]},
        agent_reason="canary needs to land first",
        calling_agent_context="root_goal: migrate v1→v2",
    )
    rendered = ctx.to_advisor_prompt()
    assert "submit_full_plan" in rendered
    assert "canary needs to land first" in rendered
    assert "root_goal: migrate v1→v2" in rendered
    assert "task_dep_graphs" in rendered


def test_decode_verdict_round_trips_encode_verdict() -> None:
    assert decode_verdict(encode_verdict("accept", "looks good")) == (
        "accept",
        "looks good",
    )
    assert decode_verdict(encode_verdict("reject", "missing exit code")) == (
        "reject",
        "missing exit code",
    )


def test_decode_verdict_handles_malformed_text() -> None:
    verdict, reason = decode_verdict("garbage no pipe")
    assert verdict == "reject"
    assert "malformed" in reason


def test_decode_verdict_handles_unknown_verdict_word() -> None:
    verdict, reason = decode_verdict("maybe|some reason")
    assert verdict == "reject"
    assert "unknown verdict" in reason


# ---- TaskCenter.create_advisor + _create_advisor primitive ----------------


def test_create_advisor_primitive_creates_ready_task() -> None:
    tc = _new_tc()
    caller = tc._create_executor(
        input="caller", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    advisor = tc._create_advisor(input="advisor input", caller_id=caller.id)
    assert advisor.role == "advisor"
    assert advisor.status is Status.READY
    assert advisor.task_center_harness_graph_id is None


def test_create_advisor_composer_synthesizes_input_and_returns_task() -> None:
    tc = _new_tc()
    caller = tc._create_executor(
        input="caller", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    advisor = tc.create_advisor(
        caller_id=caller.id,
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        agent_reason="need to plan",
        calling_agent_context="caller context",
    )
    assert advisor.role == "advisor"
    assert advisor.status is Status.READY
    assert "submit_full_plan" in advisor.input
    assert "caller context" in advisor.input


def test_submit_advisor_feedback_stores_verdict() -> None:
    tc = _new_tc()
    caller = tc._create_executor(
        input="caller", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    advisor = tc.create_advisor(
        caller_id=caller.id,
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        agent_reason="r",
        calling_agent_context="ctx",
    )
    tc.graph.transition(advisor.id, Status.RUNNING)
    tc.submit_advisor_feedback(advisor.id, "accept", "looks correct")

    assert tc.graph.get(advisor.id).status is Status.DONE
    assert tc.graph.get(advisor.id).summaries[-1].kind == "advisor_feedback"
    verdict, reason = decode_verdict(tc.graph.get(advisor.id).summaries[-1].text)
    assert verdict == "accept"
    assert reason == "looks correct"


# ---- Pre-hook semantics — accept / reject / drift / missing ---------------


def test_check_advisor_accept_passes_on_exact_match() -> None:
    tc = _new_tc()
    record_accept(
        tc,
        caller_id="planner-1",
        terminal_tool="submit_full_plan",
        proposed_input={"task_dep_graphs": [{"id": "a"}]},
        verdict="accept",
        reason="ok",
    )
    # Should not raise.
    check_advisor_accept(
        tc,
        "planner-1",
        "submit_full_plan",
        {"task_dep_graphs": [{"id": "a"}]},
    )


def test_check_advisor_accept_blocks_on_missing_accept() -> None:
    tc = _new_tc()
    with pytest.raises(BlockedTerminal, match="must consult advisor"):
        check_advisor_accept(tc, "planner-1", "submit_full_plan", {})


def test_check_advisor_accept_blocks_on_reject_verdict() -> None:
    tc = _new_tc()
    record_accept(
        tc,
        caller_id="planner-1",
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        verdict="reject",
        reason="missing canary",
    )
    with pytest.raises(BlockedTerminal, match="advisor rejected"):
        check_advisor_accept(tc, "planner-1", "submit_full_plan", {"x": 1})


def test_check_advisor_accept_blocks_on_terminal_mismatch() -> None:
    tc = _new_tc()
    record_accept(
        tc,
        caller_id="planner-1",
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        verdict="accept",
        reason="ok",
    )
    with pytest.raises(BlockedTerminal, match="advisor approved a different terminal"):
        check_advisor_accept(tc, "planner-1", "submit_partial_plan", {"x": 1})


def test_check_advisor_accept_blocks_on_payload_drift() -> None:
    tc = _new_tc()
    record_accept(
        tc,
        caller_id="planner-1",
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        verdict="accept",
        reason="ok",
    )
    with pytest.raises(BlockedTerminal, match="payload differs"):
        check_advisor_accept(
            tc, "planner-1", "submit_full_plan", {"x": 2}
        )


def test_get_accept_returns_recorded_token() -> None:
    tc = _new_tc()
    record_accept(
        tc,
        caller_id="planner-1",
        terminal_tool="submit_full_plan",
        proposed_input={"x": 1},
        verdict="accept",
        reason="ok",
    )
    accept = get_accept(tc, "planner-1")
    assert isinstance(accept, AdvisorAccept)
    assert accept.terminal_tool == "submit_full_plan"
    assert accept.verdict == "accept"
