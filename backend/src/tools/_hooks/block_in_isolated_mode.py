"""Pre-hook that blocks ``ask_advisor`` while the calling agent is inside an
active isolated workspace.

Isolated-state is read from the daemon (single source of truth) via the thin
``sandbox.api.isolated_active`` wrapper over the existing
``api.isolated_workspace.status`` op — the same ``get_handle`` verdict the
daemon plugin gate uses, never an engine-local mirror flag.

Failure mode is fail-OPEN: ``ask_advisor`` is read-only and a stuck agent is
worse than a rare missed block, so a daemon error logs and passes.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.hooks import HookResult
from tools._hooks._context import resolve_agent_id, resolve_sandbox_id


logger = logging.getLogger(__name__)

_MSG_BLOCKED = (
    "BLOCKED: ask_advisor is unavailable inside an isolated workspace; call "
    "exit_isolated_workspace first, then ask_advisor and submit your terminal."
)


class BlockInIsolatedMode:
    """Per-tool hook: reject when the calling agent is in isolated mode.

    Instance-per-target so ``target_tool`` matches the decorator's ``name``.
    """

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"block_in_isolated_mode:{target_tool}"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[BaseModel]:
        sandbox_id = resolve_sandbox_id(context)
        if not sandbox_id:
            # No sandbox → cannot be in an isolated workspace.
            return HookResult.pass_(tool_input)
        agent_id = resolve_agent_id(context)

        # Lazy import: ``sandbox.api`` at module scope re-triggers the
        # isolated-workspace import cycle documented in
        # ``sandbox.host.isolated_workspace_lifecycle``.
        import sandbox.api as sandbox_api

        try:
            active = await sandbox_api.isolated_active(sandbox_id, agent_id)
        except Exception as exc:  # noqa: BLE001 - any daemon RPC failure
            logger.warning(
                "block_in_isolated_mode fail-open on %s: isolated status "
                "unavailable (%s)",
                self.target_tool,
                exc,
            )
            return HookResult.pass_(tool_input)
        if active:
            return HookResult.fail(
                _MSG_BLOCKED,
                metadata={
                    "policy": "block_in_isolated_mode",
                    "reason": "isolated_workspace_open",
                },
            )
        return HookResult.pass_(tool_input)


__all__ = ["BlockInIsolatedMode"]
