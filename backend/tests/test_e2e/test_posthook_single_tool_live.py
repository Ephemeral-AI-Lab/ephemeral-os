# ruff: noqa
"""Live e2e tests — Suite 1: Single posthook tool per role.

Each role that exposes exactly ONE posthook tool is tested end-to-end
through the real LLM via run_trigger(). Verifies:

  1. Planner role → submit_plan produces valid Plan structure
  2. Explorer role → post_note produces meaningful summary content

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_posthook_single_tool_live.py -v -m live -o "addopts="
"""

from __future__ import annotations

import pytest

from engine.testing.eval_agent import EvalAgent
from external_trigger.runner import run as run_trigger
from tests.test_e2e.conftest import create_eval_agent
from tools.context.toolkit import PostNoteTool
from tools.posthook.toolkit import SubmitPlanTool

pytestmark = [pytest.mark.e2e, pytest.mark.live]

HAS_CREDENTIALS = EvalAgent.has_credentials()


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
# Test 1: Planner → submit_plan (single tool)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_planner_submits_plan_single_tool(api_client):
    """Planner with only submit_plan produces a valid plan decomposition."""
    messages = [
        {"role": "user", "content": (
            "Decompose this task: Refactor the authentication module. "
            "The module has three files: src/auth/login.py (login flow), "
            "src/auth/session.py (session management), and src/auth/middleware.py "
            "(request authentication). Each file needs independent fixes. "
            "Use agent 'developer' for atomic tasks."
        )},
        {"role": "assistant", "content": (
            "I've analyzed the authentication module. It has three independent "
            "concerns: login flow, session management, and middleware authentication. "
            "Each can be addressed by a separate developer task with no cross-dependencies."
        )},
    ]

    result = await run_trigger(
        agent_name="test:planner_submit",
        messages=messages,
        system_prompt=(
            "You are a planner agent. Decompose work into subtasks. "
            "You MUST call submit_plan with a list of tasks. "
            "Each task needs: id, task (prose description), agent, deps, scope_paths."
        ),
        prompt=(
            "Your main work is complete. You must now submit your results "
            "by calling submit_plan. Summarize what you accomplished and "
            "call the tool."
        ),
        tools=[SubmitPlanTool()],
        api_client=api_client,
        max_tokens_per_turn=1500,
        max_turns=5,
    )

    assert result.tool_name == "submit_plan", (
        f"Planner should call submit_plan, got {result.tool_name}"
    )
    tasks = result.tool_input.get("tasks", [])
    assert len(tasks) >= 2, f"Plan should have at least 2 tasks, got {len(tasks)}"

    # Each task must have required fields
    for task in tasks:
        assert "id" in task, f"Task missing 'id': {task}"
        assert "task" in task, f"Task missing 'task': {task}"
        assert "agent" in task, f"Task missing 'agent': {task}"
        assert len(task["task"]) > 10, f"Task description too short: {task['task']}"

    # Pydantic validation should have passed
    assert result.validated is not None, "Pydantic validation should succeed"
    assert result.turns_used <= 3, f"Should not need many retries, used {result.turns_used}"


# ---------------------------------------------------------------------------
# Test 2: Explorer → post_note (single tool)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_explorer_posts_note_single_tool(api_client):
    """Explorer with only post_note produces meaningful summary content."""
    messages = [
        {"role": "user", "content": (
            "Investigate the codebase structure of the pkg/ directory. "
            "Identify the main modules and their dependencies."
        )},
        {"role": "assistant", "content": (
            "I've explored the pkg/ directory. It contains:\n"
            "- pkg/io.py — IO module, imports from pkg._compat\n"
            "- pkg/parser.py — parser, imports from pkg._compat and pkg.utils\n"
            "- pkg/cli.py — CLI entry point, uses argparse and pkg.commands\n"
            "- pkg/_compat.py — compatibility shim, exports load_defaults()\n"
            "- pkg/utils.py — shared utilities\n"
            "The main dependency chain is: cli → commands, io/parser → _compat → utils."
        )},
    ]

    result = await run_trigger(
        agent_name="test:explorer_note",
        messages=messages,
        system_prompt="You are an explorer agent. Report your findings.",
        prompt=(
            "Your main work is complete. You must now submit your results "
            "by calling post_note. Summarize what you found."
        ),
        tools=[PostNoteTool()],
        api_client=api_client,
        max_tokens_per_turn=500,
        max_turns=5,
    )

    assert result.tool_name == "post_note", (
        f"Explorer should call post_note, got {result.tool_name}"
    )
    content = result.tool_input.get("content", "")
    assert len(content) > 20, f"Note content too short: {content}"
    assert result.validated is not None, "Pydantic validation should succeed"
    assert result.turns_used <= 2, f"Single tool should succeed quickly, used {result.turns_used}"


# ---------------------------------------------------------------------------
# Test 3: Planner submit_plan with complex dependencies
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_planner_submits_plan_with_deps(api_client):
    """Planner produces a plan where later tasks depend on earlier ones."""
    messages = [
        {"role": "user", "content": (
            "Decompose: Migrate the database schema. Step 1: create migration file. "
            "Step 2: run migration. Step 3: verify migration. Each step depends on "
            "the previous one. Use agent 'developer' for all tasks."
        )},
        {"role": "assistant", "content": (
            "The database migration is a strictly sequential pipeline: "
            "create → run → verify. Each step must complete before the next starts."
        )},
    ]

    result = await run_trigger(
        agent_name="test:planner_submit_deps",
        messages=messages,
        system_prompt=(
            "You are a planner agent. Create a plan with sequential dependencies. "
            "You MUST call submit_plan. Use deps arrays to express ordering."
        ),
        prompt=(
            "Your main work is complete. Submit your plan by calling submit_plan."
        ),
        tools=[SubmitPlanTool()],
        api_client=api_client,
        max_tokens_per_turn=1500,
        max_turns=5,
    )

    assert result.tool_name == "submit_plan"
    tasks = result.tool_input.get("tasks", [])
    assert len(tasks) >= 3, f"Sequential plan needs at least 3 tasks, got {len(tasks)}"

    # At least one task should have non-empty deps
    has_deps = any(task.get("deps") for task in tasks)
    assert has_deps, "Sequential plan must have dependency edges"
