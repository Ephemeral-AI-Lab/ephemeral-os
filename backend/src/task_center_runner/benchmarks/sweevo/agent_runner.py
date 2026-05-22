"""Agent runner factory for the ``benchmark_sweevo`` lifecycle.

TaskCenter entry bootstrap now converts ``entry_prompt`` into the initial
Goal directly, so the benchmark delegates every agent launch to the real
agent runner.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tools._framework.core.runtime import ExecutionMetadata

if TYPE_CHECKING:
    from task_center.attempt.launch import AttemptAgentRunner
    from task_center_runner.core.config import RunContext


def build_benchmark_sweevo_delegate_factory(
    *,
    repo_dir: str,
) -> Callable[["RunContext"], "AttemptAgentRunner"]:
    """Return a ``RunConfig.runner_factory`` that delegates every agent launch."""

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
        initial_messages: Any = None,
    ) -> Any:
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
            initial_messages=initial_messages,
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
            initial_messages: Any = None,
            **_ignored: Any,
        ) -> Any:
            return await _delegate_to_real_runner(
                config,
                prompt,
                agent_def=agent_def,
                sandbox_id=sandbox_id,
                persist_agent_run=persist_agent_run,
                task_id=task_id,
                on_event=on_event,
                extra_tool_metadata=extra_tool_metadata,
                initial_messages=initial_messages,
            )

        return runner

    return _factory


__all__ = ["build_benchmark_sweevo_delegate_factory"]
