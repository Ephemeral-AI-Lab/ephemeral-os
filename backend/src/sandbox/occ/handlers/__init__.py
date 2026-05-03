"""OCC runtime operation registration."""

from __future__ import annotations

from . import apply_changeset


_HANDLERS = {
    "occ.apply_changeset": apply_changeset.handle,
}


def register_handlers() -> None:
    # Imported lazily to avoid the bootstrap-time circular import:
    # ``sandbox.occ.handlers`` is loaded from ``sandbox.runtime.server``'s
    # ``_load_peer_bootstraps``, so importing ``server`` at module top
    # would re-enter ``handlers`` while it is still partially initialized.
    from sandbox.runtime import server

    for op, handler in _HANDLERS.items():
        existing = server.OP_TABLE.get(op)
        if existing is not None:
            if str(getattr(existing, "__module__", "")).startswith(
                "sandbox.occ.handlers."
            ):
                continue
            raise ValueError(f"runtime op already registered: {op}")
        server.register_op(op, handler)


__all__ = ["register_handlers"]
