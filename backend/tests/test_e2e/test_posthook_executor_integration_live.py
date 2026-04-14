# ruff: noqa
"""Live e2e tests — Executor _run_post_run integration.

Tests the posthook re-prompt flow end-to-end:

  1. Streaming runner — no in-loop submission, _run_post_run invokes
     runner.run() with posthook tools and maps RunResult → domain objects.
  2. Result mapping — each tool_name maps to the correct domain type
     (Plan, ReplanPlan, RetryRequest, ReplanRequest, BlockerDeclaration).
  3. No api_client → sentinel result.

Uses real LLM for the streaming runner tests.

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_posthook_executor_integration_live.py -v -m live -o "addopts="
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.types import AgentDefinition
from engine.testing.eval_agent import EvalAgent
from team.models import (
    AgentResult,
    BlockerDeclaration,
    Plan,
    ReplanPlan,
    ReplanRequest,
    RetryRequest,
)
from team.runtime.context_builder import TeamAgentContext
from team.runtime.executor import Executor
from tests.test_e2e.conftest import create_eval_agent
from tools.core.base import ExecutionMetadata

pytestmark = [pytest.mark.e2e, pytest.mark.live]

HAS_CREDENTIALS = EvalAgent.has_credentials()


# ---------------------------------------------------------------------------
# Minimal fakes — just enough to call _run_post_run directly
# ---------------------------------------------------------------------------


class FakeTeamRun:
    """Minimal team run stub for _run_post_run tests."""

    def __init__(self, api_client: Any = None) -> None:
        self.id = f"test-run-{uuid.uuid4().hex[:8]}"
        self.api_client = api_client
        self.conductor = None
        self.cancel_event = asyncio.Event()


def _make_ctx(
    *,
    role: str = "developer",
    agent_name: str = "developer",
    work_result: str | None = None,
) -> TeamAgentContext:
    """Build a TeamAgentContext with the given metadata."""
    meta = ExecutionMetadata()
    meta.extras["role"] = role
    meta.agent_name = agent_name
    meta.extras["agent_name"] = agent_name
    if work_result is not None:
        meta.extras["work_result"] = work_result
    ctx = TeamAgentContext(
        user_message="test task",
        tool_metadata=meta,
    )
    # PosthookTools.from_context(ctx) reads getattr(ctx, "metadata", {}).
    # TeamAgentContext only exposes tool_metadata, so alias it here.
    ctx.metadata = meta  # type: ignore[attr-defined]
    return ctx


def _make_defn(
    *,
    name: str = "developer",
    role: str = "developer",
    posthook: list[str] | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description=f"Test {name} agent",
        role=role,
        posthook=posthook or [],
    )


def _make_executor(api_client: Any = None) -> Executor:
    team_run = FakeTeamRun(api_client=api_client)
    return Executor(
        team_run=team_run,
        runner=lambda defn, ctx: asyncio.sleep(0),
        agent_lookup=lambda name: _make_defn(name=name),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent():
    if not HAS_CREDENTIALS:
        pytest.skip("No LLM credentials configured")
    return create_eval_agent()


@pytest.fixture(scope="module")
def api_client(agent):
    return agent.api_client


# ---------------------------------------------------------------------------
# Test 1: Streaming runner — developer post_note (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_streaming_runner_developer_post_note(api_client):
    """No in-loop submission → runner invokes LLM with developer posthook tools → post_note."""
    ctx = _make_ctx(role="developer", agent_name="developer")
    defn = _make_defn(name="developer", role="developer")
    executor = _make_executor(api_client=api_client)

    class FakeConductor:
        _executor_snapshots: dict[str, list[dict]] = {}
    conductor = FakeConductor()
    conductor._executor_snapshots["test-task"] = [
        {"role": "user", "content": (
            "Fix the broken import in pkg/io.py. Change load_defaults to get_defaults."
        )},
        {"role": "assistant", "content": (
            "I've fixed pkg/io.py: changed the import from load_defaults to get_defaults. "
            "All tests in pkg/tests/test_io.py pass."
        )},
    ]
    executor.team_run.conductor = conductor

    @dataclass
    class FakeTask:
        id: str = "test-task"
        agent_name: str = "developer"

    result = await executor._run_post_run(task=FakeTask(), defn=defn, ctx=ctx)

    assert isinstance(result, AgentResult)
    assert result.submitted_plan is None, "Developer should not submit a plan"
    assert result.submitted_replan is None, "Developer should not submit a replan"
    assert len(result.summary) > 10, f"Summary too short: {result.summary}"


# ---------------------------------------------------------------------------
# Test 2: Streaming runner — planner submit_plan (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_streaming_runner_planner_submit_plan(api_client):
    """No in-loop submission → runner invokes LLM with planner posthook → submit_plan."""
    ctx = _make_ctx(role="planner", agent_name="team_planner")
    defn = _make_defn(name="team_planner", role="planner")
    executor = _make_executor(api_client=api_client)

    class FakeConductor:
        _executor_snapshots: dict[str, list[dict]] = {}
    conductor = FakeConductor()
    conductor._executor_snapshots["plan-task"] = [
        {"role": "user", "content": (
            "Decompose: Fix the authentication module. It has three files: "
            "src/auth/login.py, src/auth/session.py, src/auth/middleware.py. "
            "Each needs independent fixes. Use agent 'developer'."
        )},
        {"role": "assistant", "content": (
            "I've analyzed the auth module. Three independent concerns — "
            "login, session, middleware — can each be a separate developer task "
            "with no cross-dependencies."
        )},
    ]
    executor.team_run.conductor = conductor

    @dataclass
    class FakeTask:
        id: str = "plan-task"
        agent_name: str = "team_planner"

    result = await executor._run_post_run(task=FakeTask(), defn=defn, ctx=ctx)

    assert isinstance(result, AgentResult)
    assert result.submitted_plan is not None, "Planner should submit a plan"
    assert len(result.submitted_plan.tasks) >= 2, (
        f"Plan should have 2+ tasks, got {len(result.submitted_plan.tasks)}"
    )


# ---------------------------------------------------------------------------
# Test 3: Streaming runner — replanner declare_blocker (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_streaming_runner_replanner_declare_blocker(api_client):
    """No in-loop submission → runner invokes LLM with replanner tools → declare_blocker."""
    ctx = _make_ctx(role="replanner", agent_name="team_replanner")
    defn = _make_defn(name="team_replanner", role="replanner")
    executor = _make_executor(api_client=api_client)

    class FakeConductor:
        _executor_snapshots: dict[str, list[dict]] = {}
    conductor = FakeConductor()
    conductor._executor_snapshots["replan-task"] = [
        {"role": "user", "content": (
            "A sibling task failed. Context:\n"
            "## Failed task\n"
            "**Task ID:** fix-io\n"
            "**Failure:** ImportError: cannot import 'load_defaults' from 'pkg._compat'\n\n"
            "## Sibling statuses\n"
            "- fix-compat [DONE]: Renamed load_defaults→get_defaults in pkg/_compat.py\n"
            "- fix-io [FAILED]: pkg/io.py line 3 imports load_defaults\n"
            "- fix-parser [RUNNING]: pkg/parser.py line 7 imports load_defaults\n"
            "- fix-cli [RUNNING]: pkg/cli.py line 2 imports load_defaults\n\n"
            "All running siblings import the renamed symbol. Shared break."
        )},
        {"role": "assistant", "content": (
            "This is clearly a shared dependency break — fix-compat renamed "
            "load_defaults and all importers will fail. I need to declare a blocker "
            "on pkg/_compat.py so running siblings are paused."
        )},
    ]
    executor.team_run.conductor = conductor

    @dataclass
    class FakeTask:
        id: str = "replan-task"
        agent_name: str = "team_replanner"

    result = await executor._run_post_run(task=FakeTask(), defn=defn, ctx=ctx)

    assert isinstance(result, BlockerDeclaration), (
        f"Expected BlockerDeclaration, got {type(result).__name__}: {result}"
    )
    assert len(result.root_cause_paths) >= 1
    assert len(result.reason) > 10


# ---------------------------------------------------------------------------
# Test 4: No api_client → sentinel result
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_no_api_client_returns_sentinel():
    """When team_run has no api_client, _run_post_run returns a sentinel result."""
    ctx = _make_ctx(role="developer", work_result="I fixed the bug in io.py")
    defn = _make_defn()
    executor = _make_executor(api_client=None)

    @dataclass
    class FakeTask:
        id: str = "test-task"
        agent_name: str = "developer"

    result = await executor._run_post_run(task=FakeTask(), defn=defn, ctx=ctx)

    assert isinstance(result, AgentResult)
    assert "no api_client" in result.summary
