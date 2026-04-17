# ruff: noqa
"""E2E live test for background task progress live-tailing.

Verifies that streaming-capable background tools can push incremental
output via ``on_progress_line`` and that ``check_background_progress``
surfaces a live tail (with ``last_n_lines`` honoured) while the task is
still running. Also guards the negative case: a non-streaming background
task must NOT leak any partial output mid-run.

No API credentials required — exercises the BackgroundTaskManager and
the real CheckBackgroundProgressTool directly with the same context
wiring as ``query.py``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from engine.runtime.background_tasks import BackgroundTaskManager
from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import (
    create_eval_agent,
    create_test_sandbox,
    delete_test_sandbox,
)
from tools.builtins.background.check_background_progress import (
    CheckBackgroundProgressInput,
    CheckBackgroundProgressTool,
)
from tools.core.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e


class _StreamingInput(BaseModel):
    n_lines: int = Field(default=5)
    interval: float = Field(default=0.05)


class _StreamingTool(BaseTool):
    """Background-capable tool that emits progress lines via on_progress_line."""

    name: str = "fake_streaming"
    description: str = "Emit n_lines progress lines, sleeping interval between each."
    input_model: type[BaseModel] = _StreamingInput
    background: str = "optional"

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, _StreamingInput)
        on_line = context.metadata.get("on_progress_line")
        for i in range(arguments.n_lines):
            if on_line is not None:
                on_line(f"line {i + 1}")
            await asyncio.sleep(arguments.interval)
        return ToolResult(output="\n".join(f"line {i + 1}" for i in range(arguments.n_lines)))


@pytest.mark.asyncio
async def test_live_tail_visible_while_running() -> None:
    """While the streaming tool is mid-flight, check_background_progress
    must return the lines already emitted via on_progress_line, with
    last_n_lines honoured. After completion, the final output is available."""
    mgr = BackgroundTaskManager()
    tool = _StreamingTool()

    n_lines = 6
    interval = 0.08
    alias = mgr.next_alias()

    async def _coro() -> ToolResult:
        ctx = ToolExecutionContext(
            cwd=Path("/tmp"),
            metadata={"on_progress_line": mgr.make_progress_callback(alias)},
        )
        return await tool.execute(_StreamingInput(n_lines=n_lines, interval=interval), ctx)

    mgr.launch(alias, "fake_streaming", {}, _coro())

    # Wait long enough for ~3 lines to have been emitted, but not all 6.
    await asyncio.sleep(interval * 3 + interval / 2)

    check_tool = CheckBackgroundProgressTool()
    check_ctx = ToolExecutionContext(
        cwd=Path("/tmp"),
        metadata={"background_task_manager": mgr},
    )

    mid_result = await check_tool.execute(
        CheckBackgroundProgressInput(task_id=alias, last_n_lines=2),
        check_ctx,
    )
    assert not mid_result.is_error, mid_result.output
    assert '"status": "running"' in mid_result.output, mid_result.output
    assert '"output"' in mid_result.output, (
        f"Expected live tail in mid-flight check, got:\n{mid_result.output}"
    )
    # last_n_lines=2 → only the most recent two streamed lines should
    # appear, and earlier ones should NOT.
    assert "line 1" not in mid_result.output, mid_result.output
    assert any(f"line {i}" in mid_result.output for i in (2, 3, 4)), mid_result.output

    # Now wait for completion and re-check.
    completed = await mgr.wait_for(alias, timeout=5.0)
    assert completed is not None, "task should complete within timeout"
    assert completed.status in ("completed", "delivered")

    final_result = await check_tool.execute(
        CheckBackgroundProgressInput(task_id=alias, last_n_lines=20),
        check_ctx,
    )
    assert not final_result.is_error
    assert '"status":' in final_result.output
    assert "completed" in final_result.output or "delivered" in final_result.output
    assert f"line {n_lines}" in final_result.output


@pytest.mark.asyncio
async def test_no_streaming_means_no_output_field_while_running() -> None:
    """A background task that does NOT use on_progress_line should not
    surface any partial output until it finishes."""
    mgr = BackgroundTaskManager()

    async def _coro() -> ToolResult:
        await asyncio.sleep(0.3)
        return ToolResult(output="final only")

    alias = mgr.next_alias()
    mgr.launch(alias, "noop", {}, _coro())

    await asyncio.sleep(0.05)
    snap = mgr.get_status(alias)
    assert snap and snap[0]["status"] == "running"
    assert snap[0].get("output") == "[started: noop]"

    await mgr.wait_for(alias, timeout=2.0)
    snap = mgr.get_status(alias)
    assert snap[0]["status"] in ("completed", "delivered")
    assert snap[0]["output"] == "final only"


# ===========================================================================
# Real Daytona: daytona_codeact streams stdout via on_progress_line while running
# ===========================================================================


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.skipif(
    not EvalAgent.has_daytona(), reason="Daytona credentials required for live streaming test"
)
class TestDaytonaBashLiveStreaming:
    """Verify daytona_codeact uses session-based streaming when launched as a
    background task, so check_background_progress sees partial output mid-run."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("nova-livestream")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_streaming_visible_via_background_manager(self, sandbox) -> None:
        """Drive daytona_codeact directly through BackgroundTaskManager (no LLM):
        a slow loop must surface lines through check_background_progress
        BEFORE the command finishes."""
        from sandbox.async_client import get_async_daytona_client
        from tools.daytona_toolkit.codeact_tool import daytona_codeact

        client = get_async_daytona_client()
        sb = await client.get(sandbox["id"])

        mgr = BackgroundTaskManager()
        alias = mgr.next_alias()

        async def _coro() -> ToolResult:
            ctx = ToolExecutionContext(
                cwd=Path("/tmp"),
                metadata={
                    "daytona_sandbox": sb,
                    "daytona_cwd": "/home/daytona",
                    "on_progress_line": mgr.make_progress_callback(alias),
                    "background_task_id": alias,
                },
            )
            args = daytona_codeact.input_model(
                command='for i in $(seq 1 8); do echo "step_$i"; sleep 2; done',
                timeout=60,
            )
            return await daytona_codeact.execute(args, ctx)

        mgr.launch(alias, "daytona_codeact", {}, _coro())

        # Allow time for session creation + WebSocket connect + ~3 lines
        # to stream through (~6s of command output).
        await asyncio.sleep(8.0)

        check_tool = CheckBackgroundProgressTool()
        check_ctx = ToolExecutionContext(
            cwd=Path("/tmp"),
            metadata={"background_task_manager": mgr},
        )
        mid_result = await check_tool.execute(
            CheckBackgroundProgressInput(task_id=alias, last_n_lines=10),
            check_ctx,
        )
        logger.info("[livestream] mid-flight check:\n%s", mid_result.output)
        assert not mid_result.is_error, mid_result.output
        assert '"status": "running"' in mid_result.output, mid_result.output
        assert '"output"' in mid_result.output, (
            f"Expected live tail in mid-flight check, got:\n{mid_result.output}"
        )
        # At least one of the early steps should have streamed through.
        assert any(f"step_{i}" in mid_result.output for i in (1, 2, 3)), mid_result.output
        # The final step (~16s in) cannot have arrived yet (we waited ~8s).
        assert "step_8" not in mid_result.output, mid_result.output

        completed = await mgr.wait_for(alias, timeout=30.0)
        assert completed is not None and completed.status in ("completed", "delivered")
        final_result = await check_tool.execute(
            CheckBackgroundProgressInput(task_id=alias, last_n_lines=20),
            check_ctx,
        )
        logger.info("[livestream] final check:\n%s", final_result.output)
        assert "step_8" in final_result.output, final_result.output


