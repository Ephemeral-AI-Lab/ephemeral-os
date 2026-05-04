"""Legacy overlay capture runtime bootstrap registration."""

from __future__ import annotations

from sandbox.overlay import handlers as _handlers
from sandbox.runtime import setup_orchestrator
from sandbox.runtime.setup_orchestrator import SetupScript

_SETUP = SetupScript(
    name="overlay_capture",
    package="sandbox.runtime.overlay_capture",
    relative_path="sandbox/runtime/overlay_capture/setup.sh",
)


def register() -> None:
    setup_orchestrator.register(_SETUP)
    _handlers.register_handlers()


register()


__all__ = ["register"]
