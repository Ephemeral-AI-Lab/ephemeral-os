from __future__ import annotations

import asyncio
from types import SimpleNamespace

from engine.core.query import QueryExitReason
from team.runtime.context_builder import TeamAgentContext
from team.runtime.runner import TeamAgentRunner
from tools.core.runtime import ExecutionMetadata


def test_team_agent_runner_autofails_missing_terminal_submission(monkeypatch):
    run_prompts: list[str] = []

    class _Tracker:
        run_id = "agent-run-1"

    async def _fake_run(prompt: str):
        run_prompts.append(prompt)
        agent.query_context.exit_reason = QueryExitReason.TEXT_RESPONSE
        if False:
            yield None

    agent = SimpleNamespace(
        query_context=SimpleNamespace(
            tool_metadata=ExecutionMetadata(session_config="cfg", sandbox_id="sbx-1"),
            run_id="",
            session_state=None,
            exit_reason=None,
            terminal_tools=set(),
            on_turn=None,
        ),
        display_messages=[SimpleNamespace(role="assistant", text="Still working")],
        model="test-model",
        run=_fake_run,
    )

    monkeypatch.setattr(
        "team.runtime.runner.AgentRunTracker",
        SimpleNamespace(create=lambda **_: _Tracker()),
    )
    monkeypatch.setattr("team.runtime.runner.spawn_agent", lambda *_args, **_kwargs: agent)

    runner = TeamAgentRunner(
        session_config=SimpleNamespace(session_id="session-1"),
        sandbox_id="sbx-1",
    )
    tool_metadata = ExecutionMetadata(
        team_run_id="team-run-1",
        work_item_id="task-1",
    )
    tool_metadata["terminal_tools"] = {"submit_task_summary"}
    ctx = TeamAgentContext(user_message="Do the task", tool_metadata=tool_metadata)

    result = asyncio.run(
        runner(
            SimpleNamespace(name="developer"),
            ctx,
        )
    )

    assert run_prompts == ["Do the task"]
    assert ctx.tool_metadata["task_summary_type"] == "request_replan"
    assert ctx.tool_metadata["task_summary"] == "Agent did not call a terminal submission tool."
    assert ctx.tool_metadata["work_result"] == "Still working"
    assert result["agent_run_id"] == "agent-run-1"
