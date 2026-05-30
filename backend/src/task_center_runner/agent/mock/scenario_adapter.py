"""Adapter: drive an imperative :class:`Scenario` through the REAL query loop.

Bridges the existing scenario decision methods (``planner_response`` /
``executor_actions`` / ``reducer_response``, which return
:class:`ToolCallSpec` / probe-name sequences) into the per-turn
``TurnScript`` protocol consumed by :class:`ScenarioEventSource`.

Per-role:
- planner / reducer → one single-call ``Turn`` from the spec.
- executor → run each probe-name's coroutine (yielding one ``ToolCall`` per
  step), then submit ``submit_generator_success``.

``ScenarioContext`` is built from ``context.tool_metadata`` at call time
(``attempt_runtime`` carries the live TaskCenter stores), so a single per-agent
source serves whichever task/attempt the launcher routes to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import ToolResult
from message.message import ToolResultBlock

from task_center_runner.audit.events import EventType
from task_center_runner.agent.mock.event_source import ToolCall, Turn, TurnScript
from task_center_runner.agent.mock.probe_bridge import (
    bridge_probe_for,
    bridge_script_for,
    bridge_turns,
)
from task_center_runner.agent.mock.probes import (
    PROBE_BUILDERS,
    PROBE_SUMMARY,
    ProbeContext,
)
from task_center_runner.scenarios.base import ScenarioContext, ToolCallSpec

if TYPE_CHECKING:
    from agents import AgentDefinition
    from engine.query.context import QueryContext
    from task_center_runner.scenarios.base import Scenario


def normalize_result(blocks: list[ToolResultBlock]) -> ToolResult:
    """Normalize the loop's trailing ``ToolResultBlock``(s) into the
    ``ToolResult`` shape the imperative probe bodies inspect (``.output`` etc.)."""
    block = blocks[0] if blocks else None
    if block is None:
        return ToolResult(output="", is_error=True)
    return ToolResult(
        output=block.content,
        is_error=block.is_error,
        metadata=dict(block.metadata or {}),
        is_terminal=block.is_terminal,
    )


def _attempt_and_iteration(metadata: Any) -> tuple[Any, Any]:
    runtime = metadata.get("attempt_runtime")
    if runtime is None:
        raise RuntimeError("Missing AttemptDeps in mocked agent metadata.")
    attempt_id = str(metadata.get("task_center_attempt_id") or "")
    attempt = runtime.attempt_store.get(attempt_id)
    if attempt is None:
        raise RuntimeError(f"Attempt {attempt_id!r} not found.")
    iteration = runtime.iteration_store.get(attempt.iteration_id)
    if iteration is None:
        raise RuntimeError(f"Iteration {attempt.iteration_id!r} not found.")
    return attempt, iteration


def build_scenario_context(
    scenario: "Scenario",
    metadata: Any,
    *,
    prompt: str,
    audit_recorder: Any | None,
) -> ScenarioContext:
    """Build the live :class:`ScenarioContext` from loop ``tool_metadata``."""
    attempt, iteration = _attempt_and_iteration(metadata)
    runtime = metadata.get("attempt_runtime")
    workflow = runtime.workflow_store.get(iteration.workflow_id)
    task_id = str(metadata.get("task_center_task_id") or "")
    task = runtime.task_store.get_task(task_id) if task_id else None
    return ScenarioContext(
        attempt=attempt,
        iteration=iteration,
        workflow=workflow,
        prompt=prompt,
        metadata=metadata,
        audit_recorder=audit_recorder,
        task_id=task_id or None,
        agent_name=str(metadata.agent_name or "") or None,
        context_message=(str(task.get("context_message") or "") if task else None),
        requirement_ledger=getattr(scenario, "requirement_ledger", None),
        package_plan=getattr(scenario, "package_plan", None),
        matrix_plan=getattr(scenario, "matrix_plan", None),
    )


def _spec_turn(spec: ToolCallSpec) -> Turn:
    return Turn(calls=(ToolCall(spec.tool.name, dict(spec.args)),))


def _ask_advisor_turn(tool_name: str, tool_payload: dict[str, Any]) -> Turn:
    """The approval turn every gated terminal needs.

    Main-agent submission terminals carry ``AdvisorApprovalPreHook``; the loop
    only lets the terminal through if the transcript holds an ``ask_advisor``
    result (``helper_role=="advisor"``, ``verdict=="approve"``) paired with an
    originating ``ask_advisor`` call whose ``tool_name`` matches THIS terminal.
    The advisor sub-agent that ``ask_advisor`` spawns is itself scripted by
    ``_advisor_script`` (its ``submit_advisor_feedback`` terminal is ungated).
    """
    return Turn(
        calls=(
            ToolCall(
                "ask_advisor",
                {"tool_name": tool_name, "tool_payload": dict(tool_payload)},
            ),
        )
    )


async def _planner_script(
    scenario: "Scenario",
    ctx: ScenarioContext,
) -> TurnScript:
    spec = scenario.planner_response(ctx)
    _ = yield _ask_advisor_turn(spec.tool.name, spec.args)
    _ = yield _spec_turn(spec)


async def _reducer_script(scenario: "Scenario", ctx: ScenarioContext) -> TurnScript:
    spec = scenario.reducer_response(ctx)
    _ = yield _ask_advisor_turn(spec.tool.name, spec.args)
    _ = yield _spec_turn(spec)


async def _advisor_script() -> TurnScript:
    """Advisor sub-agent (spawned by ``ask_advisor``): approve.

    Its ``submit_advisor_feedback`` terminal is ungated, so this is a single
    turn.
    """
    verdict = "approve"
    summary = "Mock advisor approval."
    _ = yield Turn(
        calls=(
            ToolCall(
                "submit_advisor_feedback",
                {"verdict": verdict, "summary": summary},
            ),
        )
    )


async def _executor_script(
    scenario: "Scenario",
    ctx: ScenarioContext,
    probe_ctx: ProbeContext,
) -> TurnScript:
    """Drive each ``executor_actions`` probe coroutine, then submit success.

    Each probe yields one :class:`ToolCall`; we delegate it to the loop as a
    single-call ``Turn`` (the yield must live here — Python forbids hiding an
    async-generator yield inside a helper) and feed the normalized result back.
    Probe-internal out-of-band sandbox work runs during ``asend``.
    """
    actions = tuple(scenario.executor_actions(ctx))
    summary = "Workspace preflight completed."
    artifacts: list[str] = []
    for action in actions:
        # --- terminal routing (each emits its own gated terminal, then ends) -
        if action == "fail" or action.startswith("fail:"):
            reason = (
                action.split(":", 1)[1]
                if ":" in action
                else "Scenario-injected generator failure."
            )
            blocker_args = {"outcome": reason}
            _ = yield _ask_advisor_turn("submit_generator_failure", blocker_args)
            _ = yield Turn(
                calls=(ToolCall("submit_generator_failure", blocker_args),)
            )
            return
        if action.startswith("request_recursive_workflow:") or action.startswith(
            "request_recursive_matrix:"
        ):
            package_id = action.split(":", 1)[1]
            goal_handoff = scenario.recursive_handoff_goal(ctx) or (
                f"Resolve recursive package {package_id}."
            )
            handoff_args = {"goal_handoff": goal_handoff}
            _ = yield _ask_advisor_turn("submit_workflow_handoff", handoff_args)
            _ = yield Turn(
                calls=(ToolCall("submit_workflow_handoff", handoff_args),)
            )
            return

        builder = PROBE_BUILDERS.get(action)
        if builder is not None:
            # Generator-style probe: yields one ToolCall per step directly.
            probe = builder(probe_ctx)
            send: Any = None
            while True:
                try:
                    call = await probe.asend(send)
                except StopAsyncIteration:
                    break
                blocks = yield Turn(calls=(call,))
                send = normalize_result(blocks)
            summary = PROBE_SUMMARY.get(action, summary)
            artifacts = [] if action == "preflight" else [probe_ctx.probe_path()]
            continue

        # PreparedToolScript action (full_case / full_stack / capacity): build
        # the deterministic script from ``ctx`` and drive its steps through the
        # SAME queue-bridge so every tool routes through the loop.
        scripted = bridge_script_for(action, ctx=ctx)
        if scripted is None:
            # Imperative call_tool-based probe (heavy/fan-out): drive its body
            # through the queue-bridge so every tool still routes through the
            # loop.
            scripted = bridge_probe_for(action, probe_ctx=probe_ctx)
        if scripted is None:
            raise NotImplementedError(
                f"executor action {action!r} not yet adapted (Phase 2)."
            )
        factory, bridge_summary = scripted
        artifact_out: list[str] = []
        driver = bridge_turns(
            factory,
            artifact_out=artifact_out,
            normalize=normalize_result,
            on_background_cancel=lambda payload: probe_ctx.publish(
                EventType.SANDBOX_TOOL_CANCELLED,
                payload=payload,
            ),
        )
        bridge_send: Any = None
        while True:
            try:
                turn = await driver.asend(bridge_send)
            except StopAsyncIteration:
                break
            bridge_send = yield turn
        summary = bridge_summary
        artifacts = [path for path in artifact_out if path]

    success_args = {"outcome": summary, "artifacts": artifacts}
    _ = yield _ask_advisor_turn("submit_generator_success", success_args)
    _ = yield Turn(calls=(ToolCall("submit_generator_success", success_args),))


def scenario_script_for(
    scenario: "Scenario",
    agent_def: "AgentDefinition",
    context: "QueryContext",
    *,
    audit_recorder: Any | None = None,
    bus: Any | None = None,
    repo_dir: str = "",
) -> TurnScript:
    """Return the profile-appropriate :class:`TurnScript` for *agent_def*.

    Dispatch is by profile ``name`` (not ``role``): the executor is a generator
    by role; the reducer scripts its own gating behavior.
    """
    role = agent_def.name
    # Helper sub-agents (advisor) carry no TaskCenter attempt context — script
    # them before touching ``tool_metadata``.
    if role == "advisor":
        return _advisor_script()
    ctx = build_scenario_context(
        scenario,
        context.tool_metadata,
        prompt="",
        audit_recorder=audit_recorder,
    )
    if role == "planner":
        return _planner_script(scenario, ctx)
    if role == "executor":
        probe_ctx = ProbeContext(
            metadata=context.tool_metadata, repo_dir=repo_dir, bus=bus
        )
        return _executor_script(scenario, ctx, probe_ctx)
    if role == "reducer":
        return _reducer_script(scenario, ctx)
    raise RuntimeError(f"Unsupported mock agent role: {role!r}")


__all__ = [
    "build_scenario_context",
    "normalize_result",
    "scenario_script_for",
]
