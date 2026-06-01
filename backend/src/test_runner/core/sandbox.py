"""SandboxProvisioner Protocol + lease container + ``AttachExisting`` helper.

Per the plan's locked decision #3 the default ``release()`` semantics are
"destroy the sandbox best-effort". ``AttachExisting(sandbox_id)`` overrides
``release()`` to a no-op so a pre-provisioned sandbox (e.g. a pytest fixture)
survives the run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from test_runner.core.config import RunContext


@dataclass(frozen=True, slots=True)
class SandboxLease:
    """A handle to a provisioned sandbox: an id plus opaque metadata."""

    sandbox_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SandboxProvisioner(Protocol):
    """Provision and release a sandbox for the lifetime of a single run."""

    async def provision(self, ctx: "RunContext") -> SandboxLease: ...

    async def release(self, lease: SandboxLease) -> None: ...


class AttachExisting:
    """Attach to a pre-existing sandbox; ``release()`` is a no-op.

    Used by tests that pre-create a sandbox via a pytest fixture and need
    the run to leave the sandbox alive for inspection.
    """

    def __init__(self, sandbox_id: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        self._lease = SandboxLease(
            sandbox_id=sandbox_id,
            metadata=metadata if metadata is not None else {},
        )

    async def provision(self, ctx: "RunContext") -> SandboxLease:
        return self._lease

    async def release(self, lease: SandboxLease) -> None:
        return None


__all__ = ["AttachExisting", "SandboxLease", "SandboxProvisioner"]
