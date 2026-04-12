"""Unit tests for the deterministic _posthook() in team.runtime.executor.Executor."""

from __future__ import annotations

from team.models import (
    AgentResult,
    Plan,
    ReplanPlan,
    ReplanRequest,
    RetryRequest,
    SubmittedSummary,
    TaskSpec,
)
from team.runtime.context_builder import TeamAgentContext
from team.runtime.executor import Executor


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class FakeDefn:
    """Minimal agent definition stub."""
    role = "developer"
    name = "developer"


class FakePlannerDefn:
    role = "planner"
    name = "team_planner"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(submitted_output=None, work_result=None) -> TeamAgentContext:
    meta: dict = {}
    if submitted_output is not None:
        meta["submitted_output"] = submitted_output
    if work_result is not None:
        meta["work_result"] = work_result
    return TeamAgentContext(tool_metadata=meta)


# ---------------------------------------------------------------------------
# submitted_output is a Plan
# ---------------------------------------------------------------------------


def test_posthook_with_plan_returns_agent_result_with_submitted_plan():
    plan = Plan(tasks=[TaskSpec(id="t1", task="do it", agent="developer")])
    ctx = _ctx(submitted_output=plan)
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.submitted_plan is plan
    assert result.submitted_replan is None


# ---------------------------------------------------------------------------
# submitted_output is a ReplanPlan
# ---------------------------------------------------------------------------


def test_posthook_with_replan_returns_agent_result_with_submitted_replan():
    replan = ReplanPlan(
        add_tasks=[TaskSpec(id="fix", task="fix bug", agent="developer")],
        cancel_ids=["old-1"],
    )
    ctx = _ctx(submitted_output=replan)
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.submitted_replan is replan
    assert result.submitted_plan is None


# ---------------------------------------------------------------------------
# submitted_output is a SubmittedSummary
# ---------------------------------------------------------------------------


def test_posthook_with_submitted_summary_returns_agent_result_with_summary():
    summary_obj = SubmittedSummary(summary="all tests passed")
    ctx = _ctx(submitted_output=summary_obj)
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "all tests passed"
    assert result.submitted_plan is None
    assert result.submitted_replan is None


# ---------------------------------------------------------------------------
# submitted_output is a RetryRequest
# ---------------------------------------------------------------------------


def test_posthook_with_retry_request_returns_retry_request_directly():
    retry = RetryRequest(reason="flaky test, retrying")
    ctx = _ctx(submitted_output=retry)
    result = Executor._posthook(ctx, FakeDefn())
    assert result is retry
    assert isinstance(result, RetryRequest)


# ---------------------------------------------------------------------------
# submitted_output is a ReplanRequest
# ---------------------------------------------------------------------------


def test_posthook_with_replan_request_returns_replan_request_directly():
    replan_req = ReplanRequest(reason="scope mismatch", suggestion="split task")
    ctx = _ctx(submitted_output=replan_req)
    result = Executor._posthook(ctx, FakeDefn())
    assert result is replan_req
    assert isinstance(result, ReplanRequest)


# ---------------------------------------------------------------------------
# No submission — role-aware fallbacks
# ---------------------------------------------------------------------------


def test_posthook_no_submission_planner_role_returns_sentinel():
    ctx = _ctx()  # no submitted_output
    result = Executor._posthook(ctx, FakePlannerDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "planner_did_not_submit_plan"


def test_posthook_no_submission_developer_with_work_result_uses_it():
    ctx = _ctx(work_result="test output here")
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "test output here"


def test_posthook_no_submission_work_result_truncated_to_2000_chars():
    long_result = "A" * 5000
    ctx = _ctx(work_result=long_result)
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert len(result.summary) == 2000
    assert result.summary == "A" * 2000


def test_posthook_no_submission_no_work_result_returns_default():
    ctx = _ctx()
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "completed (no explicit submission)"


def test_posthook_no_submission_empty_work_result_returns_default():
    ctx = _ctx(work_result="   ")  # whitespace only
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "completed (no explicit submission)"


# ---------------------------------------------------------------------------
# Unknown submitted_output type
# ---------------------------------------------------------------------------


def test_posthook_unknown_submitted_type_coerces_to_string():
    ctx = _ctx(submitted_output={"unexpected": "dict"})
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert "unexpected" in result.summary


# ---------------------------------------------------------------------------
# Edge cases with metadata
# ---------------------------------------------------------------------------


def test_posthook_empty_tool_metadata_dict():
    # TeamAgentContext with empty dict
    ctx = TeamAgentContext(tool_metadata={})
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == "completed (no explicit submission)"


def test_posthook_plan_has_empty_summary():
    plan = Plan(tasks=[])
    ctx = _ctx(submitted_output=plan)
    result = Executor._posthook(ctx, FakeDefn())
    assert isinstance(result, AgentResult)
    assert result.summary == ""
