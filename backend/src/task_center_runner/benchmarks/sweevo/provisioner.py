"""``SweevoProvisioner`` — wraps :func:`benchmarks.sweevo.sandbox.setup_sweevo_sandbox`.

Daytona sandbox creation happens externally (e.g. in
``benchmarks.sweevo.__main__``); this provisioner takes the
externally-created ``sandbox_id``, runs ``setup_sweevo_sandbox`` to seed
the repo at the base commit, and leaves release as a no-op — the caller
owns the sandbox lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from benchmarks.sweevo.models import SWEEvoInstance
from benchmarks.sweevo.sandbox import setup_sweevo_sandbox
from task_center_runner.core.sandbox import SandboxLease

if TYPE_CHECKING:
    from task_center_runner.core.config import RunContext


class SweevoProvisioner:
    """``SandboxProvisioner`` that seeds an existing sandbox for SWE-EVO."""

    def __init__(
        self,
        instance: SWEEvoInstance,
        sandbox_id: str,
        *,
        repo_dir: str,
        install_lsp: bool = False,
    ) -> None:
        self._instance = instance
        self._sandbox_id = sandbox_id
        self._repo_dir = repo_dir
        self._install_lsp = install_lsp

    async def provision(self, ctx: "RunContext") -> SandboxLease:
        await setup_sweevo_sandbox(
            self._instance,
            self._sandbox_id,
            repo_dir=self._repo_dir,
            install_lsp=self._install_lsp,
        )
        return SandboxLease(
            sandbox_id=self._sandbox_id,
            metadata={
                "instance_id": self._instance.instance_id,
                "repo_dir": self._repo_dir,
            },
        )

    async def release(self, lease: SandboxLease) -> None:
        # Caller owns the Daytona lifecycle; do not destroy the sandbox here.
        return None


__all__ = ["SweevoProvisioner"]
