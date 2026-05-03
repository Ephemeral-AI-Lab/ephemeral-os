"""Overlay runtime bootstrap registration."""

from __future__ import annotations

from sandbox.overlay import handlers as _handlers
from sandbox.runtime import setup_orchestrator
from sandbox.runtime.setup_orchestrator import SetupScript

_SETUP = SetupScript(
    name="overlay",
    package="sandbox.overlay",
    relative_path="sandbox/overlay/setup.sh",
)


def register() -> None:
    setup_orchestrator.register(_SETUP)
    _handlers.register_handlers()


register()


__all__ = ["register"]
