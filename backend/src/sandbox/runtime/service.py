"""Per-sandbox runtime service facade.

The facade delegates every public op to a backend selected at construction
time. Sandboxes with a registered provider adapter use :class:`DaemonBackend`;
sandboxless/local flows keep using :class:`InProcessBackend`.

After the OCC simplification this surface is intentionally minimal:
mutation requests flow through typed OCC services, not through service-level
write/edit methods or runtime OCC wire handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from sandbox.providers.registry import get_adapter
from sandbox.runtime.backends import (
    CodeIntelligenceBackend,
    DaemonBackend,
    InProcessBackend,
)

__all__ = ["CodeIntelligenceService"]

logger = logging.getLogger(__name__)


def _select_backend(
    sandbox_id: str,
    workspace_root: str,
    sandbox: Any,
    *,
    direct_runtime: bool = False,
) -> CodeIntelligenceBackend:
    """Pick a backend based on provider-adapter availability."""
    if _has_provider_adapter(sandbox_id):
        return DaemonBackend(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
        )
    return InProcessBackend(
        sandbox_id=sandbox_id,
        workspace_root=workspace_root,
        sandbox=sandbox,
        direct_runtime=direct_runtime,
    )


def _has_provider_adapter(sandbox_id: str) -> bool:
    if not sandbox_id:
        return False
    try:
        get_adapter(sandbox_id)
    except KeyError:
        return False
    return True


class CodeIntelligenceService:
    """Thin facade that forwards every public op to the selected backend."""

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
        *,
        direct_runtime: bool = False,
    ) -> None:
        self._impl: CodeIntelligenceBackend = _select_backend(
            sandbox_id,
            workspace_root,
            sandbox,
            direct_runtime=direct_runtime,
        )

    @property
    def sandbox_id(self) -> str:
        return self._impl.sandbox_id

    @property
    def workspace_root(self) -> str:
        return self._impl.workspace_root

    @property
    def is_initialized(self) -> bool:
        return self._impl.is_initialized

    @property
    def _command_executor(self) -> Any:
        return self._impl._command_executor  # type: ignore[attr-defined]

    def ensure_initialized(self, wait: bool = True) -> bool:
        return self._impl.ensure_initialized(wait=wait)

    def warmup(self) -> None:
        self._impl.warmup()

    def rebind_sandbox(self, sandbox: Any) -> None:
        self._impl.rebind_sandbox(sandbox)

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        return await self._impl.cmd(sandbox, command, **kwargs)

    def dispose(self) -> None:
        self._impl.dispose()
