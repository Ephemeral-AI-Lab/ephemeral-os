"""Context objects passed to in-sandbox plugin op handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from sandbox._shared.models import Intent, SandboxCaller
from sandbox.ephemeral_workspace.events import WorkspaceChangeEvent
from sandbox.overlay.handle import OverlayHandle

__all__ = [
    "PluginOpContext",
    "EphemeralPipelineLike",
    "WorkspaceChangeEvent",
    "WorkspaceProjectionLike",
]


class WorkspaceProjectionLike(Protocol):
    @property
    def layer_stack_root(self) -> Any: ...

    def acquire(self, owner_request_id: str) -> Any: ...

    def acquire_overlay(
        self,
        owner_request_id: str,
        *,
        workspace_root: str,
    ) -> OverlayHandle: ...

    def active_manifest_key(self) -> str: ...


class EphemeralPipelineLike(Protocol):
    @property
    def workspace_root(self) -> str: ...

    def active_manifest_key(self) -> str: ...

    async def ensure_current(self, *, reason: str = "ensure_current") -> str: ...

    def current_manifest(self) -> Any: ...

    def acquire_operation_overlay(
        self,
        *,
        invocation_id: str,
        workspace_root: str | None = None,
    ) -> OverlayHandle: ...

    def subscribe_workspace_changes(
        self, subscriber_id: str
    ) -> asyncio.Queue[WorkspaceChangeEvent]: ...

    def unsubscribe_workspace_changes(self, subscriber_id: str) -> None: ...

    def workspace_operation(
        self,
        *,
        reason: str = "operation",
    ) -> AbstractAsyncContextManager[Any]: ...

    async def publish_workspace_paths(
        self,
        *,
        paths: list[str] | tuple[str, ...],
        agent_id: str = "",
        description: str = "plugin workspace edit",
    ) -> object: ...

    async def publish_cycle(
        self,
        *,
        request: Any,
        upperdir: str,
        snapshot: Any,
        run_maintenance: bool = True,
    ) -> object: ...


@dataclass(frozen=True)
class PluginOpContext:
    """Plugin handler context.

    READ_ONLY handlers must query a PluginService instead of doing direct
    filesystem I/O.
    """

    layer_stack_root: str
    caller: SandboxCaller
    projection: WorkspaceProjectionLike
    overlay: EphemeralPipelineLike
    intent: Intent = Intent.READ_ONLY
    metadata: dict[str, Any] = field(default_factory=dict)
