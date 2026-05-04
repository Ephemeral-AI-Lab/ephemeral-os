"""Legacy overlay capture runtime bootstrap registration."""

from __future__ import annotations

from sandbox.overlay.handlers import run, shell
from sandbox.runtime import server
from sandbox.runtime import setup_orchestrator
from sandbox.runtime.setup_orchestrator import SetupScript

_SETUP = SetupScript(
    name="overlay_capture",
    package="sandbox.runtime.overlay_capture",
    relative_path="sandbox/runtime/overlay_capture/setup.sh",
)


def register() -> None:
    setup_orchestrator.register(_SETUP)
    _register_handlers()


def _register_handlers() -> None:
    for op, handler in {
        "overlay.run": run.handle,
        "shell": shell.handle,
    }.items():
        existing = server.OP_TABLE.get(op)
        if existing is not None:
            if str(getattr(existing, "__module__", "")).startswith(
                "sandbox.overlay.handlers."
            ):
                continue
            raise ValueError(f"runtime op already registered: {op}")
        server.register_op(op, handler)


register()


__all__ = ["register"]
