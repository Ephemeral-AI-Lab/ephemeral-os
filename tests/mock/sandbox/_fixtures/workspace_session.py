"""Test-only workspace session helper.

This module is not part of the production sandbox API. Production callers use
the explicit ``sandbox.host.iws_lifecycle.enter_isolated_workspace`` /
``sandbox.host.iws_lifecycle.exit_isolated_workspace`` pair.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from sandbox._shared.models import (
    EnterIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceRequest,
    SandboxCaller,
)
from sandbox.host.iws_lifecycle import (
    enter_isolated_workspace,
    exit_isolated_workspace,
)


@dataclass(frozen=True)
class WorkspaceSession:
    agent_id: str
    mode: str

    @classmethod
    @asynccontextmanager
    async def enter_isolated(
        cls,
        *,
        agent_id: str,
        layer_stack_root: str,
    ) -> AsyncIterator["WorkspaceSession"]:
        caller = SandboxCaller(agent_id=agent_id)
        await enter_isolated_workspace(
            EnterIsolatedWorkspaceRequest(
                caller=caller,
                layer_stack_root=layer_stack_root,
            )
        )
        try:
            yield cls(agent_id=agent_id, mode="isolated")
        finally:
            await exit_isolated_workspace(ExitIsolatedWorkspaceRequest(caller=caller))

    @classmethod
    @asynccontextmanager
    async def ephemeral(cls, *, agent_id: str) -> AsyncIterator["WorkspaceSession"]:
        yield cls(agent_id=agent_id, mode="ephemeral")


__all__ = ["WorkspaceSession"]
