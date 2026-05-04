"""In-process CodeIntelligenceService backend."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sandbox.runtime.shell_command_executor import AuditedCommandExecutor

__all__ = ["InProcessBackend"]

logger = logging.getLogger(__name__)


class InProcessBackend:
    """In-process backend for local and sandboxless flows.

    Holds the per-sandbox shell executor and sandbox handle. The backend owns
    no write/edit mutation methods.
    """

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
        *,
        direct_runtime: bool = False,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._sandbox = sandbox
        self._initialized = False
        self._init_lock = threading.Lock()
        self._command_executor = AuditedCommandExecutor(
            sandbox_id=sandbox_id,
            workspace_root=workspace_root,
            rebind_sandbox=self.rebind_sandbox,
            direct_runtime=direct_runtime,
        )

    def ensure_initialized(self, wait: bool = True) -> bool:
        del wait
        with self._init_lock:
            if self._initialized:
                return True

        with self._init_lock:
            self._initialized = True
        return self.is_initialized

    @property
    def is_initialized(self) -> bool:
        with self._init_lock:
            return self._initialized

    def warmup(self) -> None:
        if self.is_initialized:
            return
        try:
            self.ensure_initialized(wait=True)
        except Exception:
            logger.debug("warmup full init failed", exc_info=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        if sandbox is None:
            return
        self._sandbox = sandbox

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        return await self._command_executor.cmd(sandbox, command, **kwargs)

    def dispose(self) -> None:
        logger.info("CodeIntelligenceService disposed for sandbox %s", self.sandbox_id)
