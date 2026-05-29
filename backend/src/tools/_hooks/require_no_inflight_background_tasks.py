"""Pre-hook that rejects a tool call while the calling agent has in-flight
sandbox-bound background tasks.

Reused on ``enter_isolated_workspace``, ``exit_isolated_workspace`` and the
nine main-role terminals (wired *before* ``AdvisorApprovalPreHook`` so the
background rejection is the one surfaced). "In-flight" means *running,
sandbox-bound* background tasks for this agent — the same definition as
``BackgroundTaskSupervisor.count_by_agent`` and the daemon's
``inflight_count``; subagent / non-sandbox background work is not counted.

The decision uses ``max(local, daemon)`` like
``sandbox.host.isolated_workspace_lifecycle``: confirmed in-flight blocks. On
a daemon error (count indeterminate, local zero) failure/blocker terminals
fail OPEN so a flaky daemon can never trap the agent's bail-out path; every
other gated tool fails safe.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.hooks import HookResult
from tools._hooks._context import resolve_agent_id, resolve_sandbox_id


logger = logging.getLogger(__name__)

# Failure/blocker terminals exempt from the daemon-error fail-safe-block so a
# flaky daemon never hard-locks the agent's bail-out path (plan D7). Scoped to
# the daemon-error branch only — confirmed in-flight still blocks these too.
_BAILOUT_TOOLS = frozenset(
    {
        "submit_execution_blocker",
        "submit_evaluation_failure",
        "submit_verification_failure",
        "submit_plan_defers_goal",
    }
)

_MSG_IN_FLIGHT = (
    "BLOCKED: {count} sandbox-bound background task(s) are still in flight for "
    "this agent. Cancel them with cancel_background_task before calling "
    "{tool}, then retry."
)
_MSG_UNAVAILABLE = (
    "BLOCKED: could not confirm background-task state from the sandbox daemon, "
    "so {tool} is refused to avoid orphaning in-flight work. Retry shortly."
)


class RequireNoInflightBackgroundTasks:
    """Per-tool hook: reject when the calling agent has in-flight bg tasks.

    Instance-per-target so ``target_tool`` matches the decorator's ``name``;
    ``validate_hook_targets`` reads ``target_tool`` via ``getattr``.
    """

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"no_bg_tasks:{target_tool}"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[BaseModel]:
        agent_id = resolve_agent_id(context)

        local = self._local_count(context, agent_id)
        if local > 0:
            return self._fail_in_flight(local)

        sandbox_id = resolve_sandbox_id(context)
        if not sandbox_id:
            # No sandbox → no sandbox-bound background tasks possible.
            return HookResult.pass_(tool_input)

        # Lazy import: ``sandbox.api`` at module scope re-triggers the
        # isolated-workspace import cycle documented in
        # ``sandbox.host.isolated_workspace_lifecycle``.
        import sandbox.api as sandbox_api

        try:
            daemon = await sandbox_api.inflight_count(sandbox_id, agent_id)
        except Exception as exc:  # noqa: BLE001 - any daemon RPC failure
            return self._fail_or_bailout(tool_input, exc)
        if daemon > 0:
            return self._fail_in_flight(daemon)
        return HookResult.pass_(tool_input)

    @staticmethod
    def _local_count(context: ToolExecutionContextService, agent_id: str) -> int:
        manager = context.get("background_task_manager")
        counter = getattr(manager, "count_by_agent", None)
        if not callable(counter):
            return 0
        return int(counter(agent_id))

    def _fail_in_flight(self, count: int) -> HookResult[BaseModel]:
        return HookResult.fail(
            _MSG_IN_FLIGHT.format(count=count, tool=self.target_tool),
            metadata={
                "policy": "no_inflight_background_tasks",
                "reason": "ephemeral_jobs_in_flight",
                "count": count,
            },
        )

    def _fail_or_bailout(
        self, tool_input: BaseModel, exc: Exception
    ) -> HookResult[BaseModel]:
        if self.target_tool in _BAILOUT_TOOLS:
            logger.warning(
                "no_bg_tasks gate fail-open on %s: daemon in-flight count "
                "unavailable (%s)",
                self.target_tool,
                exc,
            )
            return HookResult.pass_(
                tool_input,
                metadata={
                    "policy": "no_inflight_background_tasks",
                    "reason": "daemon_unavailable_bailout",
                },
            )
        return HookResult.fail(
            _MSG_UNAVAILABLE.format(tool=self.target_tool),
            metadata={
                "policy": "no_inflight_background_tasks",
                "reason": "inflight_count_unavailable",
            },
        )


__all__ = ["RequireNoInflightBackgroundTasks"]
