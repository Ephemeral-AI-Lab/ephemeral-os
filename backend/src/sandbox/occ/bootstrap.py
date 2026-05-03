"""OCC runtime bootstrap registration."""

from __future__ import annotations

from sandbox.runtime import setup_orchestrator
from sandbox.runtime.setup_orchestrator import SetupScript

from sandbox.occ import handlers as _handlers

_SETUP = SetupScript(
    name="occ",
    package="sandbox.occ",
    relative_path="sandbox/occ/setup.sh",
)


def register() -> None:
    setup_orchestrator.register(_SETUP)
    _handlers.register_handlers()


register()


__all__ = ["register"]
