"""Selective entry-mock runner factory for the SWE-EVO CSV benchmarker.

Per Option A in ``.omc/plans/sweevo-csv-real-agent-benchmarker-20260516.md``:
the CSV benchmarker reuses the production task-center pipeline for every
agent role EXCEPT ``entry_executor``. The entry agent is mocked so the
CSV ``pr_description`` flows verbatim into ``submit_execution_handoff``'s
``goal`` arg — bypassing the LLM that would otherwise be free to reformat
or refuse the handoff.

This module is intentionally narrow: one public factory builder, no
shared state, no scenario machinery. The non-entry delegate forwards the
exact frozen kwarg set ``EphemeralAttemptAgentLauncher._run_launch``
passes to its runner; the R1 kwarg-drift guard test
(``test_runner_kwargs_contract.py``) keeps that contract honest.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from engine.api import EphemeralRunResult
from message.messages import ConversationMessage, ToolUseBlock
from message.stream_events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from providers.types import UsageSnapshot
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.execution.tool_call import execute_tool_once
from tools.submission.executor.submit_execution_handoff import (
    submit_execution_handoff,
)

if TYPE_CHECKING:
    from task_center.attempt.launch import AttemptAgentRunner
    from task_center_runner.core.config import RunContext


async def _noop_emit(_event: Any) -> None:
    return None


def build_selective_entry_mock_runner_factory(
    *,
    goal: str,
    repo_dir: str,
) -> Callable[["RunContext"], "AttemptAgentRunner"]:
    """Return a ``RunConfig.runner_factory`` that mocks only ``entry_executor``.

    The factory captures *goal* and *repo_dir* and returns an async
    runner closure. On launch:

    - ``agent_def.name == "entry_executor"`` → directly invoke
      ``submit_execution_handoff(goal=goal)`` via ``execute_tool_once``,
      emitting the same ``AssistantMessageComplete`` / ``ToolExecutionStarted``
      / ``ToolExecutionCompleted`` shape the mock runner uses. The
      returned :class:`EphemeralRunResult` satisfies the launcher's
      exhaustion guard at ``task_center/attempt/launch.py:166-179``.
    - Any other ``agent_def.name`` → delegate to the production real-LLM
      ``engine.api.run_ephemeral_agent`` with the frozen kwarg set the
      launcher passes (``config``, ``prompt``, ``agent_def``,
      ``sandbox_id``, ``persist_agent_run``, ``task_id``, ``on_event``,
      ``extra_tool_metadata``).
    """

    async def _delegate_to_real_runner(
        config: Any,
        prompt: str,
        *,
        agent_def: Any,
        sandbox_id: str | None,
        persist_agent_run: bool,
        task_id: str,
        on_event: Callable[[Any], Awaitable[None]] | None,
        extra_tool_metadata: Any,
    ) -> Any:
        # Lazy-import to keep the engine.api dependency edge contained.
        from engine.api import run_ephemeral_agent

        if isinstance(extra_tool_metadata, ExecutionMetadata):
            metadata = extra_tool_metadata.copy()
        else:
            metadata = ExecutionMetadata()
            metadata.update(extra_tool_metadata or {})
        metadata = metadata.with_overrides(
            sandbox_id=str(sandbox_id or ""),
            agent_name=str(getattr(agent_def, "name", "") or ""),
            repo_root=repo_dir,
            exec_cwd=repo_dir,
        )
        return await run_ephemeral_agent(
            config,
            prompt,
            agent_def=agent_def,
            sandbox_id=sandbox_id,
            persist_agent_run=persist_agent_run,
            task_id=task_id,
            on_event=on_event,
            extra_tool_metadata=metadata,
        )

    async def _run_entry_executor_via_handoff(
        agent_def: Any,
        sandbox_id: str | None,
        extra_tool_metadata: Any,
        on_event: Callable[[Any], Awaitable[None]] | None,
    ) -> EphemeralRunResult:
        if isinstance(extra_tool_metadata, ExecutionMetadata):
            metadata = extra_tool_metadata.copy()
        else:
            metadata = ExecutionMetadata()
            metadata.update(extra_tool_metadata or {})

        tool_id = f"toolu_{uuid4().hex}"
        metadata = metadata.with_overrides(
            tool_id=tool_id,
            sandbox_id=str(sandbox_id or ""),
            agent_name=agent_def.name,
            repo_root=repo_dir,
            exec_cwd=repo_dir,
        )

        agent_name = agent_def.name
        run_id = str(
            metadata.get("task_center_task_id")
            or metadata.agent_run_id
            or metadata.get("run_id")
            or ""
        )

        async def _emit(event: Any) -> None:
            if callable(on_event):
                await on_event(event)

        await _emit(
            AssistantTextDelta(
                text=f"Calling {submit_execution_handoff.name}.\n",
                agent_name=agent_name,
                run_id=run_id,
            )
        )
        await _emit(
            AssistantMessageComplete(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id=tool_id,
                            name=submit_execution_handoff.name,
                            input={"goal": goal},
                        )
                    ],
                ),
                usage=UsageSnapshot(),
                agent_name=agent_name,
                run_id=run_id,
            )
        )
        await _emit(
            ToolExecutionStarted(
                tool_name=submit_execution_handoff.name,
                tool_input={"goal": goal},
                tool_id=tool_id,
                agent_name=agent_name,
                run_id=run_id,
            )
        )

        result = await execute_tool_once(
            submit_execution_handoff,
            {"goal": goal},
            ToolExecutionContextService(cwd=Path(repo_dir), services=metadata),
            emit=_noop_emit,
            emit_started=False,
        )

        await _emit(
            ToolExecutionCompleted(
                tool_name=submit_execution_handoff.name,
                output=result.output,
                is_error=result.is_error,
                tool_id=tool_id,
                metadata=dict(result.metadata or {}),
                does_terminate=result.does_terminate,
                agent_name=agent_name,
                run_id=run_id,
            )
        )

        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=result,
            agent_name=agent_name,
            event_count=1,
        )

    def _factory(_ctx: "RunContext") -> "AttemptAgentRunner":
        async def runner(
            config: Any,
            prompt: str,
            *,
            agent_def: Any,
            sandbox_id: str | None = None,
            persist_agent_run: bool = True,
            task_id: str = "",
            on_event: Callable[[Any], Awaitable[None]] | None = None,
            extra_tool_metadata: Any = None,
            **_ignored: Any,
        ) -> Any:
            if agent_def is not None and agent_def.name == "entry_executor":
                return await _run_entry_executor_via_handoff(
                    agent_def=agent_def,
                    sandbox_id=sandbox_id,
                    extra_tool_metadata=extra_tool_metadata,
                    on_event=on_event,
                )
            return await _delegate_to_real_runner(
                config,
                prompt,
                agent_def=agent_def,
                sandbox_id=sandbox_id,
                persist_agent_run=persist_agent_run,
                task_id=task_id,
                on_event=on_event,
                extra_tool_metadata=extra_tool_metadata,
            )

        return runner

    return _factory


__all__ = ["build_selective_entry_mock_runner_factory"]
