"""OCC runtime operation registration."""

from __future__ import annotations

from sandbox.runtime import server

from . import apply_changeset, commit, edit, write


_HANDLERS = {
    "occ.apply": edit.handle_apply,
    "occ.apply_changeset": apply_changeset.handle,
    "occ.commit": commit.handle,
    "occ.edit": edit.handle,
    "occ.write": write.handle,
}


def register_handlers() -> None:
    for op, handler in _HANDLERS.items():
        existing = server.OP_TABLE.get(op)
        if existing is not None:
            if str(getattr(existing, "__module__", "")).startswith(
                "sandbox.occ.handlers."
            ):
                continue
            raise ValueError(f"runtime op already registered: {op}")
        server.register_op(op, handler)


register_handlers()


__all__ = ["register_handlers"]