# ===========================================================================
# Real LLM: agent observes live tail mid-run via check_background_progress
# ===========================================================================


_LIVE_AGENT_PROMPT = """\
You are a senior developer with a remote Daytona sandbox.

You MUST use tools for every action. Never describe what you'd do — execute it.
For long-running shell commands, run them in background with "background": true,
then use check_background_progress (non-blocking) to peek at partial output,
and wait_for_background_task to block until they finish.
"""


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestSupernovaLiveTail:
    """An LLM-driven check that the agent can observe streaming output from a
    long-running daytona_codeact task while it is still running."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("nova-livetail")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_agent_sees_partial_output_mid_run(self, sandbox) -> None:
        agent = create_eval_agent(
            system_prompt=_LIVE_AGENT_PROMPT,
            sandbox_id=sandbox["id"],
            enable_background_tasks=True,
        )
        result = await agent.invoke(
            "Run this exact bash command in BACKGROUND (set background=true):\n\n"
            '  for i in $(seq 1 10); do echo "step_$i"; sleep 3; done\n\n'
            "(Total runtime ~30 seconds.)\n\n"
            "Then:\n"
            "1. Sleep ~8 seconds in the FOREGROUND (use daytona_codeact with `sleep 8`,\n"
            "   background=false).\n"
            "2. Call check_background_progress(task_id='bg_1', last_n_lines=20)\n"
            "   and read the partial output. The background task should still be running.\n"
            "3. Report which step_N lines you see at that moment.\n"
            "4. Then wait_for_background_task until it completes.\n"
            "5. Report the final lines.\n"
        )

        # Verify the agent actually exercised the live-tail path.
        assert result.has_tool("check_background_progress"), (
            f"Agent never called check_background_progress; tools used: {result.tool_names}"
        )

        # Inspect every check_background_progress completion event — at least
        # one must have surfaced partial step_ output while the bg task was
        # still running, and must NOT contain the final step.
        check_completions = [
            e for e in result.tools_completed() if e.tool_name == "check_background_progress"
        ]
        saw_live_tail = False
        for evt in check_completions:
            out = evt.output or ""
            logger.info("[livetail] check_background_progress output:\n%s", out)
            if (
                '"status": "running"' in out
                and any(f"step_{i}" in out for i in (1, 2, 3, 4, 5))
                and "step_10" not in out
            ):
                saw_live_tail = True
                break
        assert saw_live_tail, (
            "No mid-flight check_background_progress call surfaced partial step_ lines. "
            f"Outputs: {[(e.output or '')[:300] for e in check_completions]}"
        )
