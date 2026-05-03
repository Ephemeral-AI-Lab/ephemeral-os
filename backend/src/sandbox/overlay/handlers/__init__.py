"""Overlay runtime operation registration."""

from __future__ import annotations

from sandbox.runtime import server

from . import run, shell

_HANDLERS = {
    "overlay.run": run.handle,
    "shell": shell.handle,
}


def register_handlers() -> None:
    for op, handler in _HANDLERS.items():
        existing = server.OP_TABLE.get(op)
        if existing is not None:
            if str(getattr(existing, "__module__", "")).startswith(
                "sandbox.overlay.handlers."
            ):
                continue
            raise ValueError(f"runtime op already registered: {op}")
        server.register_op(op, handler)


__all__ = ["register_handlers"]
