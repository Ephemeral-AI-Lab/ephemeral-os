"""Native team agent runner.

Provides :class:`TeamAgentRunner`, the standard implementation of the
``QueryRunner`` callable expected by :class:`team.runtime.executor.Executor`.
It spawns an :class:`EphemeralAgent`, wires ``tool_metadata`` into the agent's
``QueryContext``, drives the event loop, observes tool completions for
coordination (``TaskCenter.on_edit`` / ``on_posthook``), schedules ``tc_note``
checkpoints, and extracts ``work_result`` for the posthook submission phase.

Callers customise behaviour (telemetry, printer output, persistence) by
supplying the ``on_spawned`` / ``on_event`` / ``on_complete`` /
``on_checkpoint_event`` hooks rather than reimplementing the runner itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agents.run_tracker import AgentRunTracker
from engine.runtime.agent import spawn_agent
from message.stream_events import ToolExecutionCompleted, ToolExecutionStarted
from team.runtime.context_builder import TeamAgentContext

logger = logging.getLogger(__name__)

# Tools whose completion should tick ``TaskCenter.on_posthook`` for coordination.
POSTHOOK_TOOL_NAMES = frozenset({
    "post_note",
    "submit_plan",
    "request_replan",
    "add_tasks",
    "declare_blocker",
    "cancel_and_redraft",
})
# Tools whose completion should record a scoped edit via ``TaskCenter.on_edit``.
EDIT_TOOL_NAMES = frozenset({"daytona_edit_file", "daytona_write_file"})


@dataclass
class AgentRunState:
    """Mutable state handed to :class:`TeamAgentRunner` hooks."""

    defn: Any
    ctx: TeamAgentContext
    agent: Any
    tracker: Any
    team_run_id: str
    work_item_id: str
    compacted_before: int | None = None
    final_text: str = ""
    error: str | None = None
    pending_tool_inputs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def extract_final_text(messages: list[Any]) -> str:
    """Return the last assistant text emitted during an agent run."""
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "assistant":
            continue
        text = getattr(msg, "text", "")
        if text:
            return str(text).strip()
    return ""


class TeamAgentRunner:
    """Standard team runner — spawn agent, wire metadata, drive event loop.

    Responsibilities (always performed):
      * ``AgentRunTracker`` lifecycle
      * ``spawn_agent`` + tool_metadata wiring
      * ``on_turn`` callback (conductor snapshot + ``task_center.tick``)
      * Tool completion observation (``on_edit`` / ``on_posthook``)
      * ``tc_note`` checkpoint scheduling (per agent's ``allowed_triggers``)
      * ``work_result`` extraction into ``ctx.tool_metadata``

    Hooks (optional extension points):
      * ``on_spawned(state)`` — synchronous, after spawn, before ``agent.run``
      * ``on_event(event, state)`` — synchronous, per stream event
      * ``on_complete(state)`` — awaitable, after the event loop returns
      * ``on_checkpoint_event(payload)`` — synchronous, on tc_note lifecycle
    """

    def __init__(
        self,
        session_config: Any,
        sandbox_id: str,
        *,
        agent_overrides: dict[str, dict[str, Any]] | None = None,
        on_spawned: Callable[[AgentRunState], None] | None = None,
        on_event: Callable[[Any, AgentRunState], None] | None = None,
        on_complete: Callable[[AgentRunState], Awaitable[None]] | None = None,
        on_checkpoint_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session_config = session_config
        self.sandbox_id = sandbox_id
        self.agent_overrides = agent_overrides
        self.on_spawned = on_spawned
        self.on_event = on_event
        self.on_complete = on_complete
        self.on_checkpoint_event = on_checkpoint_event

    def _effective_defn(self, defn: Any) -> Any:
        if not self.agent_overrides:
            return defn
        overrides = self.agent_overrides.get(defn.name)
        return defn.model_copy(update=overrides) if overrides else defn

    async def __call__(self, defn: Any, ctx: TeamAgentContext) -> dict[str, Any]:
        effective_defn = self._effective_defn(defn)
        prompt = ctx.user_message or ""

        tracker = AgentRunTracker.create(
            session_id=getattr(self.session_config, "session_id", None),
            run_id=getattr(ctx.tool_metadata, "agent_run_id", None),
            agent_name=effective_defn.name,
            input_query=prompt,
        )
        if tracker.run_id is not None:
            ctx.tool_metadata.agent_run_id = tracker.run_id

        agent = spawn_agent(
            self.session_config,
            messages=list(ctx.initial_messages),
            agent_def=effective_defn,
            latest_user_prompt=prompt,
            sandbox_id=self.sandbox_id,
        )

        compacted_before: int | None = None
        if getattr(agent.query_context, "session_state", None) is not None:
            compacted_before = int(agent.query_context.session_state.compacted)

        # Merge spawn_agent's tool_metadata into ctx and redirect agent to ctx's metadata
        # so team tools (submit_plan / post_note / …) write into the correct slot.
        spawned_meta = agent.query_context.tool_metadata
        if getattr(spawned_meta, "session_config", None) is not None:
            ctx.tool_metadata.session_config = spawned_meta.session_config
        sb = getattr(spawned_meta, "sandbox_id", None) or ""
        if sb:
            ctx.tool_metadata["sandbox_id"] = sb
        ctx.tool_metadata.agent_name = effective_defn.name
        agent.query_context.tool_metadata = ctx.tool_metadata
        agent.query_context.run_id = tracker.run_id or ""

        team_run_id = str(ctx.tool_metadata.get("team_run_id") or "")
        work_item_id = str(ctx.tool_metadata.get("work_item_id") or "")

        state = AgentRunState(
            defn=effective_defn,
            ctx=ctx,
            agent=agent,
            tracker=tracker,
            team_run_id=team_run_id,
            work_item_id=work_item_id,
            compacted_before=compacted_before,
        )
        checkpoint_task: asyncio.Task[None] | None = None

        def _snapshot() -> list[dict[str, Any]]:
            return [m.model_dump(mode="json") for m in agent.display_messages]

        def _schedule_checkpoint(snapshot: list[dict[str, Any]] | None = None) -> None:
            nonlocal checkpoint_task
            if not team_run_id or not work_item_id:
                return
            if "tc_note" not in getattr(effective_defn, "allowed_triggers", []):
                return
            try:
                from team.runtime.registry import get as get_team_run

                team_run = get_team_run(team_run_id)
                if team_run is None:
                    return
                frozen = snapshot if snapshot is not None else _snapshot()
                team_run.conductor.register_snapshot(work_item_id, frozen)
                trigger = team_run.task_center.activity.should_take_note(work_item_id)
                if trigger is None:
                    return
                if checkpoint_task is not None and not checkpoint_task.done():
                    return

                async def _run_checkpoint() -> None:
                    event_base = {
                        "event": "external_hook",
                        "hook": "tc_note",
                        "team_run_id": team_run_id,
                        "work_item_id": work_item_id,
                        "agent": effective_defn.name,
                        "trigger": trigger,
                    }
                    if self.on_checkpoint_event is not None:
                        self.on_checkpoint_event({**event_base, "status": "started"})
                    try:
                        posted = await team_run.task_center.activity.check(
                            work_item_id,
                            snapshot=frozen,
                            api_client=agent.query_context.api_client,
                            model=agent.model,
                        )
                    except Exception as exc:
                        if self.on_checkpoint_event is not None:
                            self.on_checkpoint_event(
                                {**event_base, "status": "failed", "error": str(exc)}
                            )
                        raise
                    status = "completed" if posted else "skipped"
                    if self.on_checkpoint_event is not None:
                        self.on_checkpoint_event({**event_base, "status": status})

                checkpoint_task = asyncio.create_task(_run_checkpoint())
            except Exception:
                logger.debug(
                    "Failed to schedule task-center checkpoint for %s",
                    work_item_id,
                    exc_info=True,
                )

        def _on_turn(display_messages: list[Any]) -> None:
            if not team_run_id or not work_item_id:
                return
            try:
                from team.runtime.registry import get as get_team_run

                team_run = get_team_run(team_run_id)
                if team_run is None:
                    return
                snap = [m.model_dump(mode="json") for m in display_messages]
                team_run.conductor.register_snapshot(work_item_id, snap)
                team_run.task_center.activity.tick(work_item_id)
                _schedule_checkpoint(snap)
            except Exception:
                logger.debug("Failed to observe turn for %s", work_item_id, exc_info=True)

        agent.query_context.on_turn = _on_turn

        if self.on_spawned is not None:
            self.on_spawned(state)

        try:
            async for event in agent.run(prompt):
                if isinstance(event, ToolExecutionStarted):
                    state.pending_tool_inputs.setdefault(event.tool_name, []).append(
                        event.tool_input
                    )
                elif (
                    isinstance(event, ToolExecutionCompleted)
                    and team_run_id
                    and work_item_id
                ):
                    try:
                        from team.runtime.registry import get as get_team_run

                        team_run = get_team_run(team_run_id)
                        inputs = state.pending_tool_inputs.get(event.tool_name) or []
                        tool_input = inputs.pop(0) if inputs else {}
                        if team_run is not None and not event.is_error:
                            if event.tool_name in EDIT_TOOL_NAMES:
                                file_path = str(
                                    tool_input.get("file_path")
                                    or tool_input.get("path")
                                    or ""
                                ).strip()
                                if file_path:
                                    team_run.task_center.activity.on_edit(work_item_id, file_path)
                            if event.tool_name in POSTHOOK_TOOL_NAMES:
                                team_run.task_center.activity.on_posthook(work_item_id)
                            _schedule_checkpoint()
                    except Exception:
                        logger.debug(
                            "Failed to observe tool completion for %s",
                            work_item_id,
                            exc_info=True,
                        )
                    # Terminate agent immediately when request_replan is
                    # accepted during the main run.  The replan intent is
                    # stashed in tool_metadata so the executor can skip
                    # the posthook and dispatch a ReplanRequest directly.
                    if (
                        event.tool_name == "request_replan"
                        and not event.is_error
                    ):
                        inputs = state.pending_tool_inputs.get("request_replan") or []
                        tool_input = inputs[-1] if inputs else {}
                        ctx.tool_metadata["replan_requested_during_run"] = True
                        ctx.tool_metadata["replan_reason"] = str(tool_input.get("reason", ""))
                        ctx.tool_metadata["replan_suggestion"] = tool_input.get("suggestion")
                        logger.info(
                            "request_replan accepted during main run for %s; terminating agent",
                            work_item_id,
                        )
                        break
                if self.on_event is not None:
                    self.on_event(event, state)
        except Exception as exc:
            state.error = str(exc)
            logger.exception("team agent %s crashed", effective_defn.name)
            raise
        finally:
            if checkpoint_task is not None:
                await asyncio.gather(checkpoint_task, return_exceptions=True)
            state.final_text = extract_final_text(agent.display_messages)
            if state.final_text:
                ctx.tool_metadata["work_result"] = state.final_text
            # Detect budget exhaustion so the posthook can force a replan.
            qc = agent.query_context
            if (
                getattr(qc, "tool_call_limit", None) is not None
                and getattr(qc, "tool_calls_used", 0) >= qc.tool_call_limit
            ):
                ctx.tool_metadata["budget_exhausted"] = True
            if team_run_id and work_item_id:
                try:
                    from team.runtime.registry import get as get_team_run

                    team_run = get_team_run(team_run_id)
                    if team_run is not None:
                        team_run.conductor.register_snapshot(work_item_id, _snapshot())
                except Exception:
                    logger.debug(
                        "Failed to persist final agent snapshot for %s",
                        work_item_id,
                        exc_info=True,
                    )
            if self.on_complete is not None:
                await self.on_complete(state)

        return {
            "agent": effective_defn.name,
            "final_text": state.final_text,
            "team_run_id": team_run_id,
            "work_item_id": work_item_id,
            "agent_run_id": ctx.tool_metadata.get("agent_run_id"),
        }
